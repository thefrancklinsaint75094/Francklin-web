"""Envoi des courses livrées vers Google Sheets (facultatif).

Le bot poste les courses à un petit script Google Apps Script
(integrations/google_sheets/Code.gs), protégé par un secret partagé. Le script
écrit chaque course dans l'onglet du jour de la nuit (Lundi … Dimanche), sur la
première ligne de commande vide, avec le statut « OK ». Il ignore une course déjà
présente : renvoyer une course est sans danger.

Sans GOOGLE_SHEETS_WEBHOOK_URL, rien n'est envoyé. Un échec n'interrompt
jamais le bot : il est journalisé, et /synchro permet de renvoyer la nuit.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import httpx

from bot import config, db
from bot.timeutil import night_start_date, parse_ts, to_paris

log = logging.getLogger(__name__)

TIMEOUT = 20.0
RETRY_DELAY = 2.0
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
STATUT_LIVRE = "OK"
_tasks: set[asyncio.Task] = set()

LINE_PRICE_RE = re.compile(r"\s*\((\d+(?:[.,]\d+)?)\s*€\)\s*$")
LEADING_QTY_RE = re.compile(r"^\s*(\d{1,3})\s*[x×*]?\s+(?=\S)", re.I)
TRAILING_QTY_RE = re.compile(r"\s+[x×*]\s*(\d{1,3})\s*$", re.I)
SPLIT_RE = re.compile(r"\s*(?:\+|;|\n|,(?!\d))\s*")  # « 1,5L » reste entier


def webhook() -> tuple[str | None, str | None]:
    return os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL") or None, os.environ.get("GOOGLE_SHEETS_SECRET") or None


def enabled() -> bool:
    url, secret = webhook()
    return bool(url and secret)


def day_tab(delivered_at) -> str:
    """Onglet de la nuit : une course livrée à 2h dans la nuit de lundi à mardi va dans « Lundi »."""
    night = night_start_date(parse_ts(delivered_at), config.get().night_end_hour)
    return JOURS[night.weekday()]


def product_lines(products: str, total: float, catalog=None) -> list[dict]:
    """« 2 DIV (60 €) + 1 KT (5 €) » → [{produit: DIV, qte: 2, prix: 60}, {produit: KT, qte: 1, prix: 5}].

    Sans prix par ligne, le total de la course va sur la première ligne : le total
    de la feuille reste juste. Sans quantité écrite, la quantité est 1."""
    lines = []
    for part in SPLIT_RE.split(products or ""):
        if not part.strip():
            continue
        price = None
        m = LINE_PRICE_RE.search(part)
        if m:
            price = float(m.group(1).replace(",", "."))
            part = part[:m.start()]
        qty = 1
        m = LEADING_QTY_RE.match(part)
        if m:
            qty, part = int(m.group(1)), part[m.end():]
        else:
            m = TRAILING_QTY_RE.search(part)
            if m:
                qty, part = int(m.group(1)), part[:m.start()]
        name = " ".join(part.split())
        if catalog:
            match = catalog.match(name)
            if match.product:
                name = match.product["name"]
        lines.append({"produit": name, "qte": qty, "prix": price})
    if not lines:
        lines = [{"produit": "", "qte": "", "prix": None}]
    prices = [line["prix"] for line in lines]
    if None in prices or round(sum(prices), 2) != round(total, 2):
        for i, line in enumerate(lines):
            line["prix"] = total if i == 0 else ""
    return lines


def row(course: dict, users: dict[str, dict], catalog=None) -> dict:
    at = to_paris(parse_ts(course["delivered_at"]))
    franchise = users.get(course["franchise_id"], {})
    livreur = users.get(course.get("livreur_id"), {})
    total = round(float(course["price"]), 2)
    return {
        "numero": course["id"],
        "onglet": day_tab(course["delivered_at"]),
        "date": at.strftime("%Y-%m-%d"),
        "heure": at.strftime("%H:%M"),
        "vendeur": franchise.get("display_name", ""),
        "vendeur_nom": franchise.get("real_name") or "",
        "livreur": livreur.get("display_name", ""),
        "livreur_nom": livreur.get("real_name") or "",
        "statut": STATUT_LIVRE,
        "adresse": course["address"],
        "lignes": product_lines(course["products"], total, catalog),
        "prix": total,
    }


async def load_catalog():
    """Catalogue des produits, pour écrire le nom exact de la liste de la feuille.
    Sans catalogue (erreur), les produits partent tels qu'écrits."""
    from bot.services import catalog
    try:
        return await catalog.load()
    except Exception as exc:  # noqa: BLE001
        log.warning("Catalogue indisponible pour Google Sheets : %s", exc)
        return None


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
        await send_rows([row(course, users, await load_catalog())])
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
