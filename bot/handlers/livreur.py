"""Côté livreur : service, prise de course (verrou), livraison, enchaînement, annulation (§8–§11.1)."""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common, relay
from bot.services import broadcast, catalog, lifecycle, order_edit, sales
from bot.timeutil import iso, now_utc, parse_ts

log = logging.getLogger(__name__)

# Validation en texte de la course en cours : « OK » (espèces ; « OK CB » / « OK virement »), ou « Modif ».
OK_RE = re.compile(r"^\s*(?:ok+|okay|oké|okey|valid[ée]e?|livr[ée]e?)(?![\w])(?P<rest>.*)$", re.I | re.S)
MODIF_RE = re.compile(r"^\s*modif(?:ier|ication|i[ée]e?|s)?(?![\w])", re.I)

# Un livreur qui appuie sur deux propositions à la fois ne doit pas dépasser 1 + 1.
_livreur_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _livreur(update: Update) -> dict | None:
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] != "livreur":
        return None
    return user


# ================================================================ commandes

async def dispo(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="livreur"):
        return
    cfg = config.get()
    pos = await db.get_position(user["id"])
    fresh = pos and parse_ts(pos["updated_at"]) > now_utc() - timedelta(minutes=cfg.position_stale_minutes)
    if fresh:
        # Position en direct toujours reçue : pas besoin de la repartager.
        await db.update_user(user["id"], {"on_duty": True})
        await db.log_event("livreur_on_duty", user_id=user["id"])
        await messaging.reply(update, texts.ON_DUTY, keyboards.transport_mode(user.get("transport_mode")))
        await broadcast.kick_pending(context)
    else:
        await messaging.reply(update, texts.DISPO_PROMPT, keyboards.transport_mode(user.get("transport_mode")))
        # Pas de position dans DISPO_REMINDER_SECONDS : on relance le livreur et on prévient le dispatch.
        await db.log_event("dispo_asked", user_id=user["id"])
        if context.job_queue is not None:
            name = f"dispo:{user['id']}"
            for job in context.job_queue.get_jobs_by_name(name):
                job.schedule_removal()
            context.job_queue.run_once(dispo_reminder, when=DISPO_REMINDER_SECONDS, name=name,
                                       data={"user_id": user["id"], "asked_at": iso(now_utc())})


DISPO_REMINDER_SECONDS = 180


