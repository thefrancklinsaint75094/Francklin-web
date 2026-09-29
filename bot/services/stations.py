"""Stations de métro et de RER d'Île-de-France (données ouvertes IDFM), pour reconnaître un
trajet en métro : le livreur disparaît près d'une station et réapparaît près d'une autre.

La liste est téléchargée au démarrage (STATIONS_URL pour changer d'adresse, « - » pour s'en
passer). Sans liste, la détection du métro se fait sur le trou de positions et la vitesse seules."""
from __future__ import annotations

import csv
import io
import logging

import httpx

from bot import config
from bot.services.distance import haversine_m

log = logging.getLogger(__name__)

DEFAULT_URL = ("https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/"
               "emplacement-des-gares-idf/exports/csv?delimiter=%3B")
UNDERGROUND = ("METRO", "RER")     # là où le réseau se perd ; tram, train et bus roulent en surface
TIMEOUT = 30.0

_stations: list[tuple[float, float, str]] = []


def loaded() -> bool:
    return bool(_stations)


def set_stations(stations: list[tuple[float, float, str]]) -> None:
    _stations[:] = stations


def parse_csv(text: str) -> list[tuple[float, float, str]]:
    """CSV IDFM (« ; ») : colonne geo_point_2d « lat, lon », colonne mode (METRO, RER…),
    nom dans nom_gares / nom_long / nom. Une même station (plusieurs lignes) n'est gardée qu'une fois."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    out, seen = [], set()
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        mode = row.get("mode", "").upper()
        if mode and mode not in UNDERGROUND:
            continue
        geo = next((v for k, v in row.items() if k.startswith("geo_point")), "")
        try:
            lat, lon = (float(x) for x in geo.split(",")[:2])
        except ValueError:
            continue
        name = row.get("nom_gares") or row.get("nom_long") or row.get("nom") or "?"
        key = (round(lat, 4), round(lon, 4))
        if key not in seen:
            seen.add(key)
            out.append((lat, lon, name))
    return out


async def load(client: httpx.AsyncClient | None = None) -> int:
    """Télécharge la liste. Renvoie le nombre de stations (0 si indisponible). Ne lève jamais."""
    url = config.get().stations_url or DEFAULT_URL
    if url == "-":
        return 0
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    try:
        resp = await client.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        stations = parse_csv(resp.text)
        set_stations(stations)
        log.info("Stations IDF chargées : %d (métro et RER)", len(stations))
        return len(stations)
    except Exception as exc:  # noqa: BLE001 — sans stations, la détection reste possible (moins sûre)
        log.warning("Stations IDF indisponibles : %s", exc)
        return 0
    finally:
        if own:
            await client.aclose()


def nearest(lat: float, lon: float) -> tuple[str, float] | None:
    """(nom, distance en m) de la station la plus proche, ou None sans liste."""
    if not _stations:
        return None
    best = min(_stations, key=lambda s: haversine_m(lat, lon, s[0], s[1]))
    return best[2], haversine_m(lat, lon, best[0], best[1])
