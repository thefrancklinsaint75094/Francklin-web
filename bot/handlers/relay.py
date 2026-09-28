"""Messagerie relais (§12) : chaque message est rattaché à une course en cours.

L'état « relaying » est stocké en base (users.conversation_state) pour survivre
à un redémarrage."""
from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update

from bot import db, keyboards, messaging, texts
from bot.handlers import common
from bot.timeutil import now_utc

log = logging.getLogger(__name__)

RELAY_STATE_MINUTES = 5


def _is_party(course: dict, user: dict) -> bool:
    return user["id"] in (course.get("franchise_id"), course.get("livreur_id"))


@common.callback
async def start(update: Update, context):
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] not in ("franchise", "livreur"):
        return None
    course = await db.get_course(common.arg(update, int))
    if course is None or not _is_party(course, user):
        return texts.COURSE_FINISHED, True
    if course["status"] != "assigned":
        await messaging.reply(update, texts.COURSE_FINISHED)
        return None
    await db.set_state(user["id"], "relaying", {"course_id": course["id"]},
                       now_utc() + timedelta(minutes=RELAY_STATE_MINUTES))
    await messaging.reply(update, texts.relay_prompt(course["id"]), keyboards.relay_cancel())
    return None


@common.callback
async def cancel(update: Update, context):
    user = await common.actor(update)
    if user is None or user["status"] != "active":
        return None
    if user.get("conversation_state") == "relaying":
        await db.clear_state(user["id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.RELAY_CANCELLED)
    return None


async def course_for_reply(user: dict, replied_message_id: int) -> int | None:
    """Course visée par une réponse native (« Répondre ») à un message relayé ou à une fiche."""
    for m in await db.find_messages_by_relayed_id(replied_message_id):
        if m["sender_id"] == user["id"]:
            continue
        course = await db.get_course(m["course_id"])
        if course and _is_party(course, user):
            return course["id"]
    course = await db.find_course_by_message(replied_message_id, user)
    return course["id"] if course else None


async def relay_text(update: Update, context, user: dict, course_id: int | None, content: str) -> None:
    if user.get("conversation_state") == "relaying":
        await db.clear_state(user["id"])
    course = await db.get_course(course_id) if course_id else None
    if course is None or not _is_party(course, user) or course["status"] != "assigned":
        await messaging.reply(update, texts.COURSE_FINISHED)
        return
    other_id = course["livreur_id"] if user["id"] == course["franchise_id"] else course["franchise_id"]
    other = await db.get_user(other_id)
    if other is None or other["status"] == "banned":
        await messaging.reply(update, texts.COURSE_FINISHED)
        return
    row = await db.add_message(course["id"], user["id"], content)
    sent = await messaging.send(
        context.bot, other, texts.relayed_message(course, user["display_name"], content),
        keyboards.relay_reply(course["id"]),
    )
    if sent is not None:
        await db.set_message_relayed_id(row["id"], sent.message_id)
    await db.log_event("relay_message", course["id"], user["id"], {"message_id": row["id"]})
    await messaging.reply(update, texts.RELAY_SENT)
