"""Caisse : /depense (livreur pour lui-même, admin pour un livreur), /caisse (admins et
ravitailleurs : cash à récupérer, 💶 Récupérer ouvre /recharge en « cash seulement » prérempli)
et /macaisse (le livreur voit ce qu'il doit remettre).

La dépense se saisit par boutons dans un seul message (état « expense », 30 min) :
[livreur] → Charges / Paye → montant (boutons ou tapé) → motif (boutons ou tapé) → Enregistrer."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import timedelta

from telegram import Update

from bot import db, keyboards, messaging, texts
from bot.handlers import common
from bot.handlers import restock as restock_h
from bot.services import cash, order_edit
from bot.timeutil import now_utc

STATE = "expense"
STATE_MINUTES = 30
CASH_ROLES = ("dispatch", "franchise", "ravitailleur")
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _can_expense(user: dict | None) -> bool:
    return bool(user) and user["status"] == "active" and (user["role"] == "livreur" or common.is_admin(user))


def _can_see_cash(user: dict | None) -> bool:
    return bool(user) and user["status"] == "active" and user["role"] in CASH_ROLES


async def _save(user: dict, payload: dict) -> None:
    await db.set_state(user["id"], STATE, payload, now_utc() + timedelta(minutes=STATE_MINUTES))


async def _render(context, chat_id: int, payload: dict) -> None:
    await messaging.edit(context.bot, chat_id, payload.get("msg"), texts.expense_step(payload),
                         keyboards.expense_step(payload["step"]))


# ================================================================ /depense

async def depense(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not _can_expense(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    if user["role"] == "livreur":
        payload = {"step": "kind", "livreur_id": user["id"], "livreur_name": user["display_name"], "amount": 0}
        sent = await messaging.reply(update, texts.expense_step(payload), keyboards.expense_step("kind"))
    else:
        livreurs = await db.list_users(role="livreur", status="active")
        if not livreurs:
            await messaging.reply(update, texts.RESTOCK_NO_LIVREUR)
            return
        livreurs.sort(key=lambda u: u.get("display_name") or "")
        payload = {"step": "livreur", "amount": 0}
        sent = await messaging.reply(update, texts.EXPENSE_CHOOSE_LIVREUR, keyboards.expense_livreurs(livreurs))
    payload["msg"] = sent.message_id if sent else None
    await _save(user, payload)


@common.callback
async def action(update: Update, context):
    user = await common.actor(update)
    if not _can_expense(user):
        return None
    act, _, rest = (update.callback_query.data or "").partition(":")
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    async with _locks[user["id"]]:
        user = await db.get_user(user["id"])
        state, payload = common.active_state(user)
        if state != STATE:
            await messaging.edit_markup(context.bot, chat_id, msg_id, None)
            return texts.EXPENSE_EXPIRED, True
        payload["msg"] = msg_id

        if act == "dp_x":
            await db.clear_state(user["id"])
            await messaging.edit(context.bot, chat_id, msg_id, texts.EXPENSE_CANCELLED)
            return None
        if act == "dp_l" and common.is_admin(user):
            livreur = await db.get_user(rest)
            if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
                return texts.ALREADY_HANDLED, True
            payload.update(step="kind", livreur_id=livreur["id"], livreur_name=livreur["display_name"])
        elif act == "dp_k" and rest in cash.KINDS and payload.get("livreur_id"):
            payload.update(kind=cash.KINDS[rest], step="amount")
        elif act == "dp_a" and payload.get("kind"):
            payload["amount"] = cash.change_amount(payload.get("amount") or 0, float(rest))
        elif act == "dp_n" and payload.get("kind"):
            if float(payload.get("amount") or 0) <= 0:
                return texts.EXPENSE_ZERO, True
            payload["step"] = "motif"
        elif act == "dp_m" and payload.get("step") == "motif":
            idx = int(rest)
            payload["motif"] = cash.MOTIFS[idx] if 0 <= idx < len(cash.MOTIFS) else ""
            payload["step"] = "confirm"
        elif act == "dp_e" and payload.get("kind"):
            payload["step"] = "amount"
        elif act == "dp_ok" and payload.get("step") == "confirm":
            return await _validate(context, chat_id, user, payload)
        else:
            return None
        await _save(user, payload)
        await _render(context, chat_id, payload)
    return None


async def _validate(context, chat_id: int, user: dict, payload: dict):
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        return texts.EXPENSE_ZERO, True
    livreur = await db.get_user(payload["livreur_id"])
    if livreur is None:
        await db.clear_state(user["id"])
        return texts.ALREADY_HANDLED, True
    row = await db.create_expense({
        "livreur_id": livreur["id"], "by_user_id": user["id"], "kind": payload["kind"],
        "amount": amount, "motif": payload.get("motif") or None,
    })
    await db.clear_state(user["id"])
    await messaging.edit(context.bot, chat_id, payload["msg"], texts.expense_done(row, livreur["display_name"]))
    await db.log_event("expense", user_id=user["id"], payload={"expense_id": row["id"], "livreur_id": livreur["id"]})
    if user["id"] != livreur["id"]:
        await messaging.send(context.bot, livreur, texts.expense_for_livreur(row, user))
    if user["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, texts.d_expense(row, user, livreur))
    cash.push_expense_later(row)
    return "Enregistré ✅"


async def on_message(update: Update, context, user: dict, payload: dict) -> None:
    """Texte pendant une dépense : montant (étape montant) ou motif (étape motif)."""
    text = update.message.text or ""
    step = payload.get("step")
    async with _locks[user["id"]]:
        if step == "amount":
            amount = order_edit.parse_price(text)
            if amount is None or amount <= 0:
                await messaging.reply(update, texts.EXPENSE_AMOUNT_HINT)
                return
            payload["amount"] = cash.change_amount(0, amount)
            payload["step"] = "motif"
        elif step == "motif" and cash.clean_motif(text):
            payload["motif"] = cash.clean_motif(text)
            payload["step"] = "confirm"
        else:
            await messaging.reply(update, texts.EXPENSE_USE_BUTTONS)
            return
        await _save(user, payload)
        await _render(context, update.effective_chat.id, payload)


# ================================================================ /caisse et /macaisse

async def _overview() -> tuple[str, object]:
    data = await cash.livreurs_now()
    if not data["ok"]:
        return texts.cash_unavailable(data["error"]), None
    rows = list(data["livreurs"].values())
    return texts.cash_overview(rows), keyboards.cash_overview(rows)


async def caisse(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not _can_see_cash(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    text, markup = await _overview()
    await messaging.reply(update, text, markup)


async def macaisse(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="livreur"):
        return
    data = await cash.livreur_now(user)
    if not data["ok"]:
        await messaging.reply(update, texts.cash_unavailable(data["error"]))
        return
    await messaging.reply(update, texts.my_cash(data["row"]))


@common.callback
async def cash_action(update: Update, context):
    """cs_ref : actualiser · cs_r:<livreur> : ouvrir /recharge « cash seulement » prérempli."""
    user = await common.actor(update)
    if not _can_see_cash(user):
        return None
    act, _, rest = (update.callback_query.data or "").partition(":")
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    if act == "cs_ref":
        text, markup = await _overview()
        await messaging.edit(context.bot, chat_id, msg_id, text, markup)
        return "Actualisé"
    if act != "cs_r":
        return None
    livreur = await db.get_user(rest)
    if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
        return texts.ALREADY_HANDLED, True
    data = await cash.livreur_now(livreur)
    amount = data["row"]["cash"] if data["ok"] else 0
    from bot.services import restock

    payload = {"step": "items", "kind": "cash", "items": [], "cash": restock.change_cash(0, amount),
               "livreur_id": livreur["id"], "livreur_name": livreur["display_name"], "msg": msg_id}
    await restock_h._save(user, payload)
    await restock_h._render(context, chat_id, payload)
    return None
