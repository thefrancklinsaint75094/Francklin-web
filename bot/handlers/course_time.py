"""🕐 Heure souhaitée d'une course déjà envoyée (fiche course du franchisé, fiche ouverte depuis /encours).

Il écrit l'heure en quelques mots (« 23h30 », « vers minuit ») ou « - » pour l'enlever ; elle est appliquée
tout de suite. Le livreur de la course est prévenu (message + fiche à jour), les propositions encore ouvertes
sont corrigées, franchisé et dispatch prévenus. Mêmes droits que 📍 Adresse."""
from __future__ import annotations

from datetime import timedelta

from telegram import Update

from bot import db, keyboards, messaging, texts
from bot.handlers import address, common
from bot.services import lifecycle
from bot.timeutil import now_utc

STATE = "course_time"
STATE_MINUTES = 15
MAX_LEN = 40
CLEAR = ("-", "aucune", "aucun", "rien", "enlever", "supprimer")


@common.callback
async def start(update: Update, context):
    """ct:<course> — demande l'heure."""
    user = await common.actor(update)
    course = await db.get_course(common.arg(update, int))
    if not address._can_edit(user, course):
        return texts.COURSE_FINISHED, True
    await db.set_state(user["id"], STATE, {"course_id": course["id"]}, now_utc() + timedelta(minutes=STATE_MINUTES))
    await messaging.reply(update, texts.course_time_ask(course), keyboards.course_time_cancel())
    return None


@common.callback
async def cancel(update: Update, context):
    """ct_x — abandon."""
    user = await common.actor(update)
    if user is None:
        return None
    state, _ = common.active_state(user)
    if state == STATE:
        await db.clear_state(user["id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.CANCELLED_OP)
    return None


async def on_message(update: Update, context, user: dict, payload: dict) -> None:
    course = await db.get_course(int(payload.get("course_id") or 0))
    if not address._can_edit(user, course):
        await db.clear_state(user["id"])
        await messaging.reply(update, texts.COURSE_FINISHED)
        return
    value = " ".join((update.message.text or "").split())
    if value.lower() in CLEAR:
        value = None
    elif not 1 <= len(value) <= MAX_LEN:
        await messaging.reply(update, texts.COURSE_TIME_INVALID, keyboards.course_time_cancel())
        return
    await db.clear_state(user["id"])
    course = await apply(context, user, course, value)
    await messaging.reply(update, texts.course_time_done(course))


async def apply(context, user: dict, course: dict, value: str | None) -> dict:
    """Nouvelle heure souhaitée (None : enlevée) ; livreur, propositions, franchisé et dispatch à jour."""
    old = course.get("requested_time")
    course = await db.update_course(course["id"], {"requested_time": value}) or {**course, "requested_time": value}
    await db.log_event("requested_time_changed", course["id"], user["id"], {"from": old, "to": value})

    franchise = await db.get_user(course["franchise_id"])
    livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
    if course["status"] == "assigned" and livreur:
        await messaging.send(context.bot, livreur, texts.course_time_changed(course), ring=True)
        await messaging.edit(context.bot, livreur["telegram_id"], course.get("livreur_message_id"),
                             texts.full_fiche(course, franchise or {}), keyboards.livreur_course(course["id"]),
                             user=livreur)
    elif course["status"] == "pending":
        await address._refresh_proposals(context, course)
    if franchise and franchise["id"] != user["id"] and franchise["role"] != "dispatch":
        await messaging.send(context.bot, franchise, texts.course_time_changed(course, user))
    await messaging.notify_dispatch(
        context.bot, dispatch=user["role"] != "dispatch", skip=[user["id"], course["franchise_id"]],
        text=texts.course_time_changed(course, user))
    await lifecycle.refresh_franchise_message(context, course, livreur=livreur, franchise=franchise)
    return course
