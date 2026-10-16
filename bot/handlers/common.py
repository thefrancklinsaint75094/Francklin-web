"""Outils communs aux handlers : utilisateur courant, contrôles de rôle et de statut,
réponse systématique aux callbacks."""
from __future__ import annotations

import asyncio
import functools
import logging
from collections import defaultdict

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import TelegramError

from bot import config, db, messaging, texts
from bot.timeutil import now_utc, parse_ts

log = logging.getLogger(__name__)

ADMIN_ROLES = ("dispatch", "franchise")   # le franchisé a les pleins pouvoirs, comme le dispatch


ORDER_ROLES = ("franchise", "dispatch")   # le dispatch (grand admin) passe aussi des commandes


def can_order(user: dict | None) -> bool:
    return bool(user) and user.get("status") == "active" and user.get("role") in ORDER_ROLES


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("status") == "active" and user.get("role") in ADMIN_ROLES


def is_dispatch(user: dict | None) -> bool:
    """Pouvoirs réservés au dispatch (pas aux franchisés) : accès des personnes (valider, bannir, réactiver,
    supprimer, rôle, noms), remise à zéro de la semaine et /synchro."""
    return bool(user) and user.get("status") == "active" and user.get("role") == "dispatch"


# Le ☰ ne garde que l'essentiel ; tout le reste est dans /menu (handlers/menu.py), et chaque commande
# marche toujours en la tapant.
_ADMIN_COMMANDS = [("app", "📱 L'appli Dispatch"), ("menu", "Tout le menu, par chapitre"), ("encours", "Ce soir : en attente, en cours, livrées"),
                   ("livreurs", "Mettre un livreur en service / pause"), ("users", "Fiches des utilisateurs")]

COMMANDS = {
    "franchise": [("mescourses", "Mes courses de la nuit"), ("modele", "Modèle de commande")] + _ADMIN_COMMANDS,
    "livreur": [("dispo", "Me mettre en service"), ("pause", "Me retirer temporairement"),
                ("app", "📱 Mon appli Mes courses"), ("macourse", "Revoir ma course en cours"), ("depense", "Noter une dépense"),
                ("macaisse", "Mon cash à remettre"), ("gouts", "Mes goûts (ce qu'il me reste)"),
                ("close", "Fin de service : mon récap")],
    "dispatch": _ADMIN_COMMANDS + [("recap", "Totaux par livreur")],
    "ravitailleur": [("app", "📱 Mon appli Ravitaillement"), ("recharge", "Charger / reprendre un livreur, cash"), ("ravi", "Rechargement en texte"), ("swipe", "Transfert entre livreurs"), ("stock", "Stock des box et des livreurs"),
                     ("caisse", "Cash à récupérer chez les livreurs"), ("gouts", "Goûts chez les livreurs")],
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


_claim_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def claim_state(user: dict, state: str, msg_id: int) -> dict | None:
    """Bouton ✅ / ❌ d'un aperçu : prend l'état (et l'efface) une seule fois. Deux appuis rapprochés sur le
    même bouton : le second attend, relit l'état déjà effacé et reçoit None (« expiré »), au lieu
    d'enregistrer deux fois la même chose."""
    async with _claim_locks[user["id"]]:
        fresh = await db.get_user(user["id"]) or user
        current, payload = active_state(fresh)
        if current != state or payload.get("msg") != msg_id:
            return None
        await db.clear_state(user["id"])
        return payload


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
    if role and user["role"] != role and not (role == "franchise" and user["role"] == "dispatch"):
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
    await set_menu_button(bot, user)


async def set_menu_button(bot, user: dict) -> None:
    """Admins : le bouton à gauche du champ de saisie ouvre la mini-application « Dispatch » ; livreurs :
    leur appli « Mes courses » ; les autres rôles : la liste des commandes."""
    from telegram import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

    url = config.get().webapp_url
    if not url:
        return
    if is_admin(user):
        button = MenuButtonWebApp("Dispatch", WebAppInfo(url))
    elif user.get("role") == "livreur" and user.get("status") == "active":
        button = MenuButtonWebApp("Mes courses", WebAppInfo(f"{url}/livreur"))
    elif user.get("role") == "ravitailleur" and user.get("status") == "active":
        button = MenuButtonWebApp("Ravitaillement", WebAppInfo(f"{url}/ravi"))
    else:
        button = MenuButtonCommands()
    try:
        await bot.set_chat_menu_button(chat_id=user["telegram_id"], menu_button=button)
    except TelegramError as exc:
        log.info("Bouton de menu impossible : %s", exc)


async def refresh_all_commands(bot) -> None:
    """Au démarrage : le bouton « Menu » de chacun liste toutes les commandes de son rôle, y compris les
    nouvelles (sinon il garde la liste de son dernier /start)."""
    try:
        users = [u for u in await db.list_users(status="active") if u.get("telegram_id")]
    except Exception as exc:  # noqa: BLE001
        log.warning("Menus non actualisés : %s", exc)
        return
    for user in users:
        await set_commands(bot, user)


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
