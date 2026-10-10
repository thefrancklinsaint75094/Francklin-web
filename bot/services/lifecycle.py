"""Cycle de vie d'une course : message du franchisé, attribution, livraison,
remise en diffusion, annulation. Partagé par les handlers livreur, franchisé et dispatch."""
from __future__ import annotations

import logging

from bot import config, db, keyboards, messaging, texts
from bot.services import broadcast, pick, sheets, stock
from bot.services.distance import format_distance, haversine_m
from bot.timeutil import iso, now_utc, parse_ts

log = logging.getLogger(__name__)

# État d'affichage du message franchisé (en mémoire : au pire, après un
# redémarrage, le message repasse à « envoyée aux livreurs »).
_no_livreur: dict[int, str] = {}  # course_id -> 'none_on_duty' | 'none_eligible'
_notice: dict[int, str] = {}      # course_id -> en-tête particulier (livreur a annulé…)


def is_waiting_for_livreur(course_id: int) -> bool:
    return course_id in _no_livreur


def _forget(course_id: int) -> None:
    _no_livreur.pop(course_id, None)
    _notice.pop(course_id, None)


def franchise_view(course: dict, livreur: dict | None = None) -> tuple[str, object]:
    if course["status"] == "pending" and pick.is_awaiting(course["id"]):
        return texts.franchise_pick(course, config.get().pick_timeout_minutes), pick.markup(course["id"])
    state = _no_livreur.get(course["id"])
    text = texts.franchise_course_status(
        course, livreur,
        no_livreur=state == "none_eligible",
        none_on_duty=state == "none_on_duty",
    )
    if course["status"] == "pending" and not state and course["id"] in _notice:
        text = f"{_notice[course['id']]}\n{texts.course_summary(course)}"
    return text, keyboards.franchise_course(course)


async def refresh_franchise_message(context, course: dict, livreur: dict | None = None,
                                    franchise: dict | None = None) -> None:
    franchise = franchise or await db.get_user(course["franchise_id"])
    if not franchise:
        return
    if course["status"] == "assigned" and livreur is None and course.get("livreur_id"):
        livreur = await db.get_user(course["livreur_id"])
    text, markup = franchise_view(course, livreur)
    new = await messaging.edit(
        context.bot, franchise["telegram_id"], course.get("franchise_message_id"), text, markup,
        sent_at=parse_ts(course.get("created_at")), user=franchise,
    )
    if new is not None:
        await db.update_course(course["id"], {"franchise_message_id": new.message_id})
        course["franchise_message_id"] = new.message_id


async def mark_no_livreur(context, course: dict) -> None:
    """Aucun livreur éligible : franchisé et dispatch prévenus une seule fois."""
    if course["id"] in _no_livreur:
        return
    on_duty = await db.list_on_duty_livreurs()
    _no_livreur[course["id"]] = "none_on_duty" if not on_duty else "none_eligible"
    await refresh_franchise_message(context, course)
    await messaging.notify_dispatch(context.bot, texts.d_no_livreur(course["id"]))
    await db.log_event("no_livreur", course["id"])


async def clear_no_livreur(context, course: dict) -> None:
    if _no_livreur.pop(course["id"], None) is not None:
        await refresh_franchise_message(context, course)


# ---------------------------------------------------------------- attribution

async def after_assignment(context, course: dict, livreur: dict, message) -> None:
    """§9.4, dans l'ordre : autres livreurs, fiche complète, franchisé, dispatch, journal."""
    broadcast.cancel_wave(context.job_queue, course["id"])
    pick.forget(context.job_queue, course["id"])
    _forget(course["id"])
    await broadcast.close_proposals(context, course["id"], texts.proposal_taken(course["id"]), except_livreur=livreur["id"])

    franchise = await db.get_user(course["franchise_id"])
    fiche = texts.full_fiche(course, franchise or {})
    bc = await db.get_broadcast(course["id"], livreur["id"])
    msg_id = message.message_id if message is not None else None
    new = await messaging.edit(
        context.bot, livreur["telegram_id"], msg_id, fiche, keyboards.livreur_course(course["id"]),
        sent_at=parse_ts(bc["sent_at"]) if bc else None, user=livreur,
    )
    if new is not None:
        msg_id = new.message_id
    await db.update_course(course["id"], {"livreur_message_id": msg_id})
    course["livreur_message_id"] = msg_id

    await refresh_franchise_message(context, course, livreur=livreur, franchise=franchise)
    await messaging.notify_dispatch(context.bot, texts.d_taken(course, franchise or {"display_name": "?"}, livreur))
    await db.log_event("course_taken", course["id"], livreur["id"])
    stock.after_assignment_later(context, course, livreur)


