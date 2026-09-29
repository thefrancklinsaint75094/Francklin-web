"""Sélection des livreurs éligibles (§8.4) et diffusion par vagues (§9.1)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from bot import config, db, keyboards, messaging, texts
from bot.services import lifecycle, stock, transport
from bot.services.distance import format_distance, haversine_m
from bot.timeutil import now_utc, parse_ts

log = logging.getLogger(__name__)

NO_LIVREUR_RETRY_SECONDS = 120

_course_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def select_eligible(
    livreurs: list[dict],
    positions: dict[str, dict],
    assigned_counts: dict[str, int],
    already_solicited: set[str],
    course_lat: float,
    course_lon: float,
    now: datetime,
    stale_minutes: int,
    max_distance_km: float,
) -> list[tuple[dict, float]]:
    """Règle d'éligibilité unique (§8.4), triée par distance puis position la plus récente.

    1. status = active   2. on_duty   3. position fraîche
    4. aucune course assignée, ou exactement une et soon_free
    5. pas déjà sollicité pour cette course   6. à moins de MAX_DISTANCE_KM

    Un livreur mis en service par un admin (duty_forced) sans position fraîche reste
    éligible, distance inconnue (None), après ceux dont on connaît la position.

    Moyen de déplacement : tri par temps de trajet estimé (🚶 transport, 🛵 deux-roues, 🚗 voiture),
    et un livreur en transport ne reçoit pas de course au-delà de TRANSPORT_MAX_KM.
    """
    stale_before = now - timedelta(minutes=stale_minutes)
    located: list[tuple[dict, float, datetime]] = []
    unlocated: list[dict] = []
    for lv in livreurs:
        if lv.get("role") != "livreur" or lv.get("status") != "active" or not lv.get("on_duty"):
            continue
        n = assigned_counts.get(lv["id"], 0)
        if not (n == 0 or (n == 1 and lv.get("soon_free"))):
            continue
        if lv["id"] in already_solicited:
            continue
        pos = positions.get(lv["id"])
        updated = parse_ts(pos["updated_at"]) if pos else None
        if updated is None or updated < stale_before:
            if lv.get("duty_forced"):
                unlocated.append(lv)
            continue
        dist = haversine_m(pos["lat"], pos["lon"], course_lat, course_lon)
        mode = lv.get("transport_mode")
        if dist > min(max_distance_km, transport.max_distance_km(mode)) * 1000:
            continue
        located.append((lv, dist, updated))
    located.sort(key=lambda x: (transport.travel_minutes(x[1], x[0].get("transport_mode")), -x[2].timestamp()))
    return [(lv, dist) for lv, dist, _ in located] + [(lv, None) for lv in unlocated]


async def eligible_for(course: dict, already: set[str], context=None) -> list[tuple[dict, float]]:
    cfg = config.get()
    livreurs = await db.list_on_duty_livreurs()
    if not livreurs:
        return []
    ids = [lv["id"] for lv in livreurs]
    positions = await db.get_positions(ids)
    counts: dict[str, int] = defaultdict(int)
    for c in await db.list_assigned_for_livreurs(ids):
        counts[c["livreur_id"]] += 1
    eligible = select_eligible(
        livreurs, positions, counts, already, course["lat"], course["lon"], now_utc(),
        cfg.position_stale_minutes, cfg.max_distance_km,
    )
    if context is None:
        return eligible
    # Ceux qui ont tout en stock d'abord (si les alertes de stock sont activées).
    return await stock.rank_by_stock(context, course, eligible)


# ---------------------------------------------------------------- jobs

def _job_name(course_id: int) -> str:
    return f"wave:{course_id}"


def cancel_wave(job_queue, course_id: int) -> None:
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(_job_name(course_id)):
        job.schedule_removal()


def schedule_wave(job_queue, course_id: int, delay: float) -> None:
    if job_queue is None:
        return
    cancel_wave(job_queue, course_id)
    job_queue.run_once(_wave_job, when=max(1.0, delay), data=course_id, name=_job_name(course_id))


async def _wave_job(context) -> None:
    await run_wave(context, context.job.data, advance=True)


# ---------------------------------------------------------------- vagues

async def run_wave(context, course_id: int, advance: bool = False) -> bool:
    """Lance (ou élargit) la diffusion d'une course encore en attente.

    advance=False : première vague (création, remise en diffusion, relance).
    advance=True  : déclenchement du job ; si la vague courante a touché quelqu'un,
                    on passe à la vague suivante.
    Renvoie True si au moins un livreur a été sollicité.
    """
    cfg = config.get()
    async with _course_locks[course_id]:
        course = await db.get_course(course_id)
        if not course or course["status"] != "pending":
            cancel_wave(context.job_queue, course_id)
            return False
        broadcasts = await db.list_broadcasts(course_id)
        round_ = course["broadcast_round"]
        if advance and any(b["round"] == round_ for b in broadcasts):
            round_ += 1
        eligible = await eligible_for(course, {b["livreur_id"] for b in broadcasts}, context)
        targets = eligible[: cfg.broadcast_wave_size]

        if not targets:
            await lifecycle.mark_no_livreur(context, course)
            schedule_wave(context.job_queue, course_id, NO_LIVREUR_RETRY_SECONDS)
            return False

        if round_ != course["broadcast_round"]:
            await db.update_course(course_id, {"broadcast_round": round_})
            course["broadcast_round"] = round_
        solicited = []
        for livreur, dist in targets:
            if not await db.reserve_broadcast(course_id, livreur["id"], round_):
                continue
            msg = await messaging.send(
                context.bot, livreur, texts.proposal(course, format_distance(dist) if dist is not None else None),
                keyboards.proposal(course_id)
            )
            if msg is not None:
                await db.set_broadcast_message(course_id, livreur["id"], msg.message_id)
            solicited.append(livreur["id"])
        await db.log_event("course_broadcast", course_id, payload={"round": round_, "livreurs": solicited})
        await lifecycle.clear_no_livreur(context, course)
        schedule_wave(context.job_queue, course_id, cfg.broadcast_wave_seconds)
        return True


def _round_has_sends(course: dict, broadcasts: list[dict]) -> bool:
    return any(b["round"] == course["broadcast_round"] for b in broadcasts)


async def kick_pending(context) -> None:
    """Un livreur vient de devenir éligible : les courses qui n'avaient trouvé
    personne lui sont proposées tout de suite, sans attendre la relance."""
    for course in await db.list_courses_by_status("pending"):
        try:
            waiting = lifecycle.is_waiting_for_livreur(course["id"])
            if not waiting:
                waiting = not _round_has_sends(course, await db.list_broadcasts(course["id"]))
            if waiting:
                await run_wave(context, course["id"], advance=False)
        except Exception:  # noqa: BLE001 — une course en échec ne bloque pas les autres
            log.exception("Relance de la course %s impossible", course["id"])


async def resume_all(context) -> None:
    """Au démarrage : reprise de toutes les courses en attente, en calculant la
    vague en cours à partir du dernier envoi."""
    cfg = config.get()
    now = now_utc()
    for course in await db.list_courses_by_status("pending"):
        try:
            broadcasts = await db.list_broadcasts(course["id"])
            sent = [parse_ts(b["sent_at"]) for b in broadcasts if b["round"] > 0]
            if not sent:
                await run_wave(context, course["id"], advance=False)
                continue
            elapsed = (now - max(sent)).total_seconds()
            if elapsed >= cfg.broadcast_wave_seconds:
                await run_wave(context, course["id"], advance=True)
            else:
                schedule_wave(context.job_queue, course["id"], cfg.broadcast_wave_seconds - elapsed)
        except Exception:  # noqa: BLE001
            log.exception("Reprise de la course %s impossible", course["id"])


async def close_proposals(context, course_id: int, text: str, except_livreur: str | None = None) -> None:
    """Retire le bouton « Je prends » chez tous les livreurs sollicités."""
    broadcasts = await db.list_broadcasts(course_id)
    users = await db.get_users(b["livreur_id"] for b in broadcasts)
    for b in broadcasts:
        if b["livreur_id"] == except_livreur or not b.get("telegram_message_id"):
            continue
        lv = users.get(b["livreur_id"])
        if not lv:
            continue
        await messaging.edit(
            context.bot, lv["telegram_id"], b["telegram_message_id"], text,
            sent_at=parse_ts(b["sent_at"]), user=lv, resend_if_old=False,
        )
