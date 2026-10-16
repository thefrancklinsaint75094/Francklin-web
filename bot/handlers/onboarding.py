"""/start, /aide, inscription et validation par le dispatch (§5)."""
from __future__ import annotations

import logging

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import names

log = logging.getLogger(__name__)

MAX_NAME = 80
NEWCOMER_ALERT_SECONDS = 3600
_newcomers: dict[int, float] = {}        # telegram_id → dernière alerte (une par heure et par personne)


async def newcomer(update: Update, context, what: str) -> None:
    """Une personne inconnue du bot écrit ou ouvre le bot : trace dans les journaux et dispatch prévenu (une
    fois par heure), pour savoir si son appui arrive bien jusqu'au bot."""
    import time

    tg = update.effective_user
    if tg is None:
        return
    log.info("Personne inconnue (%s) : id %s @%s %s", what, tg.id, tg.username, tg.full_name)
    now = time.monotonic()
    if now - _newcomers.get(tg.id, -NEWCOMER_ALERT_SECONDS) < NEWCOMER_ALERT_SECONDS:
        return
    _newcomers[tg.id] = now
    await messaging.notify_dispatch(context.bot, texts.d_newcomer(tg.full_name, tg.username, what), team=False,
                                    important=True)


async def start(update: Update, context) -> None:
    tg = update.effective_user
    cfg = config.get()
    if tg.id == cfg.dispatch_telegram_id:
        user = await db.get_user_by_tg(tg.id)
        if user is None:
            user = await db.create_user({
                "telegram_id": tg.id, "telegram_username": tg.username, "real_name": tg.full_name,
                "display_name": "Dispatch", "role": "dispatch", "status": "active",
            })
            await db.log_event("user_registered", user_id=user["id"], payload={"role": "dispatch"})
        await common.set_commands(context.bot, user)
        await messaging.reply(update, texts.WELCOME_DISPATCH)
        return

    user = await common.actor(update)
    if user is None:
        await messaging.reply(update, texts.ASK_ROLE, keyboards.role_choice())
        await newcomer(update, context, "/start")
        return
    if user["status"] == "banned":
        return
    if user["status"] == "pending":
        if user.get("conversation_state") == "awaiting_name":
            await messaging.reply(update, texts.ASK_NAME)
        else:
            await messaging.reply(update, texts.PENDING)
        return
    await common.set_commands(context.bot, user)
    await messaging.reply(update, texts.welcome(user["role"]), _menu(user))


def _menu(user: dict):
    """Livreur : le menu de gros boutons en bas de l'écran ; admins : les chapitres de /menu."""
    if user.get("role") == "livreur":
        return keyboards.livreur_menu()
    return keyboards.menu_chapters() if common.is_admin(user) else None


async def aide(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    await messaging.reply(update, texts.welcome(user["role"]), _menu(user))


@common.callback
async def choose_role(update: Update, context):
    tg = update.effective_user
    role = common.arg(update)
    if role not in ("franchise", "livreur", "ravitailleur") or tg.id == config.get().dispatch_telegram_id:
        return None
    user = await db.get_user_by_tg(tg.id)
    if user is not None:
        if user["status"] == "banned":
            return None
        if user["status"] != "pending" or user.get("conversation_state") != "awaiting_name":
            return texts.ALREADY_HANDLED
        await db.update_user(user["id"], {"role": role})
    else:
        await db.create_user({
            "telegram_id": tg.id, "telegram_username": tg.username, "role": role,
            "status": "pending", "conversation_state": "awaiting_name",
        })
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         f"{texts.ASK_ROLE}\n→ {texts.ROLE_LABEL[role]}")
    await messaging.reply(update, texts.ASK_NAME)
    return None


async def receive_name(update: Update, context, user: dict) -> None:
    """Texte reçu d'un utilisateur en cours d'inscription (état awaiting_name)."""
    name = " ".join((update.message.text or "").split())
    if not name:
        await messaging.reply(update, texts.ASK_NAME)
        return
    if len(name) > MAX_NAME:
        await messaging.reply(update, texts.NAME_TOO_LONG)
        return
    user = await db.update_user(user["id"], {
        "real_name": name, "conversation_state": None, "state_payload": None, "state_expires_at": None,
    })
    await messaging.reply(update, texts.REGISTRATION_SENT)
    await messaging.notify_dispatch(
        context.bot, texts.new_registration(user["role"], name, user.get("telegram_username")),
        keyboards.approve_reject(user["id"]), team=False, important=True,
    )
    await db.log_event("user_registered", user_id=user["id"], payload={"role": user["role"]})


async def _is_dispatch(update: Update) -> bool:
    """Validation des inscriptions : le dispatch seulement."""
    if update.effective_user.id == config.get().dispatch_telegram_id:
        return True
    return common.is_dispatch(await db.get_user_by_tg(update.effective_user.id))


@common.callback
async def approve(update: Update, context):
    if not await _is_dispatch(update):
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] != "pending":
        return texts.ALREADY_HANDLED
    user = await approve_user(context, user)
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.registration_approved(user))
    if names.names_for(user["role"]):
        from bot.handlers import dispatch

        await dispatch.send_name_picker(update, user)
    return "Validé"


async def approve_user(context, user: dict) -> dict:
    """Inscription validée : nom (premier nom de feuille libre, sinon « Livreur 3 »), commandes, bienvenue."""
    fields = {"status": "active", "conversation_state": None, "state_payload": None, "state_expires_at": None}
    if not user.get("display_name"):
        # Livreur / ravitailleur : premier nom libre de la feuille (« Livreur A »…), sinon « Livreur 3 ».
        sheet_name = await names.first_free(user["role"], user["id"])
        n = await db.count_display_names(user["role"]) + 1
        fields["display_name"] = sheet_name or f"{texts.ROLE_LABEL[user['role']]} {n}"
    user = await db.update_user(user["id"], fields)
    await common.set_commands(context.bot, user)
    await messaging.send(context.bot, user, texts.welcome(user["role"]), _menu(user))
    await db.log_event("user_approved", user_id=user["id"], payload={"display_name": user["display_name"]})
    return user


@common.callback
async def reject(update: Update, context):
    if not await _is_dispatch(update):
        return None
    user = await db.get_user(common.arg(update))
    if user is None or user["status"] != "pending":
        return texts.ALREADY_HANDLED
    await reject_user(context, user)
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.registration_rejected(user))
    return "Refusé"


async def reject_user(context, user: dict) -> None:
    await db.delete_user(user["id"])
    await messaging.send(context.bot, user, texts.REJECTED)
    await db.log_event("user_rejected", payload={"telegram_id": user["telegram_id"], "real_name": user.get("real_name"),
                                                  "role": user["role"]})
