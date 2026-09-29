"""Moyen de déplacement des livreurs : 🚶 transport (à pied ou transports en commun, c'est pareil),
🛵 deux-roues, 🚗 voiture.

- Déclaré par le livreur au /dispo (retenu d'un jour sur l'autre).
- Sert au dispatch : temps de trajet estimé selon le mode (et non plus la seule distance), et
  pas de course à plus de TRANSPORT_MAX_KM pour un livreur en transport.
- Sert à l'alerte « le livreur arrive » quand sa vitesse n'est pas mesurable.
- Vérifié pendant les courses avec la position en direct :
  • 🚇 métro : la position ne bouge plus pendant quelques minutes (sous terre, pas de réseau),
    puis réapparaît loin, près d'une autre station, à une vitesse de métro ;
  • 🛵/🚗 véhicule : plusieurs positions de suite à plus de 25 km/h, reçues sans interruption ;
  • 🚶 à pied : jamais plus de 8 km/h sur la course (constaté à la livraison).
  Le dispatch est prévenu quand ce qui est détecté contredit ce qui est déclaré.
"""
from __future__ import annotations

import logging
from datetime import datetime

from bot import config, db, messaging, texts
from bot.services import stations
from bot.services.distance import haversine_m
from bot.timeutil import parse_ts

log = logging.getLogger(__name__)

MODES = {"t": "transport", "d": "deux_roues", "v": "voiture"}   # code du bouton → valeur en base
ICON = {"transport": "🚶", "deux_roues": "🛵", "voiture": "🚗"}
LABEL = {"transport": "Transport / à pied", "deux_roues": "Deux-roues", "voiture": "Voiture"}
DETECTED_ICON = {"metro": "🚇", "vehicule": "🛵", "pied": "🚶"}

# Temps de trajet (minutes) selon le mode — ordres de grandeur parisiens, porte à porte.
WALK_KMH = 4.5
METRO_KMH = 25.0
METRO_ACCESS_MIN = 10.0     # marcher jusqu'à la station, attendre, ressortir
TWO_WHEELS_KMH = 20.0
CAR_KMH = 17.0
CAR_PARKING_MIN = 4.0

# Détection du métro.
METRO_MIN_GAP_S = 150        # au moins 2 min 30 sans position
METRO_MAX_GAP_S = 30 * 60
METRO_MIN_JUMP_M = 800
METRO_KMH_RANGE = (12.0, 45.0)
STATION_RADIUS_M = 400
# Détection d'un véhicule.
FAST_KMH = 25.0
FAST_STREAK = 3
SAMPLE_MAX_S = 90            # positions « sans interruption »
WALK_MAX_KMH = 8.0
WALK_MIN_SAMPLES = 4

_stats: dict[int, dict] = {}          # course → {"n": échantillons, "max": km/h, "streak": rapides de suite}


def travel_minutes(distance_m: float, mode: str | None) -> float:
    km = distance_m / 1000
    if mode == "transport":
        return min(km / WALK_KMH * 60, METRO_ACCESS_MIN + km / METRO_KMH * 60)
    if mode == "voiture":
        return CAR_PARKING_MIN + km / CAR_KMH * 60
    return km / TWO_WHEELS_KMH * 60         # deux-roues, et mode inconnu (comme avant)


def max_distance_km(mode: str | None) -> float:
    cfg = config.get()
    return min(cfg.transport_max_km, cfg.max_distance_km) if mode == "transport" else cfg.max_distance_km


def looks_like_metro(prev_lat: float, prev_lon: float, lat: float, lon: float, seconds: float) -> dict | None:
    """Trou de positions + saut à vitesse de métro (+ stations proches si la liste est chargée)."""
    if not METRO_MIN_GAP_S <= seconds <= METRO_MAX_GAP_S:
        return None
    jump = haversine_m(prev_lat, prev_lon, lat, lon)
    kmh = jump / seconds * 3.6
    if jump < METRO_MIN_JUMP_M or not METRO_KMH_RANGE[0] <= kmh <= METRO_KMH_RANGE[1]:
        return None
    start, end = stations.nearest(prev_lat, prev_lon), stations.nearest(lat, lon)
    if stations.loaded():
        if not (start and end and start[1] <= STATION_RADIUS_M and end[1] <= STATION_RADIUS_M):
            return None
        if start[0] == end[0]:
            return None
    return {"from": start[0] if start else None, "to": end[0] if end else None,
            "km": round(jump / 1000, 1), "minutes": max(1, round(seconds / 60))}


def _sample(course_id: int, kmh: float) -> dict:
    s = _stats.setdefault(course_id, {"n": 0, "max": 0.0, "streak": 0})
    s["n"] += 1
    s["max"] = max(s["max"], kmh)
    s["streak"] = s["streak"] + 1 if kmh >= FAST_KMH else 0
    return s


def final_mode(course: dict) -> str | None:
    """À la livraison : mode détecté (métro / véhicule déjà enregistrés, sinon à pied si c'est net)."""
    s = _stats.pop(course["id"], None)
    if course.get("detected_mode"):
        return course["detected_mode"]
    if s and s["n"] >= WALK_MIN_SAMPLES and s["max"] <= WALK_MAX_KMH:
        return "pied"
    return None


async def _detected(context, livreur: dict, course: dict, mode: str, detail: dict) -> None:
    if course.get("detected_mode") in ("metro", mode):
        return
    course["detected_mode"] = mode
    await db.update_course(course["id"], {"detected_mode": mode})
    declared = livreur.get("transport_mode")
    await db.log_event("transport_detected", course["id"], livreur["id"],
                       {"mode": mode, "declared": declared, **detail})
    contradiction = (mode == "metro" and declared in ("deux_roues", "voiture")) or \
                    (mode == "vehicule" and declared == "transport")
    if contradiction:
        await messaging.notify_dispatch(context.bot, texts.transport_mismatch(course, livreur, mode, detail))


async def observe(context, livreur: dict, lat: float, lon: float, prev: dict | None, now: datetime,
                  live_update: bool) -> None:
    """À chaque position en direct d'un livreur qui a une course en cours. Ne lève jamais."""
    try:
        if not live_update or not prev:
            return
        at = parse_ts(prev.get("updated_at"))
        if at is None:
            return
        seconds = (now - at).total_seconds()
        if seconds <= 0:
            return
        courses = await db.list_assigned_for_livreur(livreur["id"])
        if not courses:
            return
        metro = looks_like_metro(prev["lat"], prev["lon"], lat, lon, seconds)
        kmh = haversine_m(prev["lat"], prev["lon"], lat, lon) / seconds * 3.6
        for course in courses:
            assigned = parse_ts(course.get("assigned_at"))
            if assigned is None or assigned > at:
                continue            # le trajet a commencé avant la course : il ne compte pas
            if metro:
                await _detected(context, livreur, course, "metro", metro)
            elif seconds <= SAMPLE_MAX_S and kmh <= 130:
                s = _sample(course["id"], kmh)
                if s["streak"] >= FAST_STREAK:
                    await _detected(context, livreur, course, "vehicule", {"kmh": round(kmh)})
    except Exception:  # noqa: BLE001 — la détection ne doit jamais gêner le suivi de position
        log.exception("Détection du moyen de déplacement impossible")
