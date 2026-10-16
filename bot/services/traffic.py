"""Heure d'arrivée en temps réel pour la course en cours de chaque livreur.

Toutes les TRAFFIC_REFRESH_MINUTES : position en direct du livreur (partagée dans Telegram) → temps de trajet
jusqu'à l'adresse :
- 🚗 voiture, 🛵 deux-roues : TomTom Routing API, avec le trafic du moment (TOMTOM_API_KEY) ;
- 🚶 à pied / transports : calculateur d'itinéraires d'Île-de-France Mobilités (PRIM, IDFM_API_KEY), avec les
  horaires réels et la marche ; l'itinéraire (lignes, arrêts) est montré au livreur.
Une seule course par livreur (celle qu'il fait), position fraîche, pas sur place. Résultat gardé en mémoire
(recalculé au plus TRAFFIC_REFRESH_MINUTES après un redémarrage), affiché dans les applis ; retard sur le temps
annoncé par le livreur : dispatch et franchisés prévenus une fois par course. Sans clé, rien ne se passe.
"""
from __future__ import annotations

import logging
import unicodedata
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
    cfg = config.get()
    return bool(cfg.tomtom_api_key or cfg.idfm_api_key)


IDFM_URL = "https://prim.iledefrance-mobilites.fr/marketplace/v2/navitia/journeys"
TRANSIT_ICON = {"metro": "🚇", "rer": "🚆", "train": "🚆", "transilien": "🚆", "bus": "🚌", "tramway": "🚊",
                "tram": "🚊", "noctilien": "🌙🚌"}


def _nav_time(value: str | None) -> datetime | None:
    """« 20261016T185200 » (heure de Paris) → datetime UTC."""
    from bot.config import PARIS

    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=PARIS).astimezone(now_utc().tzinfo)
    except (TypeError, ValueError):
        return None


def transit_steps(journey: dict) -> list[str]:
    """Sections d'un trajet Navitia → étapes simples : « 🚇 Métro 9 → Montreuil : Oberkampf → Robespierre »,
    « 🚶 6 min à pied »."""
    steps = []
    for s in journey.get("sections") or []:
        kind = s.get("type")
        minutes = round(int(s.get("duration") or 0) / 60)
        if kind == "public_transport":
            info = s.get("display_informations") or {}
            mode = str(info.get("commercial_mode") or info.get("physical_mode") or "").strip()
            plain = unicodedata.normalize("NFKD", mode.lower()).encode("ascii", "ignore").decode()   # « Métro » → metro
            icon = TRANSIT_ICON.get(plain.split()[0] if plain else "", "🚍")
            line = " ".join(x for x in (mode, str(info.get("code") or "")) if x)
            direction = info.get("direction") or ""
            start = (s.get("from") or {}).get("name") or ""
            end = (s.get("to") or {}).get("name") or ""
            steps.append(f"{icon} {line}{' → ' + direction if direction else ''} : {start} → {end}")
        elif kind in ("street_network", "transfer", "crow_fly") and minutes >= 1:
            steps.append(f"🚶 {minutes} min à pied")
        elif kind == "waiting" and minutes >= 1:
            steps.append(f"⏳ {minutes} min d'attente")
    return steps


async def route_transit(lat1: float, lon1: float, lat2: float, lon2: float,
                        client: httpx.AsyncClient | None = None) -> dict | None:
    """{"seconds", "arrival", "steps", "disruptions"} en transports d'Île-de-France (horaires réels), ou None."""
    cfg = config.get()
    if not cfg.idfm_api_key:
        return None
    params = {"from": f"{lon1};{lat1}", "to": f"{lon2};{lat2}", "data_freshness": "realtime", "count": 1}
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        r = await client.get(cfg.idfm_url or IDFM_URL, params=params, headers={"apikey": cfg.idfm_api_key})
        r.raise_for_status()
        data = r.json()
        journey = (data.get("journeys") or [])[0]
        return {"seconds": int(journey["duration"]), "arrival": _nav_time(journey.get("arrival_date_time")),
                "steps": transit_steps(journey), "disruptions": len(data.get("disruptions") or [])}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("IDFM indisponible : %s", exc)
        return None
    finally:
        if own:
            await client.aclose()


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
        if lv.get("transport_mode") == "transport":     # à pied / métro : IDFM
            result = await route_transit(pos["lat"], pos["lon"], course["lat"], course["lon"])
            if result is None:
                continue
            arrival = result["arrival"] or now + timedelta(seconds=result["seconds"])
            _live[course["id"]] = {"minutes": max(1, round((arrival - now).total_seconds() / 60)), "at": arrival,
                                   "delay": 0, "km": None, "updated": now, "livreur_id": livreur_id,
                                   "source": "transports", "steps": result["steps"],
                                   "disruptions": result["disruptions"]}
        else:
            result = await route(pos["lat"], pos["lon"], course["lat"], course["lon"], lv.get("transport_mode"))
            if result is None:
                continue
            arrival = now + timedelta(seconds=result["seconds"])
            _live[course["id"]] = {"minutes": max(1, round(result["seconds"] / 60)), "at": arrival,
                                   "delay": round(result["delay"] / 60), "km": round(result["meters"] / 1000, 1),
                                   "updated": now, "livreur_id": livreur_id, "source": "trafic"}
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