async def dispo_reminder(context) -> None:
    """3 min après un /dispo : toujours pas de position → marche à suivre au livreur, dispatch prévenu."""
    data = context.job.data
    user = await db.get_user(data["user_id"])
    if user is None or user["status"] != "active" or user.get("on_duty"):
        return
    pos = await db.get_position(user["id"])
    if pos and parse_ts(pos["updated_at"]) >= parse_ts(data["asked_at"]):
        return
    await messaging.send(context.bot, user, texts.DISPO_REMINDER)
    await messaging.notify_dispatch(context.bot, texts.d_dispo_without_position(user, DISPO_REMINDER_SECONDS // 60))
    await db.log_event("dispo_no_position", user_id=user["id"])


@common.callback
async def transport_mode(update: Update, context):
    """tmode:t|d|v — 🚶 transport, 🛵 deux-roues, 🚗 voiture (retenu jusqu'au prochain changement)."""
    from bot.services import transport

    user = await _livreur(update)
    if user is None:
        return None
    mode = transport.MODES.get(common.arg(update))
    if mode is None:
        return None
    if user.get("transport_mode") != mode:
        await db.update_user(user["id"], {"transport_mode": mode})
        await db.log_event("transport_mode", user_id=user["id"], payload={"mode": mode})
    await messaging.edit_markup(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                                keyboards.transport_mode(mode))
    return texts.transport_set(mode)


async def pause(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="livreur"):
        return
    await db.update_user(user["id"], {"on_duty": False, "soon_free": False, "duty_forced": False})
    await db.log_event("livreur_pause", user_id=user["id"])
    await messaging.reply(update, texts.PAUSED)


async def ma_course(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="livreur"):
        return
    courses = await db.list_assigned_for_livreur(user["id"])
    if not courses:
        await messaging.reply(update, texts.NO_ASSIGNED)
        return
    franchises = await db.get_users(c["franchise_id"] for c in courses)
    for c in courses:
        sent = await messaging.reply(update, texts.full_fiche(c, franchises.get(c["franchise_id"], {})),
                                     keyboards.livreur_course(c["id"]))
        if sent:
            await db.update_course(c["id"], {"livreur_message_id": sent.message_id})


async def on_message(update: Update, context, user: dict, state: str | None, payload: dict) -> None:
    msg = update.message
    if state == "relaying":
        if msg.text:
            await relay.relay_text(update, context, user, payload.get("course_id"), msg.text)
        else:
            await messaging.reply(update, texts.WRITE_TEXT)
        return
    if msg.text and msg.reply_to_message:
        course_id = await relay.course_for_reply(user, msg.reply_to_message.message_id)
        if course_id:
            await relay.relay_text(update, context, user, course_id, msg.text)
            return
    if msg.text and (_ok_payment(msg.text) or MODIF_RE.match(msg.text)):
        await validate_text(update, context, user, msg.text)
        return
    await messaging.reply(update, texts.LIVREUR_TEXT_HINT)


def _ok_payment(text: str) -> str | None:
    """« OK » seul → espèces ; « OK CB », « ok virement » → virement. Autre chose (« ok merci ») → None :
    seul un « OK » net valide une course."""
    m = OK_RE.match(text or "")
    if not m:
        return None
    rest = m["rest"].strip(" .!")
    if not rest:
        return "especes"
    payment, left = sales.split_payment(rest)
    return payment if payment and not left.strip(" .!") else None


async def current_course(user: dict) -> dict | None:
    """La course en cours du livreur : la plus ancienne qu'il a (les suivantes attendent qu'il la valide)."""
    assigned = await db.list_assigned_for_livreur(user["id"])
    return min(assigned, key=lambda c: (c.get("assigned_at") or "", c["id"])) if assigned else None


async def validate_text(update: Update, context, user: dict, text: str) -> None:
    """« OK » : la course en cours est livrée (espèces, ou virement avec « OK CB »).
    « Modif » : l'éditeur s'ouvre ; la modification part au franchisé, puis le paiement valide la livraison."""
    async with _livreur_locks[user["id"]]:
        course = await current_course(user)
        if course is None:
            await messaging.reply(update, texts.NO_ASSIGNED)
            return
        payment = _ok_payment(text)
        if payment:
            updated = await lifecycle.deliver(context, course, payment=payment, ack=True)
            if updated is None:
                await messaging.reply(update, texts.COURSE_FINISHED)
            return
        cat = await catalog.load()
        lines = order_edit.lines_from_course(course, cat)
        sent = await messaging.reply(update, texts.order_editor(course["id"], lines, None),
                                     keyboards.order_editor(lines, None))
        await _save_edit(user, {"course_id": course["id"], "lines": lines, "orig": lines, "sel": None,
                                "msg": sent.message_id if sent else None, "deliver": True})


# ================================================================ prise de course

@common.callback
async def take(update: Update, context):
    user = await _livreur(update)
    if user is None:
        return None
    course_id = common.arg(update, int)
    message = update.callback_query.message
    async with _livreur_locks[user["id"]]:
        user = await db.get_user(user["id"])
        course = await db.get_course(course_id)
        if course is None:
            return None
        if course["status"] != "pending":
            if course.get("livreur_id") == user["id"]:
                return "Elle est déjà à toi."
            text = (texts.proposal_withdrawn(course_id) if course["status"] in db.CANCELLED_STATUSES
                    else texts.proposal_taken(course_id))
            await messaging.edit(context.bot, update.effective_chat.id, message.message_id, text)
            return texts.TOO_LATE, True
        assigned = await db.list_assigned_for_livreur(user["id"])
        if len(assigned) >= 2 or (len(assigned) == 1 and not user.get("soon_free")):
            return texts.ALREADY_HAS_COURSE, True
        won = await db.take_course(course_id, user["id"])
        if won is None:
            await messaging.edit(context.bot, update.effective_chat.id, message.message_id,
                                 texts.proposal_taken(course_id))
            return texts.TOO_LATE, True
    await lifecycle.after_assignment(context, won, user, message)
    return "C'est pour toi 🚴"


@common.callback
async def deliver(update: Update, context):
    user = await _livreur(update)
    if user is None:
        return None
    course = await db.get_course(common.arg(update, int))
    if course is None or course.get("livreur_id") != user["id"]:
        return texts.COURSE_FINISHED, True
    if course["status"] == "delivered":
        return "Déjà livrée."
    if course["status"] != "assigned":
        return texts.COURSE_FINISHED, True
    current = await current_course(user)
    if current and current["id"] != course["id"]:
        return texts.finish_first(current["id"]), True
    # Le client a payé comment ? Le choix valide la livraison.
    await messaging.edit_markup(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                                keyboards.payment_choice(course["id"]))
    return texts.PAYMENT_PROMPT


@common.callback
async def pay(update: Update, context):
    """pay:<course>:e|v (espèces / virement) → livraison ; pay_back:<course> → retour à la fiche."""
    user = await _livreur(update)
    if user is None:
        return None
    parts = (update.callback_query.data or "").split(":")
    course = await db.get_course(int(parts[1]))
    if course is None or course.get("livreur_id") != user["id"]:
        return texts.COURSE_FINISHED, True
    if course["status"] == "delivered":
        return "Déjà livrée."
    if course["status"] != "assigned":
        return texts.COURSE_FINISHED, True
    message_id = update.callback_query.message.message_id
    current = await current_course(user)
    if parts[0] != "pay_back" and current and current["id"] != course["id"]:
        return texts.finish_first(current["id"]), True
    if parts[0] == "pay_back":
        await messaging.edit_markup(context.bot, update.effective_chat.id, message_id,
                                    keyboards.livreur_course(course["id"]))
        return None
    payment = lifecycle.PAYMENTS.get(parts[2] if len(parts) > 2 else "")
    if payment is None:
        return None
    course["livreur_message_id"] = message_id
    updated = await lifecycle.deliver(context, course, payment=payment)
    if updated is None:
        return "Déjà livrée."
    return "Livrée ✅"


@common.callback
async def soon_free(update: Update, context):
    user = await _livreur(update)
    if user is None:
        return None
    assigned = await db.list_assigned_for_livreur(user["id"])
    if not assigned:
        return texts.SOON_FREE_NO_COURSE, True
    if len(assigned) >= 2:
        return texts.SOON_FREE_ALREADY, True
    if not user.get("soon_free"):
        await db.update_user(user["id"], {"soon_free": True})
        await db.log_event("soon_free", assigned[0]["id"], user["id"], {"auto": False})
    await messaging.reply(update, texts.SOON_FREE_ACK)
    await broadcast.kick_pending(context)
    return None


# ================================================================ annulation par le livreur

async def _own_assigned(update: Update) -> tuple[dict | None, dict | None]:
    user = await _livreur(update)
    if user is None:
        return None, None
    course = await db.get_course(common.arg(update, int))
    if course is None or course.get("livreur_id") != user["id"] or course["status"] != "assigned":
        return user, None
    return user, course


@common.callback
async def cancel_ask(update: Update, context):
    user, course = await _own_assigned(update)
    if course is None:
        return (texts.COURSE_FINISHED, True) if user else None
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         f"{texts.LIVREUR_CANCEL_CONFIRM}\n\nCourse #{course['id']} — {texts.esc(course['district'])}",
                         keyboards.livreur_cancel_confirm(course["id"]))
    return None


@common.callback
async def cancel_no(update: Update, context):
    user, course = await _own_assigned(update)
    if course is None:
        return (texts.COURSE_FINISHED, True) if user else None
    franchise = await db.get_user(course["franchise_id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.full_fiche(course, franchise or {}), keyboards.livreur_course(course["id"]))
    return None


@common.callback
async def cancel_yes(update: Update, context):
    user, course = await _own_assigned(update)
    if course is None:
        return (texts.COURSE_FINISHED, True) if user else None
    course["livreur_message_id"] = update.callback_query.message.message_id
    updated = await lifecycle.release(context, course, reason="livreur_cancel")
    if updated is None:
        return texts.COURSE_FINISHED, True
    return "Course annulée"


# ================================================================ modification de la commande (sur place)
#
# Ouverte au livreur de la course (course en cours) et aux admins — dispatch et
# franchisés — tant que la course n'est pas livrée (en attente ou en cours).

EDIT_STATE = "editing_order"
EDIT_MINUTES = 30


def _edit_mode(user: dict | None, course: dict | None) -> str | None:
    """« livreur », « admin » ou None si l'utilisateur ne peut pas modifier cette course."""
    if not user or not course or user.get("status") != "active":
        return None
    if course.get("livreur_id") == user["id"] and course["status"] == "assigned":
        return "livreur"
    if common.is_admin(user) and course["status"] in ("pending", "assigned"):
        return "admin"
    return None


async def _save_edit(user: dict, payload: dict) -> None:
    await db.set_state(user["id"], EDIT_STATE, payload, now_utc() + timedelta(minutes=EDIT_MINUTES))


async def _render_edit(context, chat_id: int, payload: dict, picker_page: int | None = None) -> None:
    lines, sel = payload["lines"], payload.get("sel")
    if picker_page is None:
        text, markup = texts.order_editor(payload["course_id"], lines, sel), keyboards.order_editor(lines, sel)
    else:
        products = await db.list_products()
        pages = max(1, -(-len(products) // order_edit.PAGE_SIZE))
        text = texts.order_picker(payload["course_id"], picker_page, pages)
        markup = keyboards.order_picker(products, picker_page, order_edit.PAGE_SIZE)
    await messaging.edit(context.bot, chat_id, payload.get("msg"), text, markup)


async def _back_to_card(context, chat_id: int, message_id: int | None, course: dict, user: dict) -> None:
    """Remet le message tel qu'il était avant l'éditeur : fiche du livreur, ou message
    de course du franchisé."""
    if course.get("livreur_id") == user["id"]:
        franchise = await db.get_user(course["franchise_id"])
        await messaging.edit(context.bot, chat_id, message_id, texts.full_fiche(course, franchise or {}),
                             keyboards.livreur_course(course["id"]))
        return
    livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
    text, markup = lifecycle.franchise_view(course, livreur)
    if course["franchise_id"] != user["id"]:
        markup = None  # boutons du message de course réservés au franchisé de la course
    await messaging.edit(context.bot, chat_id, message_id, text, markup)


async def _edit_context(update: Update):
    """(utilisateur, payload, course) d'une modification en cours et encore permise."""
    user = await common.actor(update)
    if user is None or user["status"] != "active":
        return None, None, None
    state, payload = common.active_state(user)
    if state != EDIT_STATE or not payload.get("course_id"):
        return user, None, None
    course = await db.get_course(payload["course_id"])
    if _edit_mode(user, course) is None:
        await db.clear_state(user["id"])
        return user, None, None
    return user, payload, course


@common.callback
async def edit_start(update: Update, context):
    user = await common.actor(update)
    if user is None or user["status"] != "active":
        return None
    course = await db.get_course(common.arg(update, int))
    if _edit_mode(user, course) is None:
        return texts.COURSE_FINISHED, True
    async with _livreur_locks[user["id"]]:
        cat = await catalog.load()
        lines = order_edit.lines_from_course(course, cat)
        payload = {"course_id": course["id"], "lines": lines, "orig": lines, "sel": None,
                   "msg": update.callback_query.message.message_id}
        await _save_edit(user, payload)
        await _render_edit(context, update.effective_chat.id, payload)
    return None


async def _expired(update: Update, context, user) -> tuple[str, bool] | None:
    if user is None:
        return None
    course = await db.get_course(_course_of_message(update))
    msg_id = update.callback_query.message.message_id
    if _edit_mode(user, course) is not None:
        await _back_to_card(context, update.effective_chat.id, msg_id, course, user)
        return texts.ORDER_EDIT_EXPIRED, True
    await messaging.edit_markup(context.bot, update.effective_chat.id, msg_id, None)
    return texts.COURSE_FINISHED, True


def _course_of_message(update: Update) -> int:
    """Numéro de course lu dans l'en-tête du message (« Course #142 »), faute d'état."""
    import re

    m = re.search(r"#(\d+)", update.callback_query.message.text or "")
    return int(m.group(1)) if m else 0


@common.callback
async def edit_action(update: Update, context):
    """Tous les boutons de l'éditeur : oe_q, oe_s, oe_p, oe_add, oe_pick, oe_ok, oe_x."""
    user = await common.actor(update)
    if user is None or user["status"] != "active":
        return None
    data = update.callback_query.data or ""
    action, _, rest = data.partition(":")
    chat_id = update.effective_chat.id
    async with _livreur_locks[user["id"]]:
        user, payload, course = await _edit_context(update)
        if payload is None:
            return await _expired(update, context, user)
        payload["msg"] = update.callback_query.message.message_id
        lines = payload["lines"]

        if action == "oe_x":
            await db.clear_state(user["id"])
            await _back_to_card(context, chat_id, payload["msg"], course, user)
            return texts.CANCELLED_OP

        if action == "oe_ok":
            return await _edit_validate(context, chat_id, user, payload, course)

        if action == "oe_add":
            products = await db.list_products()
            if not products:
                return texts.ORDER_EDIT_NO_CATALOG, True
            page = max(0, int(rest or 0))
            await _save_edit(user, {**payload, "sel": None})
            await _render_edit(context, chat_id, payload, picker_page=page)
            return None

        if action == "oe_pick":
            product = await db.get_product(int(rest))
            if product is None:
                return texts.ORDER_EDIT_NO_CATALOG, True
            lines, idx = order_edit.add_product(lines, product["name"])
            if idx < 0:
                return texts.ORDER_EDIT_TOO_MANY, True
            # Nouveau produit sans prix : on ouvre tout de suite le réglage de son prix.
            sel = idx if float(lines[idx]["x"]) <= 0 else None
            payload.update(lines=lines, sel=sel)
        elif action == "oe_q":
            idx, delta = (int(x) for x in rest.split(":"))
            payload.update(lines=order_edit.change_qty(lines, idx, 1 if delta > 0 else -1), sel=None)
        elif action == "oe_s":
            idx = int(rest)
            payload["sel"] = idx if 0 <= idx < len(lines) else None
        elif action == "oe_p":
            idx, delta = rest.split(":")
            payload["lines"] = order_edit.change_price(lines, int(idx), float(delta))
            payload["sel"] = int(idx)
        else:
            return None
        await _save_edit(user, payload)
        await _render_edit(context, chat_id, payload)
    return None


async def _edit_validate(context, chat_id: int, user: dict, payload: dict, course: dict):
    lines = payload["lines"]
    if not lines:
        return texts.ORDER_EDIT_EMPTY, True
    if order_edit.same(lines, payload.get("orig") or []):
        await db.clear_state(user["id"])
        if payload.get("deliver"):              # « Modif » sans rien changer : il reste à valider le paiement
            await messaging.edit(context.bot, chat_id, payload["msg"], texts.PAYMENT_PROMPT,
                                 keyboards.payment_choice(course["id"]))
            return texts.ORDER_EDIT_UNCHANGED
        await _back_to_card(context, chat_id, payload["msg"], course, user)
        return texts.ORDER_EDIT_UNCHANGED
    missing = order_edit.missing_prices(lines)
    if missing:
        return texts.order_edit_missing_price(missing), True
    off = order_edit.off_step_prices(lines)
    if off:
        return texts.order_edit_off_step(off), True
    fields = {"products": order_edit.products_text(lines), "price": order_edit.total(lines)}
    if _edit_mode(user, course) == "livreur":
        return await _edit_request(context, chat_id, user, payload, course, fields)
    updated = await db.update_course_if_status(course["id"], ["pending", "assigned"], fields)
    await db.clear_state(user["id"])
    if updated is None:
        await messaging.edit_markup(context.bot, chat_id, payload["msg"], None)
        return texts.COURSE_FINISHED, True
    await _back_to_card(context, chat_id, payload["msg"], updated, user)
    await db.log_event("course_modified", course["id"], user["id"], {
        "before": {"products": course["products"], "price": float(course["price"])},
        "after": {"products": updated["products"], "price": float(updated["price"])},
    })
    await _notify_modified(context, user, course, updated)
    return "Commande modifiée ✅"


async def _edit_request(context, chat_id: int, user: dict, payload: dict, course: dict, fields: dict):
    """La modification du livreur part au franchisé, qui a le dernier mot (✅ / ❌). La commande ne change
    qu'à sa validation ; une course livrée entre-temps n'est écrite dans la feuille qu'après sa décision."""
    pending = {**fields, "by": user["id"], "at": iso(now_utc())}
    updated = await db.update_course_if_status(course["id"], ["assigned"], {"pending_edit": pending},
                                               livreur_id=user["id"])
    await db.clear_state(user["id"])
    if updated is None:
        await messaging.edit_markup(context.bot, chat_id, payload["msg"], None)
        return texts.COURSE_FINISHED, True
    franchise = await db.get_user(course["franchise_id"]) or {}
    if franchise:
        await messaging.send(context.bot, franchise, texts.edit_request(course, user, pending),
                             keyboards.edit_decision(course["id"]))
    await messaging.notify_dispatch(context.bot, texts.edit_request(course, user, pending)
                                    .replace("Tu valides ? (c'est toi qui as le dernier mot)",
                                             "En attente de la validation du franchisé."))
    await db.log_event("course_edit_requested", course["id"], user["id"], {
        "before": {"products": course["products"], "price": float(course["price"])},
        "after": {"products": pending["products"], "price": float(pending["price"])},
    })
    name = franchise.get("display_name") or "le franchisé"
    if payload.get("deliver"):
        await messaging.edit(context.bot, chat_id, payload["msg"], texts.edit_sent_ask_payment(course, name),
                             keyboards.payment_choice(course["id"]))
    else:
        await _back_to_card(context, chat_id, payload["msg"], course, user)
        await messaging.send(context.bot, user, texts.edit_sent(course["id"], name))
    return "Envoyée au franchisé ✅"


async def _notify_modified(context, editor: dict, before: dict, updated: dict) -> None:
    """Chacun des autres concernés apprend le changement (ancien → nouveau prix)."""
    livreur = await db.get_user(updated["livreur_id"]) if updated.get("livreur_id") else None
    franchise = await db.get_user(updated["franchise_id"])
    if livreur and livreur["id"] != editor["id"] and updated["status"] == "assigned":
        await messaging.send(context.bot, livreur,
                             texts.order_modified_for_livreur(updated, before["price"], editor["display_name"]))
        await messaging.edit(context.bot, livreur["telegram_id"], updated.get("livreur_message_id"),
                             texts.full_fiche(updated, franchise or {}), keyboards.livreur_course(updated["id"]),
                             user=livreur, resend_if_old=False)
    if franchise and franchise["id"] != editor["id"]:
        await messaging.send(context.bot, franchise,
                             texts.order_modified_for_franchise(updated, before["price"], editor["display_name"]))
        await lifecycle.refresh_franchise_message(context, updated, livreur=livreur, franchise=franchise)
    if editor["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot,
                                        texts.d_order_modified(updated, editor, before["products"], before["price"]))


async def edit_typed_price(update: Update, context, user: dict) -> None:
    """Prix tapé au clavier pendant le réglage d'une ligne (livreur ou admin)."""
    async with _livreur_locks[user["id"]]:
        user = await db.get_user(user["id"])
        state, payload = common.active_state(user)
        if state != EDIT_STATE:
            await messaging.reply(update, texts.LIVREUR_TEXT_HINT)
            return
        course = await db.get_course(payload.get("course_id") or 0)
        if _edit_mode(user, course) is None:
            await db.clear_state(user["id"])
            await messaging.reply(update, texts.COURSE_FINISHED)
            return
        sel = payload.get("sel")
        price = order_edit.parse_price(update.message.text or "")
        if sel is None:
            await messaging.reply(update, texts.ORDER_EDIT_USE_BUTTONS)
            return
        if price is None or not order_edit.valid_price(price):
            await messaging.reply(update, texts.ORDER_EDIT_PRICE_HINT)
            return
        payload["lines"] = order_edit.set_price(payload["lines"], sel, price)
        payload["sel"] = None
        await _save_edit(user, payload)
        await _render_edit(context, update.effective_chat.id, payload)
