"""Outils communs aux handlers : utilisateur courant, contrôles de rôle et de statut,
réponse systématique aux callbacks."""
from __future__ import annotations

import functools
import logging

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import TelegramError

from bot import db, messaging, texts
from bot.timeutil import now_utc, parse_ts

log = logging.getLogger(__name__)

ADMIN_ROLES = ("dispatch", "franchise")   # le franchisé a les pleins pouvoirs, comme le dispatch


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("status") == "active" and user.get("role") in ADMIN_ROLES


_ADMIN_COMMANDS = [("encours", "Courses en attente et en cours"), ("livreurs", "Mettre un livreur en service / pause"),
                   ("recap", "Totaux par livreur"), ("journal", "Détail des courses livrées"),
                   ("users", "Tous les utilisateurs"), ("recharge", "Charger / reprendre un livreur"), ("ravi", "Rechargement en texte (/ravi 1)"), ("swipe", "Transfert entre livreurs"),
                   ("stock", "Stock des box et des livreurs"), ("caisse", "Cash à récupérer chez les livreurs"),
                   ("depense", "Noter une dépense d'un livreur"),
                   ("synchro", "Renvoyer la nuit vers Google Sheets"), ("close", "Débrief de la journée"), ("ventes", "Ajouter un récap de ventes au tableau"), ("reset", "Remise à zéro de la semaine (archives)"), ("bannir", "Bannir quelqu'un (plus aucun accès)"), ("supprimer", "Supprimer un compte (définitif)"), ("prenom", "Prénoms des livreurs"),
                   ("reactiver", "Rendre un accès")]

COMMANDS = {
    "franchise": [("mescourses", "Mes courses de la nuit"), ("modele", "Modèle de commande"),
                  ("produits", "Catalogue des produits")] + _ADMIN_COMMANDS,
    "livreur": [("dispo", "Me mettre en service"), ("pause", "Me retirer temporairement"),
                ("macourse", "Revoir ma course en cours"), ("depense", "Noter une dépense"),
                ("macaisse", "Mon cash à remettre")],
    "dispatch": _ADMIN_COMMANDS + [("produits", "Catalogue des produits")],
    "ravitailleur": [("recharge", "Charger / reprendre un livreur, cash"), ("ravi", "Rechargement en texte"), ("swipe", "Transfert entre livreurs"), ("stock", "Stock des box et des livreurs"),
                     ("caisse", "Cash à récupérer chez les livreurs")],
}
COMMON_COMMANDS = [("start", "Démarrer"), ("aide", "Aide")]


async def actor(update: Update) -> dict | None:
    """Utilisateur en base correspondant à l'update ; rafraîchit son @pseudo."""
    tg = update.effective_user
    if tg is None:
        return None
    user = await db.get_user_by_tg(tg.id)
    if user is None:
        return None
    messaging.unblocked(tg.id)
    if user.get("telegram_username") != tg.username and user["status"] != "banned":
        user = await db.update_user(user["id"], {"telegram_username": tg.username}) or user
    return user


def active_state(user: dict) -> tuple[str | None, dict]:
    """État de conversation non expiré (sinon (None, {}))."""
    state = user.get("conversation_state")
    if not state:
        return None, {}
    expires = parse_ts(user.get("state_expires_at"))
    if expires is not None and expires < now_utc():
        return None, {}
    return state, user.get("state_payload") or {}


async def require(update: Update, user: dict | None, role: str | None = None, silent: bool = False) -> bool:
    """Contrôle en tête de handler. Un banni ne reçoit jamais de réponse."""
    if user is None:
        if not silent:
            await messaging.reply(update, texts.START_TO_REGISTER)
        return False
    if user["status"] == "banned":
        return False
    if user["status"] == "pending":
        if not silent:
            await messaging.reply(update, texts.PENDING)
        return False
    if role and user["role"] != role:
        if not silent:
            await messaging.reply(update, texts.NOT_FOR_YOU)
        return False
    return True


async def set_commands(bot, user: dict) -> None:
    cmds = COMMANDS.get(user["role"], []) + COMMON_COMMANDS
    try:
        await bot.set_my_commands([BotCommand(c, d) for c, d in cmds],
                                  scope=BotCommandScopeChat(chat_id=user["telegram_id"]))
    except TelegramError as exc:
        log.warning("set_my_commands impossible : %s", exc)


async def clear_commands(bot, user: dict) -> None:
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user["telegram_id"]))
    except TelegramError as exc:
        log.info("delete_my_commands impossible : %s", exc)


def callback(func):
    """Décorateur des handlers de boutons : garantit un answer_callback_query.

    Le handler peut renvoyer None, un texte (notification discrète) ou
    (texte, True) pour une alerte."""

    @functools.wraps(func)
    async def wrapper(update: Update, context):
        q = update.callback_query
        result = None
        try:
            result = await func(update, context)
        finally:
            text, alert = None, False
            if isinstance(result, tuple):
                text, alert = result
            elif isinstance(result, str):
                text = result
            try:
                await q.answer(text=text, show_alert=alert)
            except TelegramError:
                pass

    return wrapper


def arg(update: Update, cast=str):
    """Identifiant après le « : » du callback_data."""
    data = update.callback_query.data or ""
    value = data.split(":", 1)[1] if ":" in data else ""
    return cast(value)
