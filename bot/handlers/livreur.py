"""Côté livreur : service, prise de course (verrou), livraison, enchaînement, annulation (§8–§11.1)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common, relay
from bot.services import broadcast, lifecycle
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
