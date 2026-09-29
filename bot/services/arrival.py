"""Alerte « le livreur arrive » : franchisé et dispatch sont prévenus une fois par course,
quand le livreur est à moins de ARRIVAL_NOTIFY_METERS de l'adresse, ou à moins de
ARRIVAL_NOTIFY_MINUTES d'après sa vitesse (mesurée entre ses deux dernières positions,
sinon vitesse moyenne d'un deux-roues en ville)."""
from __future__ import annotations

import logging
from datetime import datetime

from bot import config, db, messaging, texts
from bot.services.distance import format_distance, haversine_m
from bot.timeutil import now_utc, parse_ts

log = logging.getLogger(__name__)

DEFAULT_SPEED_MS = 15 / 3.6      # 15 km/h : deux-roues en ville, feux compris
MIN_SPEED_MS = 1.0               # en dessous : à l'arrêt, vitesse non significative
MAX_SPEED_MS = 20.0              # au-delà : saut de position, pas une vitesse
MIN_SAMPLE_SECONDS = 5
MAX_SAMPLE_SECONDS = 600

_notified: set[tuple[int, str]] = set()   # (course, assigned_at) déjà annoncés (cache ; la base fait foi)


def measured_speed(prev: dict | None, lat: float, lon: float, now: datetime) -> float | None:
    """Vitesse (m/s) entre la position précédente et celle-ci, si elle est exploitable."""
    if not prev:
        return None
    at = parse_ts(prev.get("updated_at"))
    if at is None:
        return None
    seconds = (now - at).total_seconds()
    if not MIN_SAMPLE_SECONDS <= seconds <= MAX_SAMPLE_SECONDS:
        return None
    speed = haversine_m(prev["lat"], prev["lon"], lat, lon) / seconds
    return speed if MIN_SPEED_MS <= speed <= MAX_SPEED_MS else None


WALK_SPEED_MS = 4.5 / 3.6       # livreur en transport : les derniers mètres se font à pied


def eta_minutes(distance_m: float, speed_ms: float | None, mode: str | None = None) -> int:
    """Minutes estimées jusqu'à l'adresse, arrondies à la minute supérieure (au moins 1).
    Sans vitesse mesurée : à pied pour un livreur en transport, sinon 15 km/h."""
    speed = speed_ms or (WALK_SPEED_MS if mode == "transport" else DEFAULT_SPEED_MS)
    return max(1, int(-(-distance_m // (speed * 60))))


def should_notify(distance_m: float, eta: int, radius_m: int, minutes: int) -> bool:
    return distance_m <= radius_m or eta <= minutes


async def _already(course: dict) -> bool:
    key = (course["id"], str(course.get("assigned_at")))
    if key in _notified:
        return True
    events = await db.list_events(course["id"], "arrival_notified")
    if any((e.get("payload") or {}).get("assigned_at") == course.get("assigned_at") for e in events):
        _notified.add(key)
        return True
    return False


async def check(context, livreur: dict, lat: float, lon: float, prev: dict | None) -> None:
    """À chaque position du livreur : prévient franchisé et dispatch pour chaque course en cours
    qui vient d'entrer dans la zone d'arrivée. Ne lève jamais d'exception."""
    try:
        cfg = config.get()
        if cfg.arrival_notify_meters <= 0 and cfg.arrival_notify_minutes <= 0:
            return
        courses = await db.list_assigned_for_livreur(livreur["id"])
        if not courses:
            return
        speed = measured_speed(prev, lat, lon, now_utc())
        for course in courses:
            dist = haversine_m(lat, lon, course["lat"], course["lon"])
            eta = eta_minutes(dist, speed, livreur.get("transport_mode"))
            if not should_notify(dist, eta, cfg.arrival_notify_meters, cfg.arrival_notify_minutes):
                continue
            if await _already(course):
                continue
            _notified.add((course["id"], str(course.get("assigned_at"))))
            await db.log_event("arrival_notified", course["id"], livreur["id"], {
                "assigned_at": course.get("assigned_at"), "distance_m": int(dist), "eta_min": eta,
            })
            label = format_distance(dist)
            franchise = await db.get_user(course["franchise_id"])
            if franchise:
                await messaging.send(context.bot, franchise, texts.arrival_for_franchise(course, livreur, label, eta))
            await messaging.notify_dispatch(context.bot, texts.d_arrival(course, livreur, label, eta))
    except Exception:  # noqa: BLE001 — une alerte ratée ne doit jamais gêner le suivi de position
        log.exception("Alerte d'arrivée impossible")
