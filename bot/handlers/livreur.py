"""Côté livreur : service, prise de course (verrou), livraison, enchaînement, annulation (§8–§11.1)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common, relay
from bot.services import broadcast, catalog, lifecycle, order_edit
from bot.timeutil import now_utc, parse_ts

log = logging.getLogger(__name__)

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
        await messaging.reply(update, texts.ON_DUTY)
        await broadcast.kick_pending(context)
    else:
        await messaging.reply(update, texts.DISPO_PROMPT)


async def pause(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="livreur"):
        return
    await db.update_user(user["id"], {"on_duty": False, "soon_free": False})
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
    if state == EDIT_STATE and payload.get("sel") is not None:
        await _edit_typed_price(update, context, user, payload)
        return
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
    await messaging.reply(update, texts.LIVREUR_TEXT_HINT)


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
    if course.get("livreur_message_id") != update.callback_query.message.message_id:
        course["livreur_message_id"] = update.callback_query.message.message_id
    updated = await lifecycle.deliver(context, course)
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

EDIT_STATE = "editing_order"
EDIT_MINUTES = 30


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


async def _back_to_card(context, chat_id: int, message_id: int | None, course: dict) -> None:
    franchise = await db.get_user(course["franchise_id"])
    await messaging.edit(context.bot, chat_id, message_id, texts.full_fiche(course, franchise or {}),
                         keyboards.livreur_course(course["id"]))


async def _edit_context(update: Update):
    """(livreur, payload, course) d'une modification en cours, course encore à lui et en cours."""
    user = await _livreur(update)
    if user is None:
        return None, None, None
    state, payload = common.active_state(user)
    if state != EDIT_STATE or not payload.get("course_id"):
        return user, None, None
    course = await db.get_course(payload["course_id"])
    if course is None or course.get("livreur_id") != user["id"] or course["status"] != "assigned":
        await db.clear_state(user["id"])
        return user, None, None
    return user, payload, course


@common.callback
async def edit_start(update: Update, context):
    user, course = await _own_assigned(update)
    if course is None:
        return (texts.COURSE_FINISHED, True) if user else None
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
    if course and course.get("livreur_id") == user["id"] and course["status"] == "assigned":
        await _back_to_card(context, update.effective_chat.id, msg_id, course)
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
    user = await _livreur(update)
    if user is None:
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
            await _back_to_card(context, chat_id, payload["msg"], course)
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
    missing = order_edit.missing_prices(lines)
    if missing:
        return texts.order_edit_missing_price(missing), True
    if order_edit.same(lines, payload.get("orig") or []):
        await db.clear_state(user["id"])
        await _back_to_card(context, chat_id, payload["msg"], course)
        return texts.ORDER_EDIT_UNCHANGED
    fields = {"products": order_edit.products_text(lines), "price": order_edit.total(lines)}
    updated = await db.update_course_if_status(course["id"], ["assigned"], fields, livreur_id=user["id"])
    await db.clear_state(user["id"])
    if updated is None:
        await messaging.edit_markup(context.bot, chat_id, payload["msg"], None)
        return texts.COURSE_FINISHED, True
    await _back_to_card(context, chat_id, payload["msg"], updated)
    await db.log_event("course_modified", course["id"], user["id"], {
        "before": {"products": course["products"], "price": float(course["price"])},
        "after": {"products": updated["products"], "price": float(updated["price"])},
    })
    franchise = await db.get_user(course["franchise_id"])
    if franchise:
        await messaging.send(context.bot, franchise,
                             texts.order_modified_for_franchise(updated, course["price"], user["display_name"]))
        await lifecycle.refresh_franchise_message(context, updated, livreur=user, franchise=franchise)
    await messaging.notify_dispatch(context.bot,
                                    texts.d_order_modified(updated, user, course["products"], course["price"]))
    return "Commande modifiée ✅"


async def _edit_typed_price(update: Update, context, user: dict, payload: dict) -> None:
    """Prix tapé au clavier pendant le réglage d'une ligne."""
    async with _livreur_locks[user["id"]]:
        user = await db.get_user(user["id"])
        state, payload = common.active_state(user)
        if state != EDIT_STATE:
            await messaging.reply(update, texts.LIVREUR_TEXT_HINT)
            return
        course = await db.get_course(payload.get("course_id") or 0)
        if course is None or course.get("livreur_id") != user["id"] or course["status"] != "assigned":
            await db.clear_state(user["id"])
            await messaging.reply(update, texts.COURSE_FINISHED)
            return
        sel = payload.get("sel")
        price = order_edit.parse_price(update.message.text or "")
        if sel is None:
            await messaging.reply(update, texts.ORDER_EDIT_USE_BUTTONS)
            return
        if price is None:
            await messaging.reply(update, texts.ORDER_EDIT_PRICE_HINT)
            return
        payload["lines"] = order_edit.set_price(payload["lines"], sel, price)
        payload["sel"] = None
        await _save_edit(user, payload)
        await _render_edit(context, update.effective_chat.id, payload)
