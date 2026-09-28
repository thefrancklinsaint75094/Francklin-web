"""Modification de la commande par le livreur, sur place, sans rien taper.

Le livreur voit les lignes de la commande (produit, quantité, prix de la ligne)
et les change avec des boutons : ➖ / ➕ sur la quantité, choix d'un produit du
catalogue pour en ajouter un, ajustement du prix d'une ligne par boutons
(ou en tapant un nombre, s'il préfère). Le brouillon est gardé dans l'état de
conversation du livreur (users.state_payload) jusqu'à « Valider ».

Fonctions pures ici ; les boutons et l'enregistrement sont dans handlers/livreur.py.
"""
from __future__ import annotations

import re

from bot.services import sheets

PRICE_STEPS = (-10, -5, -1, 1, 5, 10)
PAGE_SIZE = 24          # produits par page du sélecteur (8 rangées de 3)
MAX_LINES = 15
MAX_QTY = 99
PRICE_RE = re.compile(r"^\s*(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|e|eur|euros?)?\s*$", re.I)


def lines_from_course(course: dict, catalog=None) -> list[dict]:
    """Lignes de départ : [{p: nom, q: quantité, x: prix de la ligne}]."""
    out = []
    for line in sheets.product_lines(course.get("products") or "", float(course.get("price") or 0), catalog):
        if not line["produit"]:
            continue
        qty = line["qte"] if isinstance(line["qte"], int) and line["qte"] > 0 else 1
        price = float(line["prix"]) if line["prix"] not in ("", None) else 0.0
        out.append({"p": line["produit"], "q": qty, "x": round(price, 2)})
    return out


def total(lines: list[dict]) -> float:
    return round(sum(float(l["x"]) for l in lines), 2)


def _unit(line: dict) -> float | None:
    return float(line["x"]) / line["q"] if line["q"] > 0 and float(line["x"]) > 0 else None


def change_qty(lines: list[dict], idx: int, delta: int) -> list[dict]:
    """➖ / ➕ : le prix de la ligne suit la quantité (prix unitaire gardé).
    Une ligne qui tombe à 0 disparaît."""
    if not 0 <= idx < len(lines):
        return lines
    line = dict(lines[idx])
    unit = _unit(line)
    line["q"] = min(MAX_QTY, line["q"] + delta)
    if line["q"] <= 0:
        return lines[:idx] + lines[idx + 1:]
    if unit is not None:
        line["x"] = round(unit * line["q"], 2)
    return lines[:idx] + [line] + lines[idx + 1:]


def add_product(lines: list[dict], name: str) -> tuple[list[dict], int]:
    """Ajoute 1 × produit (ou +1 s'il y est déjà). Renvoie les lignes et l'index de la ligne."""
    for i, line in enumerate(lines):
        if line["p"] == name:
            return change_qty(lines, i, 1), i
    if len(lines) >= MAX_LINES:
        return lines, -1
    return lines + [{"p": name, "q": 1, "x": 0.0}], len(lines)


def change_price(lines: list[dict], idx: int, delta: float) -> list[dict]:
    if not 0 <= idx < len(lines):
        return lines
    line = dict(lines[idx])
    line["x"] = max(0.0, round(float(line["x"]) + delta, 2))
    return lines[:idx] + [line] + lines[idx + 1:]


def set_price(lines: list[dict], idx: int, price: float) -> list[dict]:
    if not 0 <= idx < len(lines):
        return lines
    line = dict(lines[idx])
    line["x"] = max(0.0, round(price, 2))
    return lines[:idx] + [line] + lines[idx + 1:]


def parse_price(text: str) -> float | None:
    m = PRICE_RE.match(text or "")
    return float(m.group(1).replace(",", ".")) if m else None


def _eur(value: float) -> str:
    return (f"{value:.2f}".replace(".", ",").replace(",00", "")) + " €"


def products_text(lines: list[dict]) -> str:
    """Même écriture que le modèle de commande : « 2 DIV (60 €) + 1 KT (5 €) »,
    que Google Sheets sait redécouper."""
    if len(lines) == 1:
        return f"{lines[0]['q']} {lines[0]['p']}"
    return " + ".join(f"{l['q']} {l['p']} ({_eur(float(l['x']))})" for l in lines)


def missing_prices(lines: list[dict]) -> list[str]:
    return [l["p"] for l in lines if float(l["x"]) <= 0]


def same(a: list[dict], b: list[dict]) -> bool:
    key = lambda ls: [(l["p"], l["q"], round(float(l["x"]), 2)) for l in ls]  # noqa: E731
    return key(a) == key(b)
