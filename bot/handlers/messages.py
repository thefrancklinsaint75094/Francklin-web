"""Aiguillage des messages ordinaires (hors commandes et positions) selon le rôle.

Ordre de vérification : (1) statut de l'utilisateur, (2) état de conversation
non expiré, (3) traitement propre au rôle."""
from __future__ import annotations

from telegram import Update

from bot import config, messaging, texts
from bot.handlers import common, franchise, livreur, onboarding, restock


async def on_message(update: Update, context) -> None:
    if update.message is None:
        return
    user = await common.actor(update)
    if user is None:
        if update.effective_user.id == config.get().dispatch_telegram_id:
            await messaging.reply(update, "Envoie /start pour activer le dispatch.")
        else:
            await messaging.reply(update, texts.START_TO_REGISTER)
        return
    if user["status"] == "banned":
        return
    if user["status"] == "pending":
        if user.get("conversation_state") == "awaiting_name" and update.message.text:
            await onboarding.receive_name(update, context, user)
        else:
            await messaging.reply(update, texts.PENDING)
        return
    state, payload = common.active_state(user)
    if user["role"] == "ravitailleur" or (user["role"] == "dispatch" and state == restock.STATE):
        await restock.on_message(update, context, user, state, payload)
        return
    if user["role"] == "dispatch":
        if state == "adding_products" and update.message.text:
            from bot.handlers import dispatch

            await dispatch.save_products(update, user, update.message.text)
        else:
            await messaging.reply(update, texts.WELCOME_DISPATCH)
        return
    if user["role"] == "franchise":
        await franchise.on_message(update, context, user, state, payload)
    else:
        await livreur.on_message(update, context, user, state, payload)
