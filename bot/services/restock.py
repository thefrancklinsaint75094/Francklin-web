"""Rechargement des livreurs par un ravitailleur (ou le dispatch), sans rien taper.

Étapes, toutes par boutons dans le même message :
livreur → chargement / reprise / cash seul → box → produits (sélecteur du catalogue,
quantités −1 / +1 / +5) → cash récupéré (±10 / ±50 / ±100 €) → Valider.

Le brouillon est gardé dans l'état de conversation (users.state_payload).
Fonctions pures ici ; les boutons et l'enregistrement sont dans handlers/restock.py.
"""
from __future__ import annotations

KINDS = ("load", "unload", "cash")   # + « swipe » (transfert entre livreurs, /swipe)
QTY_STEPS = (-1, 1, 5)
CASH_STEPS = (-100, -50, -10, 10, 50, 100)
MAX_ITEMS = 15
MAX_QTY = 999
MAX_CASH = 99_999
SWIPE_BOX = "Swipe"   # « box » des transferts entre livreurs (/swipe) : aucun vrai box ne bouge


def change_qty(items: list[dict], idx: int, delta: int) -> list[dict]:
    """Quantité d'un produit ; à 0, le produit disparaît."""
    if not 0 <= idx < len(items):
        return items
    item = dict(items[idx])
    item["q"] = min(MAX_QTY, item["q"] + delta)
    if item["q"] <= 0:
        return items[:idx] + items[idx + 1:]
    return items[:idx] + [item] + items[idx + 1:]


def add_product(items: list[dict], name: str) -> list[dict]:
    """Ajoute 1 × produit (ou +1 s'il y est déjà)."""
    for i, item in enumerate(items):
        if item["p"] == name:
            return change_qty(items, i, 1)
    if len(items) >= MAX_ITEMS:
        return items
    return items + [{"p": name, "q": 1}]


def change_cash(cash: float, delta: float) -> float:
    return float(min(MAX_CASH, max(0.0, round(float(cash) + delta, 2))))


def items_text(items: list[dict], kind: str) -> str:
    """« +12 DIV, +6 KT » (chargement) ou « −3 DIV » (reprise)."""
    sign = "−" if kind == "unload" else "+"
    return ", ".join(f"{sign}{i['q']} {i['p']}" + (f" ({', '.join(i['v'])})" if i.get("v") else "") for i in items)


def signed_quantities(items: list[dict], kind: str) -> dict[str, int]:
    """Pour la feuille : positif = chargé au livreur, négatif = repris. Swipe : positif (le livreur de
    gauche donne au livreur de la colonne T)."""
    sign = -1 if kind == "unload" else 1
    out: dict[str, int] = {}
    for i in items:
        out[i["p"]] = out.get(i["p"], 0) + sign * int(i["q"])
    return out


def is_empty(payload: dict) -> bool:
    return not payload.get("items") and float(payload.get("cash") or 0) <= 0
