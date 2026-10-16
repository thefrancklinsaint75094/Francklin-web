"""Fiche utilisateur : dans /users, chaque personne est un bouton ; sa fiche permet de tout changer au même
endroit (admins).

Fiche : nom (ou nom de feuille pour livreurs et ravitailleurs), prénom (livreurs), rôle, statut, @pseudo,
nom donné à l'inscription, service et moyen de déplacement (livreurs), activité de la nuit.
Boutons : ✏️ Nom / 🏷 Nom de feuille · 🙂 Prénom · 🔄 Rôle · 🟢 En service / ⏸ Pause · 📍 Position ·
⛔ Bannir / ✅ Réactiver · 🗑 Supprimer · ↩️ Liste. Bannir, réactiver, supprimer et nom de feuille réutilisent
les écrans existants (confirmation comprise)."""
from __future__ import annotations

from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import names
from bot.timeutil import night_bounds, night_start_date, now_utc

STATE = "user_edit"
STATE_MINUTES = 10
NAME_MIN, NAME_MAX = 2, 40
ROLES = ("franchise", "livreur", "ravitailleur")


async def _admin(update: Update) -> dict | None:
    user = await common.actor(update)
    return user if common.is_admin(user) else None


def _sort_key(u: dict):
    name = u.get("display_name") or ""
    digits = "".join(ch for ch in name if ch.isdigit())
    return (int(digits) if digits else 10_000, name)


async def list_view(viewer: dict | None = None) -> tuple[str, object]:
    """Texte et boutons de /users : la liste détaillée par rôle, une personne = un bouton vers sa fiche,
    inscriptions en attente à valider en bas."""
    everyone = [u for u in await db.list_users() if u["role"] != "dispatch"]
    on_site: dict[str, int] = {}
    for c in await db.list_franchise_cancelled_on_site():
        on_site[c["franchise_id"]] = on_site.get(c["franchise_id"], 0) + 1

    def status_label(u: dict) -> str:
        return "⛔ banni" if u["status"] == "banned" else "actif"

    def of(role: str) -> list[dict]:
        return sorted((u for u in everyone if u["role"] == role and u["status"] != "pending"), key=_sort_key)

    franchises, livreurs = [], []
    for u in of("franchise"):
        line = f"{texts.user_who(u)} — {status_label(u)}"
        if u["status"] != "banned" and on_site.get(u["id"]):
            line += f" — {texts.plural(on_site[u['id']], 'annulation')} sur place"
        franchises.append(line)
    for u in of("livreur"):
        if u["status"] == "banned":
            livreurs.append(f"{texts.user_who(u)} — ⛔ banni")
        else:
            duty = "en service" if u.get("on_duty") else "pause"
            n = u.get("cancel_count") or 0
            livreurs.append(f"{texts.user_who(u)} — actif · {duty} — {n} annulation{'s' if n > 1 else ''}")
    ravitailleurs = [f"{texts.user_who(u)} — {status_label(u)}" for u in of("ravitailleur")]
    pending_users = [u for u in everyone if u["status"] == "pending"]
    pending = []
    for u in pending_users:
        line = f"{texts.ROLE_LABEL[u['role']]} — {texts.esc(u.get('real_name') or '(nom pas encore donné)')}"
        if u.get("telegram_username"):
            line += f" (@{texts.esc(u['telegram_username'])})"
        pending.append(line)
    active = of("franchise") + of("livreur") + of("ravitailleur")
    return (texts.users_list(franchises, livreurs, pending, ravitailleurs),
            keyboards.users_overview(active, pending_users, manage=viewer is None or common.is_dispatch(viewer)))


async def _activity(user: dict) -> dict:
    """Ce que la personne a fait cette nuit : courses livrées (nombre, total), courses en cours."""
    cfg = config.get()
    start, end = night_bounds(night_start_date(now_utc(), cfg.night_end_hour), cfg.night_end_hour)
    delivered = await db.list_delivered_between(start, end)
    key = "livreur_id" if user["role"] == "livreur" else "franchise_id"
    mine = [c for c in delivered if c.get(key) == user["id"]]
    ongoing = await db.list_assigned_for_livreur(user["id"]) if user["role"] == "livreur" else []
    return {"delivered": len(mine), "total": sum(float(c["price"]) for c in mine), "ongoing": len(ongoing)}


async def _card(user: dict, viewer: dict) -> tuple[str, object]:
    return (texts.user_card(user, names.prenom(user.get("display_name")), await _activity(user)),
            keyboards.user_card(user, can_act=user["id"] != viewer["id"],
                                sheet_names=bool(names.names_for(user["role"])), manage=common.is_dispatch(viewer)))


async def show(context, chat_id: int, message_id: int | None, user: dict, viewer: dict) -> None:
    text, markup = await _card(user, viewer)
    await messaging.edit(context.bot, chat_id, message_id, text, markup)


