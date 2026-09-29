"""/recharge : chargement, reprise et cash récupéré auprès d'un livreur (ravitailleur ou dispatch).

Tout se fait par boutons dans un seul message ; le brouillon est gardé dans l'état
de conversation « restocking » (30 min). À la validation : ligne en base, livreur
prévenu, dispatch prévenu (si c'est un ravitailleur), envoi au tableau Rechargement."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import order_edit, restock, stock
from bot.timeutil import now_utc

log = logging.getLogger(__name__)

STATE = "restocking"
STATE_MINUTES = 30
ROLES = ("ravitailleur", "dispatch", "franchise")
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _actor(update: Update) -> dict | None:
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] not in ROLES:
        return None
    return user


async def _save(user: dict, payload: dict) -> None:
    await db.set_state(user["id"], STATE, payload, now_utc() + timedelta(minutes=STATE_MINUTES))


async def _render(context, chat_id: int, payload: dict, picker_page: int | None = None) -> None:
    step = payload["step"]
    name = payload.get("livreur_name") or "?"
    if step == "kind":
        text, markup = texts.restock_choose_kind(name), keyboards.restock_kind()
    elif step == "box":
        text, markup = texts.restock_choose_box(name, payload["kind"]), keyboards.restock_boxes(config.get().boxes)
    elif step == "cash":
        text = texts.restock_editor(name, payload["kind"], payload.get("box"), payload["items"], payload["cash"])
        markup = keyboards.restock_cash()
    elif picker_page is not None:
        products = await db.list_products()
        pages = max(1, -(-len(products) // order_edit.PAGE_SIZE))
        text = texts.restock_picker(name, picker_page, pages)
        markup = keyboards.restock_picker(products, picker_page, order_edit.PAGE_SIZE)
    else:
        text = texts.restock_editor(name, payload["kind"], payload.get("box"), payload["items"], payload["cash"])
        markup = keyboards.restock_editor(payload["kind"], payload["items"])
    await messaging.edit(context.bot, chat_id, payload.get("msg"), text, markup)


# ================================================================ commande

async def recharge(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if user["role"] not in ROLES:
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    livreurs = await db.list_users(role="livreur", status="active")
    if not livreurs:
        await messaging.reply(update, texts.RESTOCK_NO_LIVREUR)
        return
    livreurs.sort(key=lambda u: u.get("display_name") or "")
    sent = await messaging.reply(update, texts.RESTOCK_CHOOSE_LIVREUR, keyboards.restock_livreurs(livreurs))
    await _save(user, {"step": "livreur", "items": [], "cash": 0, "msg": sent.message_id if sent else None})


# ================================================================ boutons

@common.callback
async def action(update: Update, context):
    user = await _actor(update)
    if user is None:
        return None
    data = update.callback_query.data or ""
    act, _, rest = data.partition(":")
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    if act == "rs_noop":
        return None
    async with _locks[user["id"]]:
        user = await db.get_user(user["id"])
        state, payload = common.active_state(user)
        if state != STATE:
            await messaging.edit_markup(context.bot, chat_id, msg_id, None)
            return texts.RESTOCK_EXPIRED, True
        payload["msg"] = msg_id

        if act == "rs_x":
            await db.clear_state(user["id"])
            await messaging.edit(context.bot, chat_id, msg_id, texts.RESTOCK_CANCELLED)
            return None

        if act == "rs_l":
            livreur = await db.get_user(rest)
            if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
                return texts.ALREADY_HANDLED, True
            payload.update(step="kind", livreur_id=livreur["id"], livreur_name=livreur["display_name"])
        elif act == "rs_k" and rest in restock.KINDS:
            payload["kind"] = rest
            payload["step"] = "items" if rest == "cash" else "box"
        elif act == "rs_b":
            boxes = config.get().boxes
            idx = int(rest)
            if not 0 <= idx < len(boxes):
                return None
            payload.update(box=boxes[idx], step="items")
        elif act == "rs_q":
            idx, delta = (int(x) for x in rest.split(":"))
            payload["items"] = restock.change_qty(payload["items"], idx, delta)
        elif act == "rs_add":
            if not await db.list_products():
                return texts.ORDER_EDIT_NO_CATALOG, True
            payload["step"] = "items"
            await _save(user, payload)
            await _render(context, chat_id, payload, picker_page=max(0, int(rest or 0)))
            return None
        elif act == "rs_pick":
            product = await db.get_product(int(rest))
            if product is None:
                return texts.ORDER_EDIT_NO_CATALOG, True
            payload["items"] = restock.add_product(payload["items"], product["name"])
        elif act == "rs_c":
            payload["step"] = "cash"
        elif act == "rs_cp":
            payload["cash"] = restock.change_cash(payload.get("cash") or 0, float(rest))
        elif act == "rs_back":
            payload["step"] = "items"
        elif act == "rs_ok":
            return await _validate(context, chat_id, user, payload)
        else:
            return None
        await _save(user, payload)
        await _render(context, chat_id, payload)
    return None


async def _validate(context, chat_id: int, user: dict, payload: dict):
    if payload.get("step") in ("livreur", "kind", "box") or not payload.get("livreur_id"):
        return None
    if restock.is_empty(payload):
        return texts.RESTOCK_EMPTY, True
    livreur = await db.get_user(payload["livreur_id"])
    if livreur is None:
        await db.clear_state(user["id"])
        return texts.ALREADY_HANDLED, True
    kind = payload["kind"]
    row = await db.create_restock({
        "livreur_id": livreur["id"], "by_user_id": user["id"], "kind": kind,
        "box": payload.get("box") if kind != "cash" else None,
        "items": payload["items"] if kind != "cash" else [],
        "cash": float(payload.get("cash") or 0),
    })
    await db.clear_state(user["id"])
    await messaging.edit(context.bot, chat_id, payload["msg"], texts.restock_done(row, livreur["display_name"]))
    await db.log_event("restock", user_id=user["id"], payload={"restock_id": row["id"], "livreur_id": livreur["id"]})
    await messaging.send(context.bot, livreur, texts.restock_for_livreur(row))
    if user["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, texts.d_restock(row, user, livreur))
    stock.push_restock_later(row)
    return "Enregistré ✅"


async def on_message(update: Update, context, user: dict, state: str | None, payload: dict) -> None:
    """Texte d'un ravitailleur (ou du dispatch) pendant un rechargement : montant du cash."""
    if state != STATE:
        await messaging.reply(update, texts.welcome(user["role"]))
        return
    if payload.get("step") != "cash" and payload.get("kind") != "cash":
        await messaging.reply(update, texts.RESTOCK_USE_BUTTONS)
        return
    amount = order_edit.parse_price(update.message.text or "")
    if amount is None:
        await messaging.reply(update, texts.RESTOCK_CASH_HINT)
        return
    async with _locks[user["id"]]:
        payload["cash"] = restock.change_cash(0, amount)
        payload["step"] = "items"
        await _save(user, payload)
        await _render(context, update.effective_chat.id, payload)