# ---------------------------------------------------------------- livraison

PAYMENTS = {"e": "especes", "v": "virement"}   # code du bouton → valeur en base


async def deliver(context, course: dict, by_dispatch: bool = False, payment: str | None = None) -> dict | None:
    """Passe la course en `delivered` (avec le mode de paiement). Renvoie la course à jour, ou
    None si elle n'était plus en cours (double appui, course déjà close…)."""
    livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
    now = now_utc()
    fields = {"status": "delivered", "delivered_at": iso(now), "closed_at": iso(now)}
    if payment in PAYMENTS.values():
        fields["payment"] = payment
    from bot.services import transport

    detected = transport.final_mode(course)
    if detected and detected != course.get("detected_mode"):
        fields["detected_mode"] = detected
    far_label = None
    if livreur and not by_dispatch:
        pos = await db.get_position(livreur["id"])
        if pos and (now - parse_ts(pos["updated_at"])).total_seconds() < 600:
            dist = int(haversine_m(pos["lat"], pos["lon"], course["lat"], course["lon"]))
            fields["delivered_distance_m"] = dist
            if dist > 1000:
                far_label = format_distance(dist)
    updated = await db.update_course_if_status(course["id"], ["assigned"], fields)
    if updated is None:
        return None

    if livreur:
        if livreur.get("soon_free"):
            await db.update_user(livreur["id"], {"soon_free": False})
        text = texts.forced_delivered_for_livreur(course["id"]) if by_dispatch else texts.delivered_for_livreur(updated)
        if by_dispatch:
            await messaging.edit_markup(context.bot, livreur["telegram_id"], course.get("livreur_message_id"))
            await messaging.send(context.bot, livreur, text)
        else:
            await messaging.edit(context.bot, livreur["telegram_id"], course.get("livreur_message_id"), text,
                                 sent_at=parse_ts(course.get("assigned_at")), user=livreur)
    await refresh_franchise_message(context, updated, livreur=livreur)
    if not by_dispatch:
        await messaging.notify_dispatch(
            context.bot, texts.d_delivered(updated, livreur or {"display_name": "?"}, far_label)
        )
    await db.log_event(
        "course_delivered", course["id"], livreur["id"] if livreur else None,
        {"by_dispatch": by_dispatch, "distance_m": fields.get("delivered_distance_m")},
    )
    stock.after_delivery_later(context, updated, livreur)
    if livreur:
        await remind_next(context, livreur, course["id"])
    await broadcast.kick_pending(context)
    return updated


async def remind_next(context, livreur: dict, done_id: int) -> None:
    """Le livreur vient de finir une course et en a une autre (reçue pendant sa livraison) : on la lui
    renvoie, fiche et boutons, comme course à faire maintenant (son délai repart de là)."""
    others = [c for c in await db.list_assigned_for_livreurs([livreur["id"]]) if c["id"] != done_id]
    if not others:
        return
    nxt = min(others, key=lambda c: (c.get("assigned_at") or "", c["id"]))
    franchise = await db.get_user(nxt["franchise_id"]) or {}
    sent = await messaging.send(context.bot, livreur, texts.next_course_for_livreur(nxt, franchise),
                                keyboards.livreur_course(nxt["id"]))
    fields = {"assigned_at": iso(now_utc())}
    if sent is not None:
        fields["livreur_message_id"] = sent.message_id
    await db.update_course(nxt["id"], fields)
    await db.log_event("next_course_reminded", nxt["id"], livreur["id"], {"after": done_id})


# ---------------------------------------------------------------- remise en diffusion

