"""Commandes d'administration (§13) : /recap, /journal, /encours, /livreurs, /users, /bannir (ex-/exclure), /reactiver…

Ouvertes au dispatch et aux franchisés (pleins pouvoirs, voir common.is_admin)."""
from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import date, timedelta

from telegram import InputFile, Update
from telegram.error import TelegramError

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import catalog as catalog_service
from bot.services import sheets
from bot.services import lifecycle
from bot.services import names
from bot.timeutil import hhmm, minutes_since, night_bounds, night_label, night_start_date, now_utc, parse_ts, to_paris

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000


async def _dispatch(update: Update) -> dict | None:
    """Admin (dispatch ou franchisé) à l'origine du bouton, sinon None."""
    user = await common.actor(update)
    return user if common.is_admin(user) else None


async def _guard_command(update: Update) -> bool:
    user = await common.actor(update)
    if not await common.require(update, user):
        return False
    if not common.is_admin(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return False
    return True


def _current_night() -> date:
    return night_start_date(now_utc(), config.get().night_end_hour)


# ================================================================ /recap

async def build_recap(night: date) -> str:
    cfg = config.get()
    start, end = night_bounds(night, cfg.night_end_hour)
    delivered = await db.list_delivered_between(start, end)
    on_site = await db.list_closed_between("cancelled_on_site", start, end)
    users = await db.get_users([c["livreur_id"] for c in delivered] + [c["franchise_id"] for c in on_site])

    per_livreur: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for c in delivered:
        per_livreur[c["livreur_id"]][0] += 1
        per_livreur[c["livreur_id"]][1] += float(c["price"])
    rows = sorted(
        ((users.get(lid, {}).get("display_name", "?"), n, total) for lid, (n, total) in per_livreur.items()),
        key=lambda r: (-r[2], -r[1], r[0]),
    )
    per_franchise: dict[str, int] = defaultdict(int)
    for c in on_site:
        per_franchise[c["franchise_id"]] += 1
    on_site_rows = sorted(((users.get(fid, {}).get("display_name", "?"), n) for fid, n in per_franchise.items()),
                          key=lambda r: -r[1])
    pending = assigned = None
    if night == _current_night():
        pending = len(await db.list_courses_by_status("pending"))
        assigned = len(await db.list_courses_by_status("assigned"))
    return texts.recap(night_label(night), rows, on_site_rows, pending, assigned)


async def recap(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    night = _current_night()
    await messaging.reply(update, await build_recap(night), keyboards.recap_prev((night - timedelta(days=1)).isoformat()))


@common.callback
async def recap_prev(update: Update, context):
    if await _dispatch(update) is None:
        return None
    night = date.fromisoformat(common.arg(update))
    await messaging.reply(update, await build_recap(night), keyboards.recap_prev((night - timedelta(days=1)).isoformat()))
    return None


# ================================================================ /journal

def split_messages(lines: list[str], limit: int = TELEGRAM_LIMIT) -> list[str]:
    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_csv(courses: list[dict], users: dict[str, dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["numero", "date_livraison", "heure_livraison", "franchise", "livreur", "adresse", "complement",
                "produits", "prix", "paiement"])
    for c in courses:
        at = to_paris(parse_ts(c["delivered_at"]))
        w.writerow([
            c["id"], at.strftime("%Y-%m-%d"), at.strftime("%H:%M"),
            users.get(c["franchise_id"], {}).get("display_name", ""),
            users.get(c["livreur_id"], {}).get("display_name", ""),
            c["address"], c.get("address_detail") or "", c["products"],
            f"{float(c['price']):.2f}".replace(".", ","),
            sheets.PAYMENT_SHEET.get(c.get("payment") or "", ""),
        ])
    return buf.getvalue().encode("utf-8-sig")


async def send_journal(bot, night: date, with_prev_button: bool = True, chat_id: int | None = None) -> None:
    """Journal d'une nuit : au dispatch (job de 6h) ou à l'admin qui le demande (chat_id)."""
    cfg = config.get()
    start, end = night_bounds(night, cfg.night_end_hour)
    delivered = await db.list_delivered_between(start, end)
    users = await db.get_users([c["livreur_id"] for c in delivered] + [c["franchise_id"] for c in delivered])
    label = night_label(night)
    lines = [texts.journal_header(label, len(delivered))]
    if delivered:
        lines.append("")
        for c in delivered:
            lines.append(texts.journal_line(
                hhmm(parse_ts(c["delivered_at"])), c,
                users.get(c["franchise_id"], {}).get("display_name", "?"),
                users.get(c["livreur_id"], {}).get("display_name", "?"),
            ))
        lines += ["", texts.journal_total(sum(float(c["price"]) for c in delivered))]
    chunks = split_messages([names.decorate(line) for line in lines])   # prénoms avant le découpage (longueur)
    markup = keyboards.journal_prev((night - timedelta(days=1)).isoformat()) if with_prev_button else None
    target = chat_id or cfg.dispatch_telegram_id
    for i, chunk in enumerate(chunks):
        try:
            await bot.send_message(target, chunk, reply_markup=markup if i == len(chunks) - 1 else None)
        except TelegramError as exc:
            log.warning("Envoi du journal impossible : %s", exc)
    if delivered:
        filename = f"journal_{(night + timedelta(days=1)).isoformat()}.csv"
        try:
            await bot.send_document(target, InputFile(build_csv(delivered, users), filename=filename))
        except TelegramError as exc:
            log.warning("Envoi du CSV impossible : %s", exc)


async def journal(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    await send_journal(context.bot, _current_night(), chat_id=update.effective_chat.id)


@common.callback
async def journal_prev(update: Update, context):
    if await _dispatch(update) is None:
        return None
    await send_journal(context.bot, date.fromisoformat(common.arg(update)), chat_id=update.effective_chat.id)
    return None


# ================================================================ /encours

async def encours(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    pending = await db.list_courses_by_status("pending")
    assigned = await db.list_courses_by_status("assigned")
    livreurs = await db.list_users(role="livreur", status="active")
    night = _current_night()
    start, end = night_bounds(night, config.get().night_end_hour)
    delivered = await db.list_delivered_between(start, end)
    cancelled = (await db.list_closed_between("cancelled", start, end)
                 + await db.list_closed_between("cancelled_on_site", start, end))
    users = await db.get_users([c["franchise_id"] for c in pending + assigned] +
                               [c["livreur_id"] for c in assigned + delivered])
    per_livreur: dict[str, int] = defaultdict(int)
    for c in assigned:
        per_livreur[c["livreur_id"]] += 1
    unknown = {"display_name": "?"}
    pending_lines = [texts.encours_pending_line(c, users.get(c["franchise_id"], unknown)) for c in pending]
    assigned_lines = [
        texts.encours_assigned_line(
            c, users.get(c["franchise_id"], unknown), users.get(c["livreur_id"], unknown),
            minutes_since(parse_ts(c["assigned_at"])), per_livreur[c["livreur_id"]] >= 2,
        )
        for c in assigned
    ]
    on_duty = sum(1 for lv in livreurs if lv.get("on_duty"))
    # Livrées cette nuit : combien, combien d'argent, par livreur.
    per: dict[str, list] = defaultdict(lambda: [0, 0.0])
    pay = {"especes": 0.0, "virement": 0.0}
    for c in delivered:
        name = users.get(c.get("livreur_id"), unknown)["display_name"]
        per[name][0] += 1
        per[name][1] += float(c["price"])
        if c.get("payment") in pay:
            pay[c["payment"]] += float(c["price"])
    done = {"n": len(delivered), "total": sum(float(c["price"]) for c in delivered), "pay": pay,
            "per": sorted(((n, k, t) for n, (k, t) in per.items()), key=lambda r: (-r[1], -r[2], r[0])),
            "cancelled": len(cancelled)}
    await messaging.reply(update, texts.encours(pending_lines, assigned_lines, on_duty, len(livreurs),
                                                done, night_label(night)),
                          keyboards.encours(pending, assigned))


@common.callback
async def override_ask(update: Update, context):
    if await _dispatch(update) is None:
        return None
    prefix, course_id = update.callback_query.data.split(":", 1)
    action = prefix.removeprefix("force_")
    await messaging.reply(update, texts.override_confirm(action, int(course_id)),
                          keyboards.override_confirm(action, int(course_id)))
    return None


@common.callback
async def override_do(update: Update, context):
    dispatcher = await _dispatch(update)
    if dispatcher is None:
        return None
    parts = update.callback_query.data.split(":")
    action, course_id = parts[1], parts[2]
    payment = lifecycle.PAYMENTS.get(parts[3]) if len(parts) > 3 else None
    course = await db.get_course(int(course_id))
    message_id = update.callback_query.message.message_id
    result = None
    if course is not None:
        if action == "cancel":
            result = await lifecycle.cancel(context, course, by="dispatch")
        elif action == "deliver" and course["status"] == "assigned":
            result = await lifecycle.deliver(context, course, by_dispatch=True, payment=payment)
        elif action == "release" and course["status"] == "assigned":
            result = await lifecycle.release(context, course, reason="dispatch")
    if result is None:
        await messaging.edit(context.bot, update.effective_chat.id, message_id,
                             f"Rien à faire : la course #{course_id} a changé d'état entre-temps.")
        return None
    await db.log_event("dispatch_override", int(course_id), dispatcher["id"],
                       {"action": action, "previous_status": course["status"]})
    await messaging.edit(context.bot, update.effective_chat.id, message_id, texts.d_override(action, int(course_id)))
    return "Fait"


@common.callback
async def op_cancel(update: Update, context):
    if await _dispatch(update) is None:
        return None
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.CANCELLED_OP)
    return None


# ================================================================ /users

async def users(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    all_users = await db.list_users()
    on_site = defaultdict(int)
    for c in await db.list_franchise_cancelled_on_site():
        on_site[c["franchise_id"]] += 1

    def status_label(u: dict) -> str:
        return "⛔ banni" if u["status"] == "banned" else "actif"

    def sort_key(u: dict):
        name = u.get("display_name") or ""
        digits = "".join(ch for ch in name if ch.isdigit())
        return (int(digits) if digits else 10_000, name)

    franchises, livreurs = [], []
    for u in sorted((u for u in all_users if u["role"] == "franchise" and u["status"] != "pending"), key=sort_key):
        line = f"{texts.user_who(u)} — {status_label(u)}"
        if u["status"] != "banned" and on_site[u["id"]]:
            line += f" — {texts.plural(on_site[u['id']], 'annulation')} sur place"
        franchises.append(line)
    for u in sorted((u for u in all_users if u["role"] == "livreur" and u["status"] != "pending"), key=sort_key):
        if u["status"] == "banned":
            livreurs.append(f"{texts.user_who(u)} — ⛔ banni")
        else:
            duty = "en service" if u.get("on_duty") else "pause"
            n = u.get("cancel_count") or 0
            livreurs.append(f"{texts.user_who(u)} — actif · {duty} — {n} annulation{'s' if n > 1 else ''}")
    ravitailleurs = [f"{texts.user_who(u)} — {status_label(u)}"
                     for u in sorted((u for u in all_users if u["role"] == "ravitailleur" and u["status"] != "pending"),
                                     key=sort_key)]
    pending_users = [u for u in all_users if u["status"] == "pending" and u["role"] != "dispatch"]
    pending = []
    for u in pending_users:
        line = f"{texts.ROLE_LABEL[u['role']]} — {texts.esc(u.get('real_name') or '(nom pas encore donné)')}"
        if u.get("telegram_username"):
            line += f" (@{texts.esc(u['telegram_username'])})"
        pending.append(line)
    await messaging.reply(update, texts.users_list(franchises, livreurs, pending, ravitailleurs),
                          keyboards.pending_users(pending_users))


# ================================================================ /bannir (ex-/exclure), /reactiver

async def exclure(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    me = update.effective_user.id
    targets = [u for u in await db.list_users(status="active") if u["role"] != "dispatch" and u["telegram_id"] != me]
    if not targets:
        await messaging.reply(update, texts.EXCLURE_EMPTY)
        return
    await messaging.reply(update, texts.EXCLURE_HEADER, keyboards.user_buttons(targets, "ban"))


async def reactiver(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    targets = await db.list_users(status="banned")
    if not targets:
        await messaging.reply(update, texts.REACTIVER_EMPTY)
        return
    await messaging.reply(update, texts.REACTIVER_HEADER, keyboards.user_buttons(targets, "unban"))


async def _remove_access(context, user: dict, status: str) -> dict:
    """Exclusion ou suppression : plus de service, courses rendues ou annulées, menu retiré,
    messages du bot des dernières 48 h effacés chez lui ; il ne reçoit plus rien ensuite."""
    user = await db.update_user(user["id"], {
        "status": status, "on_duty": False, "soon_free": False, "duty_forced": False,
        "conversation_state": None, "state_payload": None, "state_expires_at": None,
    })
    if user["role"] == "livreur":
        for course in await db.list_assigned_for_livreur(user["id"]):
            await lifecycle.release(context, course, reason="ban")
        await db.delete_position(user["id"])
    elif user["role"] == "franchise":
        for course in await db.list_courses_by_status("pending"):
            if course["franchise_id"] == user["id"]:
                await lifecycle.cancel(context, course, by="ban")
    wiped = await messaging.wipe(context.bot, user["telegram_id"])
    await common.clear_commands(context.bot, user)
    user["wiped"] = wiped
    return user


@common.callback
async def ban_ask(update: Update, context):
    if await _dispatch(update) is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] != "active" or user["role"] == "dispatch":
        return texts.ALREADY_HANDLED
    if user["telegram_id"] == update.effective_user.id:
        return texts.CANNOT_BAN_SELF, True
    await messaging.reply(update, texts.ban_confirm(user), keyboards.ban_confirm(user["id"]))
    return None


@common.callback
async def ban_do(update: Update, context):
    dispatcher = await _dispatch(update)
    if dispatcher is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] != "active" or user["role"] == "dispatch":
        return texts.ALREADY_HANDLED
    if user["id"] == dispatcher["id"]:
        return texts.CANNOT_BAN_SELF, True
    user = await _remove_access(context, user, "banned")
    await messaging.send(context.bot, user, texts.BANNED_NOTICE, even_if_removed=True)
    await db.log_event("user_banned", user_id=user["id"], payload={"by": dispatcher["id"]})
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.banned_done(user))
    return "Banni"


# ================================================================ /supprimer (définitif)

async def supprimer(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    me = update.effective_user.id
    targets = [u for u in await db.list_users() if u["role"] != "dispatch" and u["telegram_id"] != me]
    if not targets:
        await messaging.reply(update, texts.SUPPRIMER_EMPTY)
        return
    await messaging.reply(update, texts.SUPPRIMER_HEADER, keyboards.user_buttons(targets, "del", with_status=True))


@common.callback
async def delete_ask(update: Update, context):
    if await _dispatch(update) is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] == "deleted" or user["role"] == "dispatch":
        return texts.ALREADY_HANDLED
    if user["telegram_id"] == update.effective_user.id:
        return texts.CANNOT_BAN_SELF, True
    await messaging.reply(update, texts.delete_confirm(user), keyboards.delete_confirm(user["id"]))
    return None


@common.callback
async def delete_do(update: Update, context):
    """Compte supprimé : accès retiré comme une exclusion, puis le compte est détaché de son
    Telegram (la personne peut se réinscrire de zéro avec /start, dans le rôle de son choix) et
    son nom de feuille est libéré. Les courses passées gardent son nom dans l'historique."""
    admin = await _dispatch(update)
    if admin is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] == "deleted" or user["role"] == "dispatch":
        return texts.ALREADY_HANDLED
    if user["id"] == admin["id"]:
        return texts.CANNOT_BAN_SELF, True
    telegram_id = user["telegram_id"]
    user = await _remove_access(context, user, "deleted")
    await messaging.send(context.bot, user, texts.DELETED_NOTICE, even_if_removed=True)
    await db.forget_messages(chat_id=telegram_id)
    # Détaché de son compte Telegram : un /start repart de zéro.
    detached = -abs(telegram_id)
    while await db.get_user_by_tg(detached):
        detached -= 10 ** 12
    await db.update_user(user["id"], {"telegram_id": detached, "telegram_username": None, "real_name": None})
    await db.log_event("user_deleted", user_id=user["id"], payload={"by": admin["id"], "role": user["role"]})
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.deleted_done(user))
    if admin["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, f"{texts.deleted_done(user)} (par {texts.esc(admin['display_name'])})")
    return "Supprimé"


@common.callback
async def unban(update: Update, context):
    dispatcher = await _dispatch(update)
    if dispatcher is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] != "banned":
        return texts.ALREADY_HANDLED
    user = await db.update_user(user["id"], {"status": "active"})
    await common.set_commands(context.bot, user)
    await messaging.send(context.bot, user, f"{texts.UNBANNED_NOTICE}\n\n{texts.welcome(user['role'])}")
    await db.log_event("user_unbanned", user_id=user["id"], payload={"by": dispatcher["id"]})
    await messaging.reply(update, texts.unbanned_done(user))
    return "Réactivé"


# ================================================================ catalogue des produits

CATALOG_STATE_MINUTES = 10


async def produits(update: Update, context) -> None:
    """/produits : gestion du catalogue (dispatch et franchisés)."""
    if not await _guard_command(update):
        return
    await _send_catalog(update)


async def _send_catalog(update: Update) -> None:
    products = await db.list_products()
    await messaging.reply(update, texts.catalog_list(products, for_dispatch=True), keyboards.catalog(products))


async def ajouter(update: Update, context) -> None:
    """/ajouter suivi des produits (un par ligne), ou seul pour ouvrir la saisie."""
    if not await _guard_command(update):
        return
    user = await common.actor(update)
    text = update.message.text or ""
    body = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    if body.strip():
        await save_products(update, user, body)
    else:
        await _open_catalog_input(update, user)


async def _open_catalog_input(update: Update, user: dict) -> None:
    await db.set_state(user["id"], "adding_products", {},
                       now_utc() + timedelta(minutes=CATALOG_STATE_MINUTES))
    await messaging.reply(update, texts.CATALOG_ADD_PROMPT, keyboards.catalog_cancel())


async def save_products(update: Update, user: dict, text: str) -> None:
    entries = catalog_service.parse_input(text)
    if not entries:
        await messaging.reply(update, texts.CATALOG_NOTHING)
        return
    summary = await catalog_service.add_products(entries)
    if user.get("conversation_state") == "adding_products":
        await db.clear_state(user["id"])
    await db.log_event("catalog_updated", user_id=user["id"],
                       payload={k: v for k, v in summary.items() if k != "conflicts"})
    await messaging.reply(update, texts.catalog_summary(summary))
    await _send_catalog(update)


@common.callback
async def catalog_add(update: Update, context):
    user = await _dispatch(update)
    if user is None:
        return None
    await _open_catalog_input(update, user)
    return None


@common.callback
async def catalog_cancel(update: Update, context):
    user = await _dispatch(update)
    if user is None:
        return None
    if user.get("conversation_state") in ("adding_products", "editing_product"):
        await db.clear_state(user["id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.CANCELLED_OP)
    return None


@common.callback
async def catalog_edit(update: Update, context):
    """prod_edit:<produit> : le bot attend la ligne corrigée (« Nom : surnoms » ou « + surnoms »)."""
    user = await _dispatch(update)
    if user is None:
        return None
    product = await db.get_product(common.arg(update, int))
    if product is None:
        return texts.ALREADY_HANDLED
    await db.set_state(user["id"], "editing_product", {"id": product["id"]},
                       now_utc() + timedelta(minutes=CATALOG_STATE_MINUTES))
    await messaging.reply(update, texts.catalog_edit_prompt(product), keyboards.catalog_cancel())
    return None


async def save_product_edit(update: Update, user: dict, text: str, payload: dict) -> None:
    product = await db.get_product(int(payload.get("id") or 0))
    if product is None:
        await db.clear_state(user["id"])
        await messaging.reply(update, texts.ALREADY_HANDLED)
        return
    updated, error, extra = await catalog_service.edit_product(product, text)
    if error:
        await messaging.reply(update, texts.catalog_edit_error(error, extra))   # l'état reste : il renvoie
        return
    await db.clear_state(user["id"])
    await db.log_event("catalog_edited", user_id=user["id"], payload={
        "before": {"name": product["name"], "aliases": product.get("aliases") or []},
        "after": {"name": updated["name"], "aliases": updated.get("aliases") or []}})
    await messaging.reply(update, texts.catalog_edited(updated, extra))
    await _send_catalog(update)


@common.callback
async def catalog_delete(update: Update, context):
    user = await _dispatch(update)
    if user is None:
        return None
    product = await db.get_product(common.arg(update, int))
    if product is None:
        return texts.ALREADY_HANDLED
    await db.delete_product(product["id"])
    await db.log_event("catalog_deleted", user_id=user["id"], payload={"name": product["name"]})
    products = await db.list_products()
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.catalog_list(products, for_dispatch=True), keyboards.catalog(products))
    return f"{product['name']} supprimé"


# ================================================================ /synchro (Google Sheets)

async def synchro(update: Update, context) -> None:
    """Renvoie à Google Sheets toutes les courses livrées de la nuit en cours.
    Sans danger : la feuille ignore les courses déjà présentes."""
    if not await _guard_command(update):
        return
    if not sheets.enabled():
        await messaging.reply(update, texts.SHEETS_DISABLED)
        return
    cfg = config.get()
    night = _current_night()
    start, end = night_bounds(night, cfg.night_end_hour)
    # Une course dont la modification attend le franchisé n'est écrite qu'après sa décision.
    delivered = [c for c in await db.list_delivered_between(start, end) if not c.get("pending_edit")]
    restocks = await db.list_restocks_between(start, end)
    expenses = await db.list_expenses_between(start, end)
    sold = await db.list_sales_between(start, end)
    users = await db.get_users([c["livreur_id"] for c in delivered] + [c["franchise_id"] for c in delivered]
                               + [r["livreur_id"] for r in restocks] + [r["by_user_id"] for r in restocks]
                               + [e["livreur_id"] for e in expenses])
    try:
        catalog = await sheets.load_catalog()
        rows = ([sheets.row(c, users, catalog) for c in delivered] + [sheets.restock_row(r, users) for r in restocks]
                + [sheets.expense_row(e, users) for e in expenses] + [sheets.sale_row(s) for s in sold])
        added = await sheets.send_rows(rows)
    except RuntimeError as exc:
        await messaging.reply(update, texts.sheets_failed(str(exc)))
        return
    await messaging.reply(update, texts.sheets_synced(len(delivered), added, len(restocks), len(expenses), len(sold)))


# ================================================================ /livreurs : service et pause par un admin

async def _livreurs_view() -> tuple[str, object]:
    livreurs = sorted(await db.list_users(role="livreur", status="active"),
                      key=lambda u: u.get("display_name") or "")
    positions = await db.get_positions(lv["id"] for lv in livreurs)
    counts: dict[str, int] = defaultdict(int)
    for c in await db.list_assigned_for_livreurs([lv["id"] for lv in livreurs]):
        counts[c["livreur_id"]] += 1
    now = now_utc()
    stale_before = now - timedelta(minutes=config.get().position_stale_minutes)
    asked = await db.last_user_events("dispo_asked", now - timedelta(hours=2))
    rows = []
    for lv in livreurs:
        pos = positions.get(lv["id"])
        at = parse_ts(pos["updated_at"]) if pos else None
        located = at is not None and at >= stale_before
        minutes = (now - at).total_seconds() / 60 if at else None
        # /dispo récent sans position reçue depuis : « position pas encore reçue ».
        asked_at = parse_ts(asked.get(lv["id"])) if asked.get(lv["id"]) else None
        waiting = (now - asked_at).total_seconds() / 60 if asked_at and (at is None or at < asked_at) else None
        rows.append((lv, located, counts[lv["id"]], pos, minutes, waiting))
    return texts.livreurs_list(rows), keyboards.livreurs_duty(livreurs)


async def _send_position(context, chat_id: int, livreur: dict, pos: dict) -> None:
    """Point du livreur sur une carte, avec l'adresse la plus proche, l'âge et le type de partage."""
    from bot.services import geocoding

    now = now_utc()
    at = parse_ts(pos["updated_at"])
    minutes = (now - at).total_seconds() / 60
    fresh = at >= now - timedelta(minutes=config.get().position_stale_minutes)
    address = await geocoding.reverse(pos["lat"], pos["lon"])
    await messaging.send_venue(context.bot, chat_id, pos["lat"], pos["lon"], texts.venue_title(livreur, minutes),
                               texts.venue_address(livreur, pos, address, fresh))


@common.callback
async def show_position(update: Update, context):
    """loc:<livreur> — 📍 dans /livreurs."""
    if await _dispatch(update) is None:
        return None
    livreur = await db.get_user(common.arg(update))
    if livreur is None or livreur["role"] != "livreur":
        return texts.ALREADY_HANDLED, True
    pos = await db.get_position(livreur["id"])
    if pos is None:
        return texts.NO_POSITION, True
    await _send_position(context, update.effective_chat.id, livreur, pos)
    return None


@common.callback
async def show_all_positions(update: Update, context):
    """loc_all — 🗺 un point par livreur en service."""
    if await _dispatch(update) is None:
        return None
    livreurs = sorted(await db.list_on_duty_livreurs(), key=lambda u: u.get("display_name") or "")
    positions = await db.get_positions(lv["id"] for lv in livreurs)
    shown = [lv for lv in livreurs if lv["id"] in positions]
    if not shown:
        return texts.NO_POSITIONS, True
    for lv in shown:
        await _send_position(context, update.effective_chat.id, lv, positions[lv["id"]])
    return None


async def livreurs(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    text, markup = await _livreurs_view()
    await messaging.reply(update, text, markup)


@common.callback
async def duty(update: Update, context):
    """duty_on:<livreur> / duty_off:<livreur> : un admin met un livreur en service ou en pause."""
    admin = await _dispatch(update)
    if admin is None:
        return None
    prefix, livreur_id = update.callback_query.data.split(":", 1)
    livreur = await db.get_user(livreur_id)
    if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
        return texts.ALREADY_HANDLED, True
    if prefix == "duty_on":
        await db.update_user(livreur["id"], {"on_duty": True, "duty_forced": True})
        await db.log_event("livreur_on_duty", user_id=livreur["id"], payload={"by": admin["id"]})
        await messaging.send(context.bot, livreur, texts.duty_on_by_admin(admin))
        from bot.services import broadcast

        await broadcast.kick_pending(context)
        answer = f"{livreur['display_name']} en service"
    else:
        await db.update_user(livreur["id"], {"on_duty": False, "soon_free": False, "duty_forced": False})
        await db.log_event("livreur_pause", user_id=livreur["id"], payload={"by": admin["id"]})
        await messaging.send(context.bot, livreur, texts.duty_off_by_admin(admin))
        answer = f"{livreur['display_name']} en pause"
    text, markup = await _livreurs_view()
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id, text, markup)
    return answer


# ================================================================ attribution directe d'une course

@common.callback
async def assign_ask(update: Update, context):
    """assign:<course> : l'admin choisit le livreur d'une course en attente."""
    if await _dispatch(update) is None:
        return None
    course = await db.get_course(common.arg(update, int))
    if course is None or course["status"] != "pending":
        return texts.ALREADY_CLOSED, True
    livreurs = sorted(await db.list_users(role="livreur", status="active"),
                      key=lambda u: (not u.get("on_duty"), u.get("display_name") or ""))
    if not livreurs:
        return texts.RESTOCK_NO_LIVREUR, True
    counts: dict[str, int] = defaultdict(int)
    for c in await db.list_assigned_for_livreurs([lv["id"] for lv in livreurs]):
        counts[c["livreur_id"]] += 1
    await messaging.reply(update, texts.assign_prompt(course),
                          keyboards.assign_livreurs(course["id"], livreurs, counts))
    return None


@common.callback
async def assign_do(update: Update, context):
    """assign_to:<course>:<livreur> : attribue la course, sans passer par « Je prends »."""
    admin = await _dispatch(update)
    if admin is None:
        return None
    _, course_id, livreur_id = update.callback_query.data.split(":", 2)
    livreur = await db.get_user(livreur_id)
    message_id = update.callback_query.message.message_id
    if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
        return texts.ALREADY_HANDLED, True
    won = await db.take_course(int(course_id), livreur["id"])
    if won is None:
        await messaging.edit(context.bot, update.effective_chat.id, message_id,
                             f"Rien à faire : la course #{course_id} n'est plus en attente.")
        return texts.ALREADY_CLOSED, True
    await lifecycle.after_assignment(context, won, livreur, None)
    await db.log_event("course_assigned_by_admin", won["id"], livreur["id"], {"by": admin["id"]})
    await messaging.edit(context.bot, update.effective_chat.id, message_id, texts.assigned_done(won, livreur))
    return "Attribuée ✅"



# ================================================================ nom dans les feuilles (livreurs, ravitailleurs)

async def _picker(user: dict):
    from bot.services import names

    return texts.name_picker(user), keyboards.name_picker(user, names.names_for(user["role"]),
                                                        await names.holders(user["role"]))


async def send_name_picker(update: Update, user: dict) -> None:
    text, markup = await _picker(user)
    await messaging.reply(update, text, markup)


@common.callback
async def name_ask(update: Update, context):
    """sname:<utilisateur> : ouvre le choix du nom de feuille."""
    from bot.services import names

    if await _dispatch(update) is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or not names.names_for(user["role"]):
        return texts.ALREADY_HANDLED, True
    await send_name_picker(update, user)
    return None


@common.callback
async def name_set(update: Update, context):
    """sname_set:<utilisateur>:<n° du nom> : donne ce nom de feuille (s'il est libre)."""
    from bot.services import names

    admin = await _dispatch(update)
    if admin is None:
        return None
    _, user_id, idx = update.callback_query.data.split(":", 2)
    user = await db.get_user(user_id)
    choices = names.names_for(user["role"]) if user else ()
    if user is None or not 0 <= int(idx) < len(choices):
        return texts.ALREADY_HANDLED, True
    name = choices[int(idx)]
    if user.get("display_name") == name:
        return f"C'est déjà {name}"
    holder = await names.holder_of(user["role"], name, exclude_user_id=user["id"])
    if holder:
        return texts.name_taken(name, holder), True
    old = user.get("display_name")
    user = await db.update_user(user["id"], {"display_name": name})
    await db.log_event("user_renamed", user_id=user["id"], payload={"from": old, "to": name, "by": admin["id"]})
    await messaging.send(context.bot, user, texts.name_set_for_user(user))
    text, markup = await _picker(user)
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         f"{texts.name_set_done(user, old)}\n\n{text}", markup)
    return f"{old} → {name}"


@common.callback
async def name_done(update: Update, context):
    if await _dispatch(update) is None:
        return None
    user = await db.get_user(common.arg(update))
    label = f"🏷 {texts.esc(user['display_name'])} ({texts.esc(user.get('real_name') or '?')})" if user else "OK"
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id, label)
    return None
