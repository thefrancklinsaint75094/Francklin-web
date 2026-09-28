"""Envoi des courses livrées vers Google Sheets (facultatif).

Le bot poste les lignes à un petit script Google Apps Script attaché à la
feuille (integrations/google_sheets/Code.gs), protégé par un secret partagé.
Le script ignore une course déjà présente : renvoyer une ligne est sans danger.

Sans GOOGLE_SHEETS_WEBHOOK_URL, rien n'est envoyé. Un échec n'interrompt
jamais le bot : il est journalisé, et /synchro permet de renvoyer la nuit.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from bot import db
from bot.timeutil import parse_ts, to_paris

log = logging.getLogger(__name__)

TIMEOUT = 20.0
RETRY_DELAY = 2.0
_tasks: set[asyncio.Task] = set()


def webhook() -> tuple[str | None, str | None]:
    return os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL") or None, os.environ.get("GOOGLE_SHEETS_SECRET") or None


def enabled() -> bool:
    url, secret = webhook()
    return bool(url and secret)


def row(course: dict, users: dict[str, dict]) -> dict:
    at = to_paris(parse_ts(course["delivered_at"]))
    return {
        "numero": course["id"],
        "date": at.strftime("%Y-%m-%d"),
        "heure": at.strftime("%H:%M"),
        "franchise": users.get(course["franchise_id"], {}).get("display_name", ""),
        "livreur": users.get(course.get("livreur_id"), {}).get("display_name", ""),
        "adresse": course["address"],
        "complement": course.get("address_detail") or "",
        "produits": course["products"],
        "prix": round(float(course["price"]), 2),
    }


async def send_rows(rows: list[dict], client: httpx.AsyncClient | None = None) -> int:
    """Envoie des lignes. Renvoie le nombre de lignes ajoutées par le script.
    Lève RuntimeError si l'envoi échoue deux fois."""
    url, secret = webhook()
    if not (url and secret) or not rows:
        return 0
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    last: Exception | None = None
    try:
        for attempt in range(2):
            try:
                # Apps Script répond par une redirection vers le résultat : on la suit.
                resp = await client.post(url, json={"secret": secret, "rows": rows}, timeout=TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"réponse du script : {data.get('error')}")
                return int(data.get("added", 0))
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last = exc
                log.warning("Google Sheets tentative %d échouée : %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
    finally:
        if own:
            await client.aclose()
    raise RuntimeError(str(last))


async def push_delivered(course: dict) -> None:
    """Ajoute une course livrée à la feuille. Ne lève jamais d'exception."""
    if not enabled():
        return
    try:
        users = await db.get_users([course["franchise_id"], course.get("livreur_id")])
        await send_rows([row(course, users)])
    except Exception as exc:  # noqa: BLE001 — la feuille ne doit jamais bloquer une livraison
        log.warning("Course #%s non envoyée à Google Sheets : %s", course.get("id"), exc)
        try:
            await db.log_event("sheet_error", course.get("id"), payload={"error": str(exc)[:300]})
        except Exception:  # noqa: BLE001
            pass


def push_delivered_later(course: dict) -> None:
    """Envoi en arrière-plan : le bouton « Livré » répond sans attendre Google."""
    if not enabled():
        return
    task = asyncio.get_running_loop().create_task(push_delivered(dict(course)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
