"""Positions des livreurs : première position (nouveau message) et mises à jour
de la position en direct (edited_message)."""
from __future__ import annotations

import logging

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import arrival, broadcast, transport
from bot.services.distance import haversine_m
from datetime import timedelta

from bot.timeutil import iso, now_utc

log = logging.getLogger(__name__)


async def on_location(update: Update, context) -> None:
    msg = update.effective_message
    edited = update.edited_message is not None
    user = await common.actor(update)
    if user is None:
        if not edited:
            await messaging.reply(update, texts.START_TO_REGISTER)
        return
    if user["status"] == "banned":
        return
    if user["status"] == "pending":
        if not edited:
            await messaging.reply(update, texts.PENDING)
        return
    if user["role"] != "livreur":
        if not edited and user["role"] == "franchise":
            await messaging.reply(update, texts.ONLY_TEXT_OR_VOICE)
        return

    loc = msg.location
    previous = await db.get_position(user["id"])
    # En direct ou fixe, et jusqu'à quand (durée choisie dans Telegram ; 0x7FFFFFFF = sans fin).
    extra = {"live": bool(loc.live_period)}
    if not edited:
        forever = not loc.live_period or loc.live_period >= 0x7FFFFFFF
        extra["live_until"] = None if forever else iso(now_utc() + timedelta(seconds=loc.live_period))
    await db.upsert_position(user["id"], loc.latitude, loc.longitude, **extra)
    # Le livreur approche d'une adresse : franchisé et dispatch prévenus (une fois par course).
    await arrival.check(context, user, loc.latitude, loc.longitude, previous)
    # Métro, véhicule, à pied : comparé au moyen de déplacement déclaré.
    await transport.observe(context, user, loc.latitude, loc.longitude, previous, now_utc(), live_update=edited)
    became_available = False
    if not edited:
        if not user.get("on_duty"):
            user = await db.update_user(user["id"], {"on_duty": True}) or user
            await db.log_event("livreur_on_duty", user_id=user["id"], payload={"live": bool(loc.live_period)})
            became_available = True
            # Mode de déplacement pas encore choisi : les boutons sous le message.
            markup = None if user.get("transport_mode") else keyboards.transport_mode(None)
            await messaging.reply(update, texts.ON_DUTY, markup)
        if not loc.live_period:
            await messaging.reply(update, texts.STATIC_POSITION_WARNING)
            await messaging.notify_dispatch(context.bot, texts.d_static_position(user))

    # Détection automatique « bientôt libre » (§10).
    if not user.get("soon_free"):
        assigned = await db.list_assigned_for_livreur(user["id"])
        if len(assigned) == 1:
            c = assigned[0]
            if haversine_m(loc.latitude, loc.longitude, c["lat"], c["lon"]) < config.get().soon_free_radius_meters:
                await db.update_user(user["id"], {"soon_free": True})
                await db.log_event("soon_free", c["id"], user["id"], {"auto": True})
                await messaging.send(context.bot, user, texts.SOON_FREE_AUTO)
                became_available = became_available or bool(user.get("on_duty"))

    if became_available:
        await broadcast.kick_pending(context)
