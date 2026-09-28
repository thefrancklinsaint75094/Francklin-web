"""Envois et éditions Telegram « sûrs » : ne lèvent jamais d'exception.

- Une édition qui échoue (message supprimé, trop ancien, contenu identique) est ignorée.
- Un message de plus de 47 h n'est jamais édité : on en envoie un nouveau.
- Un utilisateur qui a bloqué le bot (Forbidden) : livreur mis hors service,
  dispatch prévenu (une seule fois tant que l'utilisateur ne revient pas).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardMarkup, Message
from telegram.error import BadRequest, Forbidden, TelegramError

from bot import config, db, texts
from bot.timeutil import now_utc

log = logging.getLogger(__name__)

MAX_EDIT_AGE = timedelta(hours=47)
_blocked: set[int] = set()


def unblocked(telegram_id: int) -> None:
    _blocked.discard(telegram_id)


async def _handle_blocked(bot, user: dict) -> None:
    if user["telegram_id"] in _blocked:
        return
    _blocked.add(user["telegram_id"])
    log.info("Utilisateur %s a bloqué le bot", user.get("id"))
    try:
        if user.get("role") == "livreur":
            await db.update_user(user["id"], {"on_duty": False, "soon_free": False})
        await db.log_event("blocked_bot", user_id=user.get("id"))
    except Exception:  # noqa: BLE001
        log.exception("Impossible d'enregistrer le blocage")
    await notify_dispatch(bot, texts.d_blocked(user))


async def send(bot, user: dict, text: str, markup: InlineKeyboardMarkup | None = None, **kwargs) -> Message | None:
    try:
        return await bot.send_message(user["telegram_id"], text, reply_markup=markup, **kwargs)
    except Forbidden:
        await _handle_blocked(bot, user)
    except TelegramError as exc:
        log.warning("Envoi à %s impossible : %s", user.get("telegram_id"), exc)
    return None


async def notify_dispatch(bot, text: str, markup: InlineKeyboardMarkup | None = None, **kwargs) -> Message | None:
    try:
        return await bot.send_message(config.get().dispatch_telegram_id, text, reply_markup=markup, **kwargs)
    except TelegramError as exc:
        log.warning("Notification dispatch impossible : %s", exc)
    return None


async def edit(
    bot,
    chat_id: int,
    message_id: int | None,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    sent_at: datetime | None = None,
    user: dict | None = None,
    resend_if_old: bool = True,
) -> Message | None:
    """Édite un message. Renvoie le NOUVEAU message si on a dû en envoyer un autre
    (message trop ancien ou absent), sinon None."""
    too_old = sent_at is not None and now_utc() - sent_at > MAX_EDIT_AGE
    if message_id is None or too_old:
        if not resend_if_old:
            return None
        if user is not None:
            return await send(bot, user, text, markup)
        try:
            return await bot.send_message(chat_id, text, reply_markup=markup)
        except TelegramError as exc:
            log.warning("Envoi de remplacement impossible : %s", exc)
            return None
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except Forbidden:
        if user is not None:
            await _handle_blocked(bot, user)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.info("Édition ignorée (%s/%s) : %s", chat_id, message_id, exc)
    except TelegramError as exc:
        log.warning("Édition impossible (%s/%s) : %s", chat_id, message_id, exc)
    return None


async def edit_markup(bot, chat_id: int, message_id: int | None, markup: InlineKeyboardMarkup | None = None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except TelegramError as exc:
        log.info("Édition des boutons ignorée : %s", exc)


async def reply(update, text: str, markup: InlineKeyboardMarkup | None = None) -> Message | None:
    chat = update.effective_chat
    if chat is None:
        return None
    try:
        return await chat.send_message(text, reply_markup=markup)
    except TelegramError as exc:
        log.warning("Réponse impossible : %s", exc)
    return None
