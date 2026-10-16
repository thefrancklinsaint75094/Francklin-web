"""Stock déclaré par le livreur au début et à la fin de son service.

Il écrit ce qu'il a sur lui (« 3 US, 2 DIV », « US 3 », une ligne par produit, ou « rien »), ou touche ✅
si le stock du tableau est exact ; il confirme, et le dispatch voit les écarts avec le tableau.
Fonctions pures ici ; les messages et les boutons sont dans handlers/declare.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NOTHING = ("rien", "aucun", "aucune", "0", "vide", "plus rien", "nothing")
PART_RE = re.compile(r"\s*[,;+\n]\s*|\s+et\s+")
QTY_FIRST = re.compile(r"^(?P<qty>\d{1,3})\s*[x×]?\s+(?P<product>.+?)$", re.I)
QTY_LAST = re.compile(r"^(?P<product>.+?)\s*[x×:=]?\s*(?P<qty>\d{1,3})$", re.I)


@dataclass
class Parsed:
    items: dict[str, int] = field(default_factory=dict)   # {produit du catalogue: quantité}
    errors: list[str] = field(default_factory=list)


def _key(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def looks_like_stock(text: str | None) -> bool:
    """Un stock contient un nombre (ou « rien ») ; « ok merci », « j'arrive » n'en sont pas."""
    return _key(text) in NOTHING or any(c.isdigit() for c in text or "")


def parse(text: str, catalog) -> Parsed:
    out = Parsed()
    text = (text or "").strip()
    if _key(text) in NOTHING:
        return out
    for part in PART_RE.split(text):
        part = part.strip(" .")
        if not part:
            continue
        m = QTY_FIRST.match(part) or QTY_LAST.match(part)
        if not m:
            out.errors.append(f"« {part} » — écris la quantité et le produit (ex. 3 US)")
            continue
        match = catalog.match(m["product"]) if catalog else None
        product = match.product["name"] if match and match.product else None
        if product is None:
            out.errors.append(f"produit inconnu « {m['product']} »")
            continue
        qty = int(m["qty"])
        if qty > 0:
            out.items[product] = out.items.get(product, 0) + qty
    if not out.items and not out.errors:
        out.errors.append("écris au moins un produit (ex. 3 US) ou « rien »")
    return out


def positive(stock: dict | None) -> dict[str, int]:
    """Stock du tableau → {produit: quantité entière > 0}."""
    return {p: int(q) for p, q in (stock or {}).items() if int(float(q or 0)) > 0}


def differences(items: dict[str, int], expected: dict[str, int] | None) -> list[tuple[str, int, int]]:
    """(produit, déclaré, tableau) pour chaque produit où les deux ne sont pas d'accord."""
    if expected is None:
        return []
    declared = {_key(p): (p, q) for p, q in items.items()}
    sheet = {_key(p): (p, q) for p, q in expected.items()}
    out = []
    for k in list(declared) + [k for k in sheet if k not in declared]:
        name = (declared.get(k) or sheet.get(k))[0]
        mine, theirs = declared.get(k, (name, 0))[1], sheet.get(k, (name, 0))[1]
        if mine != theirs:
            out.append((name, mine, theirs))
    return out
