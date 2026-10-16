"""Heure d'arrivée en temps réel, trafic compris (TomTom Routing API), pour la course en cours de chaque livreur.

Toutes les TRAFFIC_REFRESH_MINUTES : position en direct du livreur (partagée dans Telegram) → temps de trajet
jusqu'à l'adresse avec le trafic du moment. Une seule course par livreur (celle qu'il fait), seulement avec une
position fraîche, pas sur place, pas en transports en commun (TomTom ne les connaît pas : l'estimation
habituelle reste). Résultat gardé en mémoire (recalculé au plus TRAFFIC_REFRESH_MINUTES après un redémarrage),
affiché dans les applis ; retard sur le temps annoncé par le livreur : dispatch et franchisés prévenus une
fois par course. Sans TOMTOM_API_KEY, rien ne se passe.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from bot import config, db, messaging, texts
from bot.services.distance import haversine_m
from bot.timeutil import hhmm, now_utc, parse_ts

log = logging.getLogger(__name__)

URL = "https://api.tomtom.com/routing/1/calculateRoute/{a},{b}:{c},{d}/json"
TRAVEL_MODE = {"voiture": "car", "deux_roues": "motorcycle"}   # transport en commun : pas de calcul TomTom
ONSITE_METERS = 150
LATE_MINUTES = 10                       # au-delà du temps annoncé : alerte

_live: dict[int, dict] = {}             # course → {"minutes", "at", "delay", "km", "updated", "livreur_id"}
_late_alerted: set[tuple[int, str]] = set()


def enabled() -> bool:
    return bool(config.get().tomtom_api_key)


def live(course_id: int) -> dict | None:
    """Dernier calcul pour cette course, s'il a moins de 3 rafraîchissements."""
    row = _live.get(course_id)
    if row and now_utc() - row["updated"] <= timedelta(minutes=3 * config.get().traffic_refresh_minutes):
        return row
    return None


async def route(lat1: float, lon1: float, lat2: float, lon2: float, mode: str | None,
                client: httpx.AsyncClient | None = None) -> dict | None:
    """{"seconds", "delay", "meters"} avec le trafic du moment, ou None (pas de clé, mode non géré, erreur)."""
    travel = TRAVEL_MODE.get(mode or "deux_roues")
    key = config.get().tomtom_api_key
    if not key or travel is None:
        return None
    params = {"key": key, "traffic": "true", "travelMode": travel, "routeType": "fastest"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=8.0)
    try:
        r = await client.get(URL.format(a=lat1, b=lon1, c=lat2, d=lon2), params=params)
        r.raise_for_status()
        summary = r.json()["routes"][0]["summary"]
        return {"seconds": int(summary["travelTimeInSeconds"]), "delay": int(summary.get("trafficDelayInSeconds") or 0),
                "meters": int(summary.get("lengthInMeters") or 0)}
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        log.warning("TomTom indisponible : %s", exc)
        return None
    finally:
        if own:
            await client.aclose()


async def refresh(context) -> None:
    """Job : met à jour l'arrivée de la course en cours de chaque livreur ; alerte de retard une fois."""
    if not enabled():
        return
    cfg = config.get()
    now = now_utc()
    assigned = await db.list_courses_by_status("assigned")
    current: dict[str, dict] = {}
    for c in sorted(assigned, key=lambda c: (c.get("assigned_at") or "", c["id"])):
        current.setdefault(c["livreur_id"], c)              # une seule adresse à la fois : la plus ancienne
    if not current:
        _live.clear()
        return
    current_ids = {c["id"] for c in current.values()}
    for cid in list(_live):                                  # livrée, rendue, ou plus la course en cours
        if cid not in current_ids:
            _live.pop(cid, None)
    positions = await db.get_positions(current.keys())
    users = await db.get_users(current.keys())
    for livreur_id, course in current.items():
        pos = positions.get(livreur_id)
        lv = users.get(livreur_id) or {}
        at = parse_ts(pos["updated_at"]) if pos else None
        if not pos or at is None or at < now - timedelta(minutes=cfg.position_stale_minutes) or course.get("lat") is None:
            continue
        if haversine_m(pos["lat"], pos["lon"], course["lat"], course["lon"]) <= ONSITE_METERS:
            continue
        result = await route(pos["lat"], pos["lon"], course["lat"], course["lon"], lv.get("transport_mode"))
        if result is None:
            continue
        arrival = now + timedelta(seconds=result["seconds"])
        _live[course["id"]] = {"minutes": max(1, round(result["seconds"] / 60)), "at": arrival,
                               "delay": round(result["delay"] / 60), "km": round(result["meters"] / 1000, 1),
                               "updated": now, "livreur_id": livreur_id}
        await _late_check(context, course, lv, arrival)


async def _late_check(context, course: dict, livreur: dict, arrival: datetime) -> None:
    announced = parse_ts(course.get("eta_at")) if course.get("eta_at") else None
    key = (course["id"], course.get("eta_at") or "")
    if announced is None or key in _late_alerted:
        return
    if arrival - announced >= timedelta(minutes=LATE_MINUTES):
        _late_alerted.add(key)
        await db.log_event("late_traffic", course["id"], livreur.get("id"),
                           {"announced": course.get("eta_at"), "traffic": arrival.isoformat()})
        await messaging.notify_dispatch(context.bot, texts.d_late_traffic(course, livreur, hhmm(announced), hhmm(arrival)),
                                        important=True)
