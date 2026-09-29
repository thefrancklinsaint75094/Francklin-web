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


def restock_row(restock: dict, users: dict[str, dict]) -> dict:
    """Rechargement pour le tableau Rechargement : quantités signées
    (+ chargé au livreur, − repris), cash récupéré, box, ravitailleur."""
    from bot.services import restock as rs

    at = to_paris(parse_ts(restock["created_at"]))
    livreur = users.get(restock["livreur_id"], {})
    by = users.get(restock["by_user_id"], {})
    return {
        "type": "recharge",
        "numero": restock["id"],
        "onglet": day_tab(restock["created_at"]),
        "heure": at.strftime("%H:%M"),
        "livreur": livreur.get("display_name", ""),
        "livreur_nom": livreur.get("real_name") or "",
        "box": restock.get("box") or "",
        "cash": round(float(restock.get("cash") or 0), 2),
        "produits": rs.signed_quantities(restock.get("items") or [], restock["kind"]),
        "ravitailleur": by.get("display_name", ""),
        "ravitailleur_nom": by.get("real_name") or "",
    }


async def push_restock(restock: dict) -> None:
    """Ajoute un rechargement au tableau Rechargement. Ne lève jamais d'exception."""
    if not enabled():
        return
    try:
        users = await db.get_users([restock["livreur_id"], restock["by_user_id"]])
        await send_rows([restock_row(restock, users)])
    except Exception as exc:  # noqa: BLE001 — la feuille ne doit jamais bloquer le rechargement
        log.warning("Rechargement R#%s non envoyé à Google Sheets : %s", restock.get("id"), exc)
        try:
            await db.log_event("sheet_error", payload={"restock_id": restock.get("id"), "error": str(exc)[:300]})
        except Exception:  # noqa: BLE001
            pass


def push_restock_later(restock: dict) -> None:
    if not enabled():
        return
    task = asyncio.get_running_loop().create_task(push_restock(dict(restock)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def load_catalog():
    """Catalogue des produits, pour écrire le nom exact de la liste de la feuille.
    Sans catalogue (erreur), les produits partent tels qu'écrits."""
    from bot.services import catalog
    try:
        return await catalog.load()
    except Exception as exc:  # noqa: BLE001
        log.warning("Catalogue indisponible pour Google Sheets : %s", exc)
        return None


TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)


def explain_non_json(resp: httpx.Response) -> str:
    """Le script a renvoyé une page web au lieu de sa réponse : dire pourquoi, en clair."""
    body = resp.text or ""
    where = str(resp.url)
    low = body.lower()
    if "accounts.google.com" in where or "servicelogin" in low or "signin" in where:
        return ("le script demande une connexion Google : dans Apps Script, Déployer → Gérer les déploiements "
                "→ ✏️, mets « Qui a accès : Tout le monde »")
    if "authorization is required" in low or "autorisation requise" in low or "authorisation" in low:
        return ("le script doit être autorisé : ouvre-le dans script.google.com, lance doPost une fois "
                "(▶ Exécuter) et accepte l'autorisation, puis redéploie")
    if resp.status_code == 404 or "script function not found" in low or "fonction de script introuvable" in low:
        return ("adresse du script introuvable ou déploiement supprimé : vérifie l'adresse /exec "
                "(Déployer → Gérer les déploiements)")
    title = TITLE_RE.search(body)
    label = " ".join(title.group(1).split())[:80] if title else " ".join(body.split())[:80]
    return f"réponse inattendue du script (HTTP {resp.status_code}) : {label or 'vide'}"


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
                try:
                    data = resp.json()
                except ValueError:
                    raise RuntimeError(explain_non_json(resp)) from None
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
