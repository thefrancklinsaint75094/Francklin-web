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
STATE_FIX = "ravi_fix"            # ravitailleur, livreur ou box oublié : demandé par boutons
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
        # Ravitailleur oublié : on le demande (le texte éventuel est gardé).
        choices = await _ravitailleurs(ravitailleurs)
        if not choices:
            await messaging.reply(update, texts.RAVI_WHO)
            return
        sent = await messaging.reply(update, texts.RAVI_ASK_WHO, keyboards.ravi_choices("r", None, choices))
        await _save(user, STATE_FIX, {"ask": "r", "text": body if body.strip() else None,
                                      "msg": sent.message_id if sent else None})
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
    await _next(update, None, user, {**base, "blocks": blocks, "warnings": parsed.warnings})


async def _ravitailleurs(users: list[dict] | None = None) -> list[str]:
    names = list(config.get().ravitailleur_names)
    users = users if users is not None else await db.list_users(role="ravitailleur", status="active")
    return names + [u["display_name"] for u in users if u.get("display_name") and u["display_name"] not in names]


async def _livreur_names() -> list[str]:
    names = list(config.get().livreur_names)
    if names:
        return names
    return sorted(u["display_name"] for u in await db.list_users(role="livreur", status="active")
                  if u.get("display_name"))


async def _next(update: Update, context, user: dict, payload: dict) -> None:
    """Demande ce qui manque (livreur, puis box, bloc par bloc), sinon montre l'aperçu à confirmer.
    Une question en cours est remplacée dans le même message (context donné), sinon envoyée."""
    need = restock_text.missing(payload["blocks"])
    msg_id = payload.get("msg")
    if need is None:
        text, markup, state = (texts.ravi_preview(payload["ravi"], payload["blocks"], payload.get("warnings")),
                               keyboards.ravi_confirm(), STATE_CONFIRM)
    else:
        kind, i = need
        block = payload["blocks"][i]
        if kind == "livreur":
            text, choices = texts.ravi_ask_livreur(block), await _livreur_names()
        else:
            text, choices = texts.ravi_ask_box(block), list(config.get().boxes)
        markup, state = keyboards.ravi_choices(kind[0], i, choices), STATE_FIX
        payload = {**payload, "ask": kind[0], "block": i}
    if context is not None and msg_id:
        await messaging.edit(context.bot, update.effective_chat.id, msg_id, text, markup)
    else:
        sent = await messaging.reply(update, text, markup)
        msg_id = sent.message_id if sent else None
    await _save(user, state, {**payload, "msg": msg_id})


@common.callback
async def fix(update: Update, context):
    """rvf:r:<n> ravitailleur · rvf:l:<bloc>:<n> livreur · rvf:b:<bloc>:<n> box · rvf:x annuler."""
    user = await common.actor(update)
    if not _allowed(user):
        return None
    chat_id, msg_id = update.effective_chat.id, update.callback_query.message.message_id
    payload = await common.claim_state(user, STATE_FIX, msg_id)
    if payload is None:
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.RAVI_EXPIRED, True
    parts = (update.callback_query.data or "").split(":")
    if parts[1] == "x":
        await messaging.edit(context.bot, chat_id, msg_id, texts.RAVI_CANCELLED)
        return None
    try:
        if parts[1] == "r":
            choice = (await _ravitailleurs())[int(parts[2])]
        else:
            i, n = int(parts[2]), int(parts[3])
            choice = (await _livreur_names() if parts[1] == "l" else list(config.get().boxes))[n]
    except (IndexError, ValueError):
        await _save(user, STATE_FIX, payload)
        return texts.ALREADY_HANDLED, True
    if parts[1] == "r":
        _, ravi_user = restock_text.resolve_ravitailleur(choice, [choice], await db.list_users(
            role="ravitailleur", status="active"))
        base = {"ravi": choice, "ravi_id": ravi_user["id"] if ravi_user else None}
        if payload.get("text"):
            await messaging.edit_markup(context.bot, chat_id, msg_id, None)
            await _process(update, user, base, payload["text"], skip_first=False)
        else:
            await messaging.edit(context.bot, chat_id, msg_id, texts.ravi_help(choice))
            await _save(user, STATE_INPUT, base)
        return choice
    blocks = payload["blocks"]
    if parts[1] == "l":
        from bot.services.sales import resolve_livreur

        name, lv = resolve_livreur(choice, list(config.get().livreur_names),
                                   await db.list_users(role="livreur", status="active"))
        blocks[i]["livreur"], blocks[i]["user_id"] = name or choice, lv["id"] if lv else None
    else:
        # Comme dans le texte : le box vaut aussi pour les livreurs suivants qui n'en ont pas.
        for b in blocks[i:]:
            if not b.get("box") and (b.get("load") or b.get("unload") or b is blocks[i]):
                b["box"] = choice
    await _next(update, context, user, {**payload, "blocks": blocks})
    return choice


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
    payload = await common.claim_state(user, STATE_CONFIRM, msg_id)
    if payload is None:
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.RAVI_EXPIRED, True
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
            await messaging.notify_dispatch(
                context.bot, dispatch=user["role"] != "dispatch", skip=[user["id"]],
                text=texts.d_restock(row, by, {"display_name": block["livreur"]}))
            stock.push_restock_later(row)
        for item in block["load"]:                 # goûts chargés : cochés chez le livreur
            await db.add_livreur_variants(block["livreur"], item["p"], item.get("v") or [])
    await messaging.edit(context.bot, chat_id, msg_id, texts.ravi_done(payload["ravi"], payload["blocks"], len(saved)))
    return "Enregistré ✅"
