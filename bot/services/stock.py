"""Alertes de stock du livreur, lues dans le tableau Rechargement (onglet SOLDES :
« stock actuel chez le livreur = chargé − repris − vendu OK »).

- À l'attribution d'une course : si elle prend les derniers exemplaires d'un produit
  (« ce sont les 2 derniers US ») ou plus qu'il n'en a, alerte.
- À la livraison : stock lu AVANT d'envoyer la vente à la feuille, moins les quantités
  livrées ; s'il ne reste rien d'un produit (« n'a plus de US sur lui »), alerte.

Prévenus : le franchisé de la course, le dispatch, le livreur et les ravitailleurs actifs.
Le stock vient du script Google (action « stock ») : sans réponse, pas d'alerte.
"""
from __future__ import annotations

import asyncio
import logging

from bot import config, db, messaging, texts
from bot.services import sheets

log = logging.getLogger(__name__)


def _key(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def course_quantities(course: dict, catalog=None) -> dict[str, int]:
    """{produit: quantité} d'une course (noms du catalogue quand ils sont reconnus)."""
    out: dict[str, int] = {}
    for line in sheets.product_lines(course.get("products") or "", float(course.get("price") or 0), catalog):
        if line["produit"] and isinstance(line["qte"], int):
            out[line["produit"]] = out.get(line["produit"], 0) + line["qte"]
    return out


def _have(stock: dict[str, float], product: str) -> float:
    wanted = _key(product)
    return next((float(v or 0) for k, v in stock.items() if _key(k) == wanted), 0.0)


def assignment_warnings(quantities: dict[str, int], stock: dict[str, float]) -> list[tuple[str, int, float]]:
    """(produit, commandé, en stock) quand la course prend tout ce qui reste, ou plus."""
    out = []
    for product, need in quantities.items():
        have = _have(stock, product)
        if need >= have:
            out.append((product, need, have))
    return out


def emptied_after_delivery(quantities: dict[str, int], stock_before: dict[str, float]) -> list[str]:
    """Produits dont il ne reste plus rien une fois la course livrée."""
    return [p for p, q in quantities.items() if _have(stock_before, p) - q <= 0]


def enabled() -> bool:
    return config.get().stock_alerts and sheets.enabled()


async def _recipients(course: dict, livreur: dict) -> list[dict]:
    users = [u for u in [await db.get_user(course["franchise_id"]), livreur] if u]
    users += await db.list_users(role="ravitailleur", status="active")
    seen, out = set(), []
    for u in users:
        if u["id"] not in seen and u["role"] != "dispatch":
            seen.add(u["id"])
            out.append(u)
    return out


async def _broadcast(context, course: dict, livreur: dict, text: str) -> None:
    for user in await _recipients(course, livreur):
        await messaging.send(context.bot, user, text)
    await messaging.notify_dispatch(context.bot, text)


async def check_assignment(context, course: dict, livreur: dict) -> None:
    """Ne lève jamais d'exception."""
    try:
        stock, sheet_name = await sheets.fetch_stock(livreur)
        if stock is None:
            return
        quantities = course_quantities(course, await sheets.load_catalog())
        warnings = assignment_warnings(quantities, stock)
        if warnings:
            await db.log_event("stock_warning", course["id"], livreur["id"],
                               {"stock": {p: h for p, _, h in warnings}})
            await _broadcast(context, course, livreur,
                             texts.stock_assignment_alert(course, livreur, sheet_name, warnings))
    except Exception:  # noqa: BLE001
        log.exception("Alerte de stock (attribution) impossible")


async def deliver_then_push(context, course: dict, livreur: dict | None) -> None:
    """Stock lu avant l'envoi de la vente, alerte « plus de … sur lui », puis envoi à la feuille."""
    try:
        if livreur is not None:
            stock, sheet_name = await sheets.fetch_stock(livreur)
            if stock is not None:
                quantities = course_quantities(course, await sheets.load_catalog())
                empty = emptied_after_delivery(quantities, stock)
                if empty:
                    await db.log_event("stock_empty", course["id"], livreur["id"], {"products": empty})
                    await _broadcast(context, course, livreur,
                                     texts.stock_empty_alert(course, livreur, sheet_name, empty))
    except Exception:  # noqa: BLE001
        log.exception("Alerte de stock (livraison) impossible")
    await sheets.push_delivered(course)


def _later(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    sheets._tasks.add(task)
    task.add_done_callback(sheets._tasks.discard)


def after_assignment_later(context, course: dict, livreur: dict) -> None:
    if enabled():
        _later(check_assignment(context, dict(course), livreur))


def after_delivery_later(context, course: dict, livreur: dict | None) -> None:
    """Remplace sheets.push_delivered_later : même envoi, précédé de l'alerte de stock."""
    if not sheets.enabled():
        return
    if enabled():
        _later(deliver_then_push(context, dict(course), livreur))
    else:
        sheets.push_delivered_later(course)