async def release(context, course: dict, reason: str) -> dict | None:
    """Retire le livreur d'une course assignée et la remet en diffusion depuis la vague 1.

    reason : 'livreur_cancel' (compte dans ses annulations), 'dispatch' ou 'ban'.
    Le livreur retiré reste exclu de cette course ; les autres redeviennent sollicitables.
    """
    livreur_id = course.get("livreur_id")
    updated = await db.update_course_if_status(
        course["id"], ["assigned"],
        {"status": "pending", "livreur_id": None, "assigned_at": None, "broadcast_round": 1},
        livreur_id=livreur_id,
    )
    if updated is None:
        return None
    livreur = await db.get_user(livreur_id) if livreur_id else None
    await db.reset_broadcasts_except(course["id"], livreur_id)

    if livreur:
        fields = {"soon_free": False}
        if reason == "livreur_cancel":
            fields["cancel_count"] = (livreur.get("cancel_count") or 0) + 1
        livreur = await db.update_user(livreur["id"], fields) or livreur
        if reason == "livreur_cancel":
            text = texts.livreur_cancelled(course["id"])
            await messaging.edit(context.bot, livreur["telegram_id"], course.get("livreur_message_id"), text,
                                 sent_at=parse_ts(course.get("assigned_at")), user=livreur)
        elif reason == "dispatch":
            await messaging.edit_markup(context.bot, livreur["telegram_id"], course.get("livreur_message_id"))
            await messaging.send(context.bot, livreur, texts.released_by_dispatch_for_livreur(course["id"]))
        else:  # ban : l'exclu reçoit uniquement « Ton accès a été retiré. »
            await messaging.edit_markup(context.bot, livreur["telegram_id"], course.get("livreur_message_id"))

    await db.update_course(course["id"], {"livreur_message_id": None})
    updated["livreur_message_id"] = None
    _forget(course["id"])
    if reason == "livreur_cancel" and livreur:
        _notice[course["id"]] = texts.livreur_cancelled_for_franchise(livreur["display_name"], course["id"])
    else:
        _notice[course["id"]] = texts.course_released_for_franchise(course["id"])
    await refresh_franchise_message(context, updated)

    if reason == "livreur_cancel" and livreur:
        await messaging.notify_dispatch(context.bot, texts.d_livreur_cancel(course["id"], livreur))
        await db.log_event("livreur_cancelled", course["id"], livreur["id"],
                           {"cancel_count": livreur.get("cancel_count")})
    else:
        await db.log_event("course_released", course["id"], livreur_id, {"reason": reason})

    await broadcast.run_wave(context, course["id"], advance=False)
    if livreur and reason != "ban":
        await broadcast.kick_pending(context)
    return updated


# ---------------------------------------------------------------- annulation

async def cancel(context, course: dict, by: str) -> dict | None:
    """Annule une course non livrée. by : 'franchise', 'dispatch' ou 'ban' (franchisé exclu).

    pending  → cancelled (propositions retirées chez les livreurs)
    assigned → cancelled_on_site (livreur libéré, sans toucher à ses annulations)
    """
    now = iso(now_utc())
    status = course["status"]
    if status == "pending":
        updated = await db.update_course_if_status(course["id"], ["pending"], {"status": "cancelled", "closed_at": now})
    elif status == "assigned":
        updated = await db.update_course_if_status(
            course["id"], ["assigned"], {"status": "cancelled_on_site", "closed_at": now},
            livreur_id=course.get("livreur_id"),
        )
    else:
        return None
    if updated is None:
        return None

    broadcast.cancel_wave(context.job_queue, course["id"])
    pick.forget(context.job_queue, course["id"])
    _forget(course["id"])
    franchise = await db.get_user(course["franchise_id"])
    livreur = None
    if status == "pending":
        await broadcast.close_proposals(context, course["id"], texts.proposal_withdrawn(course["id"]))
    else:
        livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
        if livreur:
            await db.update_user(livreur["id"], {"soon_free": False})
            text = (texts.cancelled_by_dispatch_for_livreur(course["id"]) if by == "dispatch"
                    else texts.cancelled_by_franchise(course["id"]))
            await messaging.edit(context.bot, livreur["telegram_id"], course.get("livreur_message_id"), text,
                                 sent_at=parse_ts(course.get("assigned_at")), user=livreur, resend_if_old=False)
            await messaging.send(context.bot, livreur, text)
    if by != "ban":
        await refresh_franchise_message(context, updated, livreur=livreur, franchise=franchise)

    if by == "franchise":
        if status == "pending":
            await messaging.notify_dispatch(context.bot, texts.d_withdrawn(course["id"], franchise))
        else:
            await messaging.notify_dispatch(context.bot, texts.d_cancelled_on_site(course["id"], franchise, livreur))
    await db.log_event(
        "course_withdrawn" if status == "pending" else "cancelled_on_site",
        course["id"], franchise["id"] if franchise else None, {"by": by},
    )
    if livreur:
        await remind_next(context, livreur, course["id"])
        await broadcast.kick_pending(context)
    return updated
