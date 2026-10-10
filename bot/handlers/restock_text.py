"""/ravi (ravitailleurs et admins) : rechargements saisis en texte, au même format que /ventes.

« /ravi 1 » + les blocs dans le même message → aperçu ; « /ravi 1 » seul → exemple, puis le bot attend
le texte (15 min). ✅ : mêmes effets qu'un /recharge par boutons (ligne en base, livreur et dispatch
prévenus, tableau Rechargement rempli, stock et caisse comptés en attendant la feuille)."""
from __future__ import annotations

from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.handlers.restock import ROLES
from bot.services import restock_text, sheets, stock
from bot.timeutil import now_utc

STATE_INPUT = "ravi_input"
STATE_CONFIRM = "ravi_confirm"
STATE_MINUTES = 15


def _allowed(user: dict | None) -> bool:
    return bool(user) and user["status"] == "active" and user["role"] in ROLES


async def _save(user: dict, state: str, payload: dict) -> None:
    await db.set_state(user["id"], state, payload, now_utc() + timedelta(minutes=STATE_MINUTES))


async def ravi(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not _allowed(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    first, _, body = (update.message.text or "").partition("\n")
    arg = " ".join(first.split()[1:])
    ravitailleurs = await db.list_users(role="ravitailleur", status="active")
    if arg:
        name, ravi_user = restock_text.resolve_ravitailleur(arg, list(config.get().ravitailleur_names), ravitailleurs)
        if name is None:
            await messaging.reply(update, texts.ravi_unknown(arg))
            return
    elif user["role"] == "ravitailleur":
        name, ravi_user = user["display_name"], user
    else:
        await messaging.reply(update, texts.RAVI_WHO)
        return
    payload = {"ravi": name, "ravi_id": ravi_user["id"] if ravi_user else None}
    if body.strip():
        await _process(update, user, payload, update.message.text, skip_first=True)
        return
    await _save(user, STATE_INPUT, payload)
    await messaging.reply(update, texts.ravi_help(name))


async def on_message(update: Update, context, user: dict, payload: dict) -> None:
    await _process(update, user, payload, update.message.text or "", skip_first=False)


async def _process(update: Update, user: dict, payload: dict, text: str, skip_first: bool) -> None:
    lines = list(enumerate(text.splitlines(), 1))[1 if skip_first else 0:]
    livreurs = await db.list_users(role="livreur", status="active")
    cfg = config.get()
    parsed = restock_text.parse(lines, await sheets.load_catalog(), list(cfg.livreur_names), livreurs, cfg.boxes)
    base = {"ravi": payload.get("ravi"), "ravi_id": payload.get("ravi_id")}
    if parsed.errors or not parsed.blocks:
        await _save(user, STATE_INPUT, base)
        await messaging.reply(update, texts.ravi_errors(parsed.errors) if parsed.errors else texts.ravi_help(base["ravi"]))
        return
    blocks = [{"livreur": b.livreur, "user_id": b.user["id"] if b.user else None, "box": b.box,
               "load": b.load, "unload": b.unload, "cash": b.cash} for b in parsed.blocks]
    sent = await messaging.reply(update, texts.ravi_preview(base["ravi"], blocks, parsed.warnings),
                                 keyboards.ravi_confirm())
    await _save(user, STATE_CONFIRM, {**base, "blocks": blocks, "msg": sent.message_id if sent else None})


def _rows(block: dict, by_id: str, ravi: str) -> list[dict]:
    """Un bloc → rechargements : chargement, reprise, ou cash seul (le cash va sur le premier)."""
    common_fields = {"livreur_id": block["user_id"], "livreur_name": block["livreur"], "by_user_id": by_id,
                     "ravitailleur_name": ravi}
    rows = []
    for kind in ("load", "unload"):
        if block[kind]:
            rows.append({**common_fields, "kind": kind, "box": block["box"], "items": block[kind], "cash": 0})
    if not rows:
        rows.append({**common_fields, "kind": "cash", "box": None, "items": [], "cash": 0})
    rows[0]["cash"] = float(block["cash"] or 0)
    return rows


@common.callback
async def action(update: Update, context):
    """rv_ok : enregistrer · rv_x : annuler."""
    user = await common.actor(update)
    if not _allowed(user):
        return None
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    state, payload = common.active_state(user)
    if state != STATE_CONFIRM or payload.get("msg") != msg_id:
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.RAVI_EXPIRED, True
    await db.clear_state(user["id"])
    if update.callback_query.data == "rv_x":
        await messaging.edit(context.bot, chat_id, msg_id, texts.RAVI_CANCELLED)
        return None
    by_id = payload.get("ravi_id") or user["id"]
    by = {"display_name": payload["ravi"]}
    saved = []
    for block in payload["blocks"]:
        livreur = await db.get_user(block["user_id"]) if block["user_id"] else None
        for fields in _rows(block, by_id, payload["ravi"]):
            row = await db.create_restock(fields)
            saved.append(row)
            await db.log_event("restock", user_id=user["id"],
                               payload={"restock_id": row["id"], "livreur": block["livreur"], "text": True})
            if livreur:
                await messaging.send(context.bot, livreur, texts.restock_for_livreur(row))
            if user["role"] != "dispatch":
                await messaging.notify_dispatch(context.bot, texts.d_restock(row, by, {"display_name": block["livreur"]}))
            stock.push_restock_later(row)
        for item in block["load"]:                 # goûts chargés : cochés chez le livreur
            await db.add_livreur_variants(block["livreur"], item["p"], item.get("v") or [])
    await messaging.edit(context.bot, chat_id, msg_id, texts.ravi_done(payload["ravi"], payload["blocks"], len(saved)))
    return "Enregistré ✅"
