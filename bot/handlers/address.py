"""Changer l'adresse d'une course déjà envoyée (le client s'était trompé), sans la retirer ni la refaire.

📍 Adresse (message de course du franchisé, /encours du dispatch) → il écrit la nouvelle adresse (2e ligne
facultative : digicode, étage…) → aperçu avec l'adresse trouvée → ✅. La course garde son numéro, son livreur
et ses produits ; le livreur reçoit la nouvelle adresse (message qui sonne + fiche à jour), les propositions
encore ouvertes sont corrigées, franchisé et dispatch prévenus, alertes « il arrive / sur place » repartent.
Franchisé de la course et admins (dispatch, franchisés admins) ; course en attente ou en cours."""
from __future__ import annotations

from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import geocoding, lifecycle
from bot.timeutil import now_utc, parse_ts

STATE = "address_edit"
STATE_MINUTES = 15
MAX_DETAIL = 200


def _can_edit(user: dict | None, course: dict | None) -> bool:
    if not user or not course or user.get("status") != "active" or course["status"] not in ("pending", "assigned"):
        return False
    return course["franchise_id"] == user["id"] or common.is_admin(user)


async def _save(user: dict, payload: dict) -> None:
    await db.set_state(user["id"], STATE, payload, now_utc() + timedelta(minutes=STATE_MINUTES))


@common.callback
async def start(update: Update, context):
    """addr:<course> — demande la nouvelle adresse."""
    user = await common.actor(update)
    course = await db.get_course(common.arg(update, int))
    if not _can_edit(user, course):
        return texts.COURSE_FINISHED, True
    sent = await messaging.reply(update, texts.address_ask(course), keyboards.address_cancel())
    await _save(user, {"course_id": course["id"], "msg": sent.message_id if sent else None})
    return None


async def on_message(update: Update, context, user: dict, payload: dict) -> None:
    """La nouvelle adresse écrite : recherche, puis aperçu à confirmer."""
    course = await db.get_course(int(payload.get("course_id") or 0))
    if not _can_edit(user, course):
        await db.clear_state(user["id"])
        await messaging.reply(update, texts.COURSE_FINISHED)
        return
    first, _, rest = (update.message.text or "").strip().partition("\n")
    address, detail = first.strip(), " ".join(rest.split())[:MAX_DETAIL] or None
    if len(address) < 5:
        await messaging.reply(update, texts.address_ask(course), keyboards.address_cancel())
        return
    try:
        geo = await geocoding.geocode(address)
    except geocoding.GeocodingUnavailable:
        await messaging.reply(update, texts.GENERIC_ERROR)
        return
    if geo is None:
        await messaging.reply(update, texts.geocode_not_found(address), keyboards.address_cancel())
        return
    if not geocoding.in_zone(geo.postcode, config.get().departements_autorises):
        await messaging.reply(update, texts.out_of_zone(f"{geo.city} {geo.postcode}".strip()), keyboards.address_cancel())
        return
    new = {"address": geo.label, "postal_code": geo.postcode, "district": geocoding.district(geo.postcode, geo.city),
           "lat": geo.lat, "lon": geo.lon}
    if detail:
        new["address_detail"] = detail
    sent = await messaging.reply(update, texts.address_preview(course, new), keyboards.address_confirm())
    await _save(user, {"course_id": course["id"], "new": new, "msg": sent.message_id if sent else None})


@common.callback
async def action(update: Update, context):
    """addr_ok : appliquer · addr_x : annuler."""
    user = await common.actor(update)
    if user is None:
        return None
    chat_id, msg_id = update.effective_chat.id, update.callback_query.message.message_id
    if update.callback_query.data == "addr_x":
        state, _ = common.active_state(user)
        if state == STATE:
            await db.clear_state(user["id"])
        await messaging.edit(context.bot, chat_id, msg_id, texts.ADDRESS_CANCELLED)
        return None
    payload = await common.claim_state(user, STATE, msg_id)
    if payload is None or not payload.get("new"):
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.ADDRESS_EXPIRED, True
    course = await db.get_course(int(payload["course_id"]))
    if not _can_edit(user, course):
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.COURSE_FINISHED, True
    old = course["address"]
    course = await db.update_course(course["id"], payload["new"]) or {**course, **payload["new"]}
    await db.log_event("address_changed", course["id"], user["id"], {"from": old, "to": course["address"]})
    await messaging.edit(context.bot, chat_id, msg_id, texts.address_done(course))

    franchise = await db.get_user(course["franchise_id"])
    livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
    if course["status"] == "assigned" and livreur:
        # Message qui sonne + fiche à jour (le livreur part peut-être déjà vers l'ancienne adresse).
        await messaging.send(context.bot, livreur, texts.address_for_livreur(course, old))
        await messaging.edit(context.bot, livreur["telegram_id"], course.get("livreur_message_id"),
                             texts.full_fiche(course, franchise or {}), keyboards.livreur_course(course["id"]),
                             user=livreur)
    elif course["status"] == "pending":
        await _refresh_proposals(context, course)
    if franchise and franchise["id"] != user["id"] and franchise["role"] != "dispatch":
        await messaging.send(context.bot, franchise, texts.address_for_franchise(course, old, user))
    await messaging.notify_dispatch(
        context.bot, dispatch=user["role"] != "dispatch", skip=[user["id"], course["franchise_id"]],
        text=texts.d_address_changed(course, old, user))
    await lifecycle.refresh_franchise_message(context, course, livreur=livreur, franchise=franchise)
    return "Adresse changée ✅"


async def _refresh_proposals(context, course: dict) -> None:
    """Propositions encore ouvertes chez les livreurs : nouvelle adresse, même bouton « Je prends »."""
    broadcasts = await db.list_broadcasts(course["id"])
    users = await db.get_users(b["livreur_id"] for b in broadcasts)
    for b in broadcasts:
        lv = users.get(b["livreur_id"])
        if lv and b.get("telegram_message_id"):
            await messaging.edit(context.bot, lv["telegram_id"], b["telegram_message_id"], texts.proposal(course, None),
                                 keyboards.proposal(course["id"]), sent_at=parse_ts(b["sent_at"]), user=lv,
                                 resend_if_old=False)
