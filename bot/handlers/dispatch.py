"""Commandes du dispatch (§13) : /recap, /journal, /encours, /users, /exclure, /reactiver."""
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
from bot.timeutil import hhmm, minutes_since, night_bounds, night_label, night_start_date, now_utc, parse_ts, to_paris

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000


async def _dispatch(update: Update) -> dict | None:
    if update.effective_user.id != config.get().dispatch_telegram_id:
        return None
    user = await common.actor(update)
    if user is None or user["role"] != "dispatch" or user["status"] != "active":
        return None
    return user


async def _guard_command(update: Update) -> bool:
    user = await common.actor(update)
    return await common.require(update, user, role="dispatch")


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
                "produits", "prix"])
    for c in courses:
        at = to_paris(parse_ts(c["delivered_at"]))
        w.writerow([
            c["id"], at.strftime("%Y-%m-%d"), at.strftime("%H:%M"),
            users.get(c["franchise_id"], {}).get("display_name", ""),
            users.get(c["livreur_id"], {}).get("display_name", ""),
            c["address"], c.get("address_detail") or "", c["products"],
            f"{float(c['price']):.2f}".replace(".", ","),
        ])
    return buf.getvalue().encode("utf-8-sig")


async def send_journal(bot, night: date, with_prev_button: bool = True) -> None:
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
    chunks = split_messages(lines)
    markup = keyboards.journal_prev((night - timedelta(days=1)).isoformat()) if with_prev_button else None
    for i, chunk in enumerate(chunks):
        await messaging.notify_dispatch(bot, chunk, markup if i == len(chunks) - 1 else None)
    if delivered:
        filename = f"journal_{(night + timedelta(days=1)).isoformat()}.csv"
        try:
            await bot.send_document(cfg.dispatch_telegram_id, InputFile(build_csv(delivered, users), filename=filename))
        except TelegramError as exc:
            log.warning("Envoi du CSV impossible : %s", exc)


async def journal(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    await send_journal(context.bot, _current_night())


@common.callback
async def journal_prev(update: Update, context):
    if await _dispatch(update) is None:
        return None
    await send_journal(context.bot, date.fromisoformat(common.arg(update)))
    return None


# ================================================================ /encours

async def encours(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    pending = await db.list_courses_by_status("pending")
    assigned = await db.list_courses_by_status("assigned")
    livreurs = await db.list_users(role="livreur", status="active")
    users = await db.get_users([c["franchise_id"] for c in pending + assigned] +
                               [c["livreur_id"] for c in assigned])
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
    await messaging.reply(update, texts.encours(pending_lines, assigned_lines, on_duty, len(livreurs)),
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
    _, action, course_id = update.callback_query.data.split(":", 2)
    course = await db.get_course(int(course_id))
    message_id = update.callback_query.message.message_id
    result = None
    if course is not None:
        if action == "cancel":
            result = await lifecycle.cancel(context, course, by="dispatch")
        elif action == "deliver" and course["status"] == "assigned":
            result = await lifecycle.deliver(context, course, by_dispatch=True)
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
        return "⛔ exclu" if u["status"] == "banned" else "actif"

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
            livreurs.append(f"{texts.user_who(u)} — ⛔ exclu")
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


# ================================================================ /exclure, /reactiver

async def exclure(update: Update, context) -> None:
    if not await _guard_command(update):
        return
    targets = [u for u in await db.list_users(status="active") if u["role"] != "dispatch"]
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


@common.callback
async def ban_ask(update: Update, context):
    if await _dispatch(update) is None:
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] != "active" or user["role"] == "dispatch":
        return texts.ALREADY_HANDLED
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
    user = await db.update_user(user["id"], {
        "status": "banned", "on_duty": False, "soon_free": False,
        "conversation_state": None, "state_payload": None, "state_expires_at": None,
    })
    if user["role"] == "livreur":
        for course in await db.list_assigned_for_livreur(user["id"]):
            await lifecycle.release(context, course, reason="ban")
    elif user["role"] == "franchise":
        for course in await db.list_courses_by_status("pending"):
            if course["franchise_id"] == user["id"]:
                await lifecycle.cancel(context, course, by="ban")
    await messaging.send(context.bot, user, texts.BANNED_NOTICE)
    await common.clear_commands(context.bot, user)
    await db.log_event("user_banned", user_id=user["id"], payload={"by": dispatcher["id"]})
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.banned_done(user))
    return "Exclu"


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
    """/produits : gestion du catalogue pour le dispatch, lecture seule pour un franchisé."""
    user = await common.actor(update)
    if user is not None and user["status"] == "active" and user["role"] == "franchise":
        from bot.handlers import franchise

        await franchise.produits(update, context)
        return
    if not await common.require(update, user, role="dispatch"):
        return
    await _send_catalog(update)


async def _send_catalog(update: Update) -> None:
    products = await db.list_products()
    await messaging.reply(update, texts.catalog_list(products, for_dispatch=True), keyboards.catalog(products))


async def ajouter(update: Update, context) -> None:
    """/ajouter suivi des produits (un par ligne), ou seul pour ouvrir la saisie."""
    user = await common.actor(update)
    if not await common.require(update, user, role="dispatch"):
        return
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
    if user.get("conversation_state") == "adding_products":
        await db.clear_state(user["id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.CANCELLED_OP)
    return None


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
    delivered = await db.list_delivered_between(start, end)
    restocks = await db.list_restocks_between(start, end)
    users = await db.get_users([c["livreur_id"] for c in delivered] + [c["franchise_id"] for c in delivered]
                               + [r["livreur_id"] for r in restocks] + [r["by_user_id"] for r in restocks])
    try:
        catalog = await sheets.load_catalog()
        rows = [sheets.row(c, users, catalog) for c in delivered] + [sheets.restock_row(r, users) for r in restocks]
        added = await sheets.send_rows(rows)
    except RuntimeError as exc:
        await messaging.reply(update, texts.sheets_failed(str(exc)))
        return
    await messaging.reply(update, texts.sheets_synced(len(delivered), added, len(restocks)))