@common.callback
async def action(update: Update, context):
    """uc:<id> fiche · uc_list liste · uc_edit:<id>:<name|prenom> · uc_role:<id> · uc_role_set:<id>:<rôle> ·
    uc_duty:<id>:<on|off>."""
    admin = await _admin(update)
    if admin is None:
        return None
    chat_id, msg_id = update.effective_chat.id, update.callback_query.message.message_id
    data = update.callback_query.data or ""
    if data == "uc_list":
        text, markup = await list_view(admin)
        await messaging.edit(context.bot, chat_id, msg_id, text, markup)
        return None
    act, _, rest = data.partition(":")
    uid, _, arg = rest.partition(":")
    user = await db.get_user(uid)
    if user is None or user["status"] == "deleted" or user["role"] == "dispatch":
        return texts.ALREADY_HANDLED, True

    if act == "uc":
        await show(context, chat_id, msg_id, user, admin)
        return None
    if user["id"] == admin["id"]:
        return texts.USER_SELF, True
    if act in ("uc_edit", "uc_role", "uc_role_set") and not common.is_dispatch(admin):
        return texts.DISPATCH_ONLY, True
    if act == "uc_edit" and arg in ("name", "prenom"):
        if arg == "prenom" and user["role"] != "livreur":
            return texts.ALREADY_HANDLED, True
        await db.set_state(admin["id"], STATE, {"user_id": user["id"], "field": arg, "msg": msg_id},
                           now_utc() + timedelta(minutes=STATE_MINUTES))
        await messaging.reply(update, texts.user_edit_ask(user, arg, names.prenom(user.get("display_name"))),
                              keyboards.user_edit_cancel(user["id"]))
        return None
    if act == "uc_role":
        await messaging.edit(context.bot, chat_id, msg_id, texts.user_role_ask(user), keyboards.user_roles(user))
        return None
    if act == "uc_role_set" and arg in ROLES:
        if arg == user["role"]:
            await show(context, chat_id, msg_id, user, admin)
            return "Rôle inchangé"
        user = await _change_role(context, admin, user, arg)
        await show(context, chat_id, msg_id, user, admin)
        if names.names_for(arg):                     # livreur / ravitailleur : vérifier son nom de feuille
            from bot.handlers import dispatch

            await dispatch.send_name_picker(update, user)
        return f"Rôle : {texts.ROLE_LABEL[arg]}"
    if act == "uc_duty" and user["role"] == "livreur" and user["status"] == "active":
        from bot.handlers import dispatch

        answer = await dispatch.set_duty(context, admin, user, arg == "on")
        await show(context, chat_id, msg_id, await db.get_user(user["id"]) or user, admin)
        return answer
    return None


async def _change_role(context, admin: dict, user: dict, role: str) -> dict:
    """Nouveau rôle : un livreur qui change de rôle quitte le service (ses courses repartent en diffusion) ;
    nouveau nom comme à l'inscription (premier nom de feuille libre, sinon « Franchisé 3 »…)."""
    from bot.handlers import onboarding
    from bot.services import lifecycle

    old = user["role"]
    if old == "livreur":
        for course in await db.list_assigned_for_livreur(user["id"]):
            await lifecycle.release(context, course, reason="dispatch")
        await db.delete_position(user["id"])
    fields = {"role": role, "on_duty": False, "soon_free": False, "duty_forced": False,
              "conversation_state": None, "state_payload": None, "state_expires_at": None}
    sheet_name = await names.first_free(role, user["id"])       # comme à l'inscription
    fields["display_name"] = sheet_name or f"{texts.ROLE_LABEL[role]} {await db.count_display_names(role) + 1}"
    user = await db.update_user(user["id"], fields) or {**user, **fields}
    await db.log_event("user_role_changed", user_id=user["id"], payload={"from": old, "to": role, "by": admin["id"]})
    await common.set_commands(context.bot, user)
    await messaging.send(context.bot, user, f"{texts.role_changed_notice(role)}\n\n{texts.welcome(role)}",
                         onboarding._menu(user))
    return user


async def on_message(update: Update, context, admin: dict, payload: dict) -> None:
    """Nouveau nom ou prénom écrit par l'admin."""
    await db.clear_state(admin["id"])
    user = await db.get_user(payload.get("user_id") or "")
    if user is None or user["status"] == "deleted":
        await messaging.reply(update, texts.ALREADY_HANDLED)
        return
    value = " ".join((update.message.text or "").split())
    if payload.get("field") == "prenom":
        if value.lower() in ("-", "aucun", "rien", "supprimer"):
            await db.delete_prenom(user["display_name"])
        elif NAME_MIN <= len(value) <= 30:
            await db.set_prenom(user["display_name"], value)
        else:
            await messaging.reply(update, texts.USER_NAME_INVALID)
            return
        await names.refresh()
        await db.log_event("prenom", user_id=admin["id"], payload={"name": user["display_name"], "prenom": value})
    else:
        if not NAME_MIN <= len(value) <= NAME_MAX:
            await messaging.reply(update, texts.USER_NAME_INVALID)
            return
        old = user.get("display_name")
        user = await db.update_user(user["id"], {"display_name": value}) or user
        await db.log_event("user_renamed", user_id=user["id"], payload={"from": old, "to": value, "by": admin["id"]})
    text, markup = await _card(user, admin)
    await messaging.reply(update, text, markup)
    if payload.get("msg"):
        await messaging.edit_markup(context.bot, update.effective_chat.id, payload["msg"], None)


@common.callback
async def cancel_edit(update: Update, context):
    """uc_x:<id> : abandon de la saisie du nom / prénom."""
    admin = await _admin(update)
    if admin is None:
        return None
    state, _ = common.active_state(admin)
    if state == STATE:
        await db.clear_state(admin["id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.CANCELLED_OP)
    return None
