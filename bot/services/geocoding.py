"""Géocodage via l'API Adresse (BAN), contrôle du périmètre et calcul du district."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

BAN_URL = "https://api-adresse.data.gouv.fr/search/"


def ban_url() -> str:
    """Surchargeable par la variable BAN_URL si l'API change d'adresse."""
    return os.environ.get("BAN_URL") or BAN_URL
MIN_SCORE = 0.5
TIMEOUT = 20.0
RETRY_DELAY = 2.0

_POSTCODE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")


class GeocodingUnavailable(Exception):
    """BAN injoignable après deux tentatives."""


@dataclass
class GeocodeResult:
    label: str
    postcode: str
    city: str
    lat: float
    lon: float
    score: float
    type: str

    @property
    def has_housenumber(self) -> bool:
        return self.type == "housenumber"


def extract_postcode(address: str) -> str | None:
    m = _POSTCODE_RE.search(address or "")
    return m.group(1) if m else None


def in_zone(postcode: str, allowed: tuple[str, ...] | list[str]) -> bool:
    return bool(postcode) and postcode[:2] in allowed


def district(postcode: str, city: str) -> str:
    """75001–75020 → « Paris 1er » / « Paris 12e » ; 75116 → « Paris 16e » ; sinon « Ville 93100 »."""
    if postcode == "75116":
        return "Paris 16e"
    if postcode.startswith("750") and len(postcode) == 5:
        n = int(postcode[3:])
        if 1 <= n <= 20:
            return "Paris 1er" if n == 1 else f"Paris {n}e"
    return f"{city} {postcode}".strip()


def build_params(address: str) -> dict:
    params = {"q": address[:200], "limit": 1, "autocomplete": 0}
    pc = extract_postcode(address)
    if pc:
        params["postcode"] = pc
    return params


def parse_response(payload: dict) -> GeocodeResult | None:
    features = (payload or {}).get("features") or []
    if not features:
        return None
    f = features[0]
    props = f.get("properties") or {}
    coords = (f.get("geometry") or {}).get("coordinates") or []
    score = float(props.get("score") or 0)
    if score < MIN_SCORE or len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])  # attention : BAN renvoie [lon, lat]
    return GeocodeResult(
        label=props.get("label") or "",
        postcode=str(props.get("postcode") or ""),
        city=props.get("city") or "",
        lat=lat,
        lon=lon,
        score=score,
        type=props.get("type") or "",
    )


async def _get(client: httpx.AsyncClient, params: dict) -> dict:
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = await client.get(ban_url(), params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            log.warning("BAN tentative %d échouée : %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
    raise GeocodingUnavailable(str(last_exc))


async def geocode(address: str, client: httpx.AsyncClient | None = None) -> GeocodeResult | None:
    """Renvoie le meilleur résultat BAN, ou None si l'adresse est introuvable (score < 0,5).

    Si un code postal écrit par le franchisé ne donne rien, on réessaie sans lui
    (code postal erroné plutôt qu'adresse inexistante)."""
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        params = build_params(address)
        result = parse_response(await _get(client, params))
        if result is None and "postcode" in params:
            params.pop("postcode")
            result = parse_response(await _get(client, params))
        return result
    finally:
        if own:
            await client.aclose()


REVERSE_TIMEOUT = 8.0


def reverse_url() -> str:
    """Même API que la recherche : …/search/ → …/reverse/."""
    url = ban_url()
    return url[: url.rstrip("/").rfind("/") + 1] + "reverse/" if "search" in url else url.rstrip("/") + "/reverse/"


async def reverse(lat: float, lon: float, client: httpx.AsyncClient | None = None) -> str | None:
    """Adresse la plus proche d'un point (« 12 Rue de Rivoli 75004 Paris »), ou None. Ne lève jamais."""
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.get(reverse_url(), params={"lat": lat, "lon": lon, "limit": 1}, timeout=REVERSE_TIMEOUT)
        resp.raise_for_status()
        features = resp.json().get("features") or []
        return (features[0].get("properties") or {}).get("label") or None if features else None
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Adresse inverse indisponible : %s", exc)
        return None
    finally:
        if own:
            await client.aclose()
