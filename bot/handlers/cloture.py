"""/reset (ancien nom : /cloture) : remise à zéro de la semaine dans les feuilles (admins).

1. Vérification : courses encore ouvertes, stock encore chez les livreurs (reporté), cash pas
   encore récupéré et reste chez le ravitailleur (remis à zéro) — puis « ✅ Reset de la semaine ».
2. Le script archive les 3 fichiers dans Drive, reporte le stock des box en stock initial,
   vide la semaine et reporte le stock des livreurs (voir integrations/google_sheets/Code.gs).

Un rappel est envoyé au dispatch chaque lundi à 6h05."""
from __future__ import annotations

import asyncio
import time

from telegram import Update

from bot import db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import cash, sheets, stock
from bot.timeutil import now_utc, to_paris

TIMEOUT = 330.0            # copie des 3 fichiers : jusqu'à quelques minutes
AGAIN_AFTER = 10 * 60      # deux clôtures à moins de 10 min : sûrement un double appui
_lock = asyncio.Lock()
_last_done: float | None = None


async def _precheck() -> tuple[str, object]:
    open_courses = len(await db.list_courses_by_status("pending")) + len(await db.list_courses_by_status("assigned"))
    stock_data, cash_data = await stock.livreurs_now(), await cash.livreurs_now()
    if not stock_data["ok"] or not cash_data["ok"]:
        error = (stock_data if not stock_data["ok"] else cash_data)["error"]
        return texts.cloture_unavailable(error), None
    tab = sheets.day_tab(now_utc())
    text = texts.cloture_precheck(open_courses, stock_data["livreurs"], list(cash_data["livreurs"].values()),
                                  cash_data.get("ravitailleur"), tab)
    return text, keyboards.cloture_confirm()


async def cloture(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not common.is_admin(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    if not sheets.enabled():
        await messaging.reply(update, texts.SHEETS_DISABLED)
        return
    text, markup = await _precheck()
    await messaging.reply(update, text, markup)


@common.callback
async def action(update: Update, context):
    """cl_go : clôturer · cl_x : annuler."""
    global _last_done
    user = await common.actor(update)
    if not common.is_admin(user):
        return None
    data = update.callback_query.data or ""
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    if data == "cl_x":
        await messaging.edit(context.bot, chat_id, msg_id, texts.CLOTURE_CANCELLED)
        return None
    if data != "cl_go":
        return None
    if _lock.locked():
        return texts.CLOTURE_RUNNING, True
    async with _lock:
        if _last_done is not None and time.monotonic() - _last_done < AGAIN_AFTER:
            await messaging.edit_markup(context.bot, chat_id, msg_id, None)
            return texts.CLOTURE_ALREADY, True
        await messaging.edit(context.bot, chat_id, msg_id, texts.CLOTURE_IN_PROGRESS)
        label = f"Reset du {to_paris(now_utc()):%Y-%m-%d %Hh%M}"
        tab = sheets.day_tab(now_utc())
        result = await sheets.fetch_action("cloture", payload={"onglet": tab, "libelle": label}, timeout=TIMEOUT)
        if not result or not result.get("ok"):
            error = (result or {}).get("error") or "Google Sheets non relié"
            await messaging.edit(context.bot, chat_id, msg_id, texts.cloture_failed(error))
            return None
        _last_done = time.monotonic()
        # Nouvelle semaine : les mouvements pas encore écrits appartenaient à l'ancienne.
        stock._inflight.clear()
        stock._sheet_cache = None
        await db.log_event("cloture", user_id=user["id"],
                           payload={"archive": result.get("archive"), "reports": result.get("reports")})
        text = texts.cloture_done(result, tab)
        await messaging.edit(context.bot, chat_id, msg_id, text)
        if user["role"] != "dispatch":
            await messaging.notify_dispatch(context.bot, f"{text}\n\n(par {texts.esc(user['display_name'])})")
    return None


async def weekly_reminder(context) -> None:
    """Lundi 6h05 : rappel au dispatch de clôturer la semaine."""
    if sheets.enabled():
        await messaging.notify_dispatch(context.bot, texts.CLOTURE_REMINDER)
