"""Stock déclaré par le livreur : au début de son service (une fois par nuit) et à sa fin de service.
Tant que son stock de début de service n'est pas confirmé, il ne reçoit aucune course (services/declare.py,
blocked_ids) ; dès qu'il confirme, les courses en attente lui sont proposées.

Le bot lui montre le stock du tableau et lui demande ce qu'il a vraiment sur lui : il touche ✅ si c'est
exact, sinon il l'écrit (« 3 US, 2 DIV », « rien ») puis confirme. Le dispatch reçoit la déclaration et
les écarts avec le tableau. Sans réponse au bout de REMIND_MINUTES : une relance au livreur, et le
dispatch est prévenu (une seule fois)."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import declare, sheets, stock
from bot.timeutil import iso, night_start_date, now_utc

log = logging.getLogger(__name__)

STATE = "stock_declare"
STATE_HOURS = 12
looks_like_stock = declare.looks_like_stock
REMIND_MINUTES = 10


def current_night() -> date:
    return night_start_date(now_utc(), config.get().night_end_hour)


async def _expected(livreur: dict) -> dict[str, int] | None:
    """Stock du livreur d'après le tableau (et les mouvements pas encore écrits) ; None si illisible."""
    if not sheets.enabled():
        return None
    try:
        have, _ = await stock.current_stock(livreur)
    except Exception:  # noqa: BLE001 — la feuille ne doit jamais bloquer la prise de service
        log.warning("Stock de %s illisible pour la déclaration", livreur.get("display_name"))
        return None
    return None if have is None else declare.positive(have)


async def _prompt(bot, livreur: dict, decl: dict, reminder: bool = False) -> None:
    sent = await messaging.send(bot, livreur, texts.declare_prompt(decl["kind"], decl.get("expected"), reminder),
                                keyboards.declare_prompt(decl["id"], decl.get("expected") is not None))
    await db.set_state(livreur["id"], STATE, {"id": decl["id"], "msg": sent.message_id if sent else None},
                       now_utc() + timedelta(hours=STATE_HOURS))


async def ask(bot, livreur: dict, kind: str, night: date | None = None) -> None:
    """Demande au livreur son stock (kind : « start » ou « end »), une fois par nuit. Déjà confirmé : rien ;
    demandé mais pas confirmé : la question est reposée. Ne lève jamais d'exception."""
    try:
        if not config.get().stock_declaration:
            return
        if not livreur or livreur.get("role") != "livreur" or not livreur.get("telegram_id"):
            return
        night = night or current_night()
        decl = await db.find_declaration(livreur["id"], kind, night)
        if decl and decl["status"] == "confirmed":
            return
        if decl is None:
            decl = await db.create_declaration({
                "livreur_id": livreur["id"], "livreur_name": livreur.get("display_name"), "kind": kind,
                "night": night.isoformat(), "expected": await _expected(livreur)})
        await _prompt(bot, livreur, decl)
    except Exception:  # noqa: BLE001
        log.exception("Déclaration de stock impossible à demander")


async def on_message(update: Update, context, user: dict, payload: dict) -> None:
    """Le livreur écrit son stock : aperçu avec ✅ Confirmer / ✏️ Corriger."""
    decl = await db.get_declaration(int(payload.get("id") or 0))
    if decl is None or decl["status"] == "confirmed" or decl["livreur_id"] != user["id"]:
        await db.clear_state(user["id"])
        await messaging.reply(update, texts.LIVREUR_TEXT_HINT)
        return
    parsed = declare.parse(update.message.text or "", await sheets.load_catalog())
    if parsed.errors:
        await messaging.reply(update, texts.declare_errors(parsed.errors))
        return
    sent = await messaging.reply(update, texts.declare_preview(decl["kind"], parsed.items, decl.get("expected")),
                                 keyboards.declare_confirm(decl["id"]))
    await db.set_state(user["id"], STATE, {"id": decl["id"], "items": parsed.items,
                                           "msg": sent.message_id if sent else None},
                       now_utc() + timedelta(hours=STATE_HOURS))


@common.callback
async def action(update: Update, context):
    """dc_same:<id> le stock du tableau est exact · dc_ok:<id> confirmer ce qu'il a écrit · dc_fix:<id> corriger."""
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] != "livreur":
        return None
    act, _, raw = (update.callback_query.data or "").partition(":")
    decl = await db.get_declaration(int(raw or 0))
    chat_id, msg_id = update.effective_chat.id, update.callback_query.message.message_id
    if decl is None or decl["livreur_id"] != user["id"] or decl["status"] == "confirmed":
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.DECLARE_DONE_ALREADY, True
    if act == "dc_fix":
        await messaging.edit(context.bot, chat_id, msg_id, texts.declare_prompt(decl["kind"], decl.get("expected")),
                             keyboards.declare_prompt(decl["id"], decl.get("expected") is not None))
        await db.set_state(user["id"], STATE, {"id": decl["id"], "msg": msg_id}, now_utc() + timedelta(hours=STATE_HOURS))
        return "Écris ce que tu as sur toi"
    if act == "dc_same":
        if decl.get("expected") is None:
            return texts.DECLARE_WRITE_IT, True
        items = dict(decl["expected"])
    else:
        payload = await common.claim_state(user, STATE, msg_id)
        if payload is None or payload.get("id") != decl["id"] or payload.get("items") is None:
            await messaging.edit_markup(context.bot, chat_id, msg_id, None)
            return texts.DECLARE_DONE_ALREADY, True
        items = payload["items"]
    done = await db.update_declaration(decl["id"], {"status": "confirmed", "items": items,
                                                    "confirmed_at": iso(now_utc())}, only_if_asked=True)
    if done is None:                                   # deux appuis : la première a déjà confirmé
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.DECLARE_DONE_ALREADY, True
    if user.get("conversation_state") == STATE:
        await db.clear_state(user["id"])
    await db.log_event("stock_declared", user_id=user["id"], payload={"id": decl["id"], "kind": decl["kind"]})
    await messaging.edit(context.bot, chat_id, msg_id, texts.declare_done(decl["kind"], items))
    await messaging.notify_dispatch(context.bot, texts.d_declared(user, decl["kind"], items, decl.get("expected")))
    if decl["kind"] == "start":                       # il peut maintenant recevoir les courses en attente
        from bot.services import broadcast

        await broadcast.kick_pending(context)
    return "Noté ✅"


async def remind(context) -> None:
    """Job : déclaration demandée il y a plus de REMIND_MINUTES et pas confirmée → relance au livreur,
    dispatch prévenu. Une seule fois par déclaration."""
    for decl in await db.list_unconfirmed_declarations(now_utc() - timedelta(minutes=REMIND_MINUTES)):
        await db.update_declaration(decl["id"], {"reminded": True})
        livreur = await db.get_user(decl["livreur_id"])
        if livreur is None or livreur["status"] != "active":
            continue
        await _prompt(context.bot, livreur, decl, reminder=True)
        await messaging.notify_dispatch(context.bot, texts.d_declare_missing(livreur, decl["kind"], REMIND_MINUTES))
