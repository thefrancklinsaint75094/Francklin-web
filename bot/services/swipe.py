"""/swipe : transfert de produits d'un livreur à un autre, sans passer par un box.

    /swipe
    Livreur A > Livreur B     ← celui qui donne > celui qui reçoit (aussi « → », « -> », « vers »)
    3 DIV                     ← quantité puis produit
    2 MSX
    tout                      ← ou : tout ce que le livreur a encore (d'après le tableau)
    cash 350                  ← cash remis à l'autre livreur (facultatif ; seul, c'est un swipe de cash)

Plusieurs transferts : un bloc après l'autre. Dans le tableau Rechargement, un swipe est une ligne :
colonne A celui qui donne, box « Swipe », quantités positives, cash en colonne C, colonne T celui qui
reçoit (SOLDES retire au premier, ajoute au second, produits comme cash). Les box ne bougent pas (ORGA ③
ne compte que les vrais box). Fonctions pures ici ; l'enregistrement est dans handlers/swipe.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.services.restock_text import CASH_RE
from bot.services.sales import resolve_livreur

HEADER_RE = re.compile(r"^(?P<src>.+?)\s*(?:->|→|=>|>|\svers\s)\s*(?P<dst>.+)$", re.I)
ITEM_RE = re.compile(r"^(?P<qty>\d{1,3})\s*[x×]?\s+(?P<product>.+?)$", re.I)
ALL_WORDS = ("tout", "tous", "all", "tout le stock")


@dataclass
class Block:
    src: str
    dst: str
    src_user: dict | None = None
    dst_user: dict | None = None
    items: list[dict] = field(default_factory=list)   # [{"p": "DIV", "q": 3}]
    all: bool = False                                  # « tout » : rempli d'après le stock du tableau
    cash: float = 0.0                                  # cash remis à celui qui reçoit


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _key(text: str) -> str:
    return " ".join((text or "").lower().split())


def _add(items: list[dict], name: str, qty: int) -> None:
    for item in items:
        if item["p"] == name:
            item["q"] += qty
            return
    items.append({"p": name, "q": qty})


def parse(lines: list[tuple[int, str]], catalog, livreur_names: list[str], livreurs: list[dict]) -> Parsed:
    out = Parsed()
    current: Block | None = None
    for n, raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _key(line) in ALL_WORDS:
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord « Livreur A > Livreur B »")
            else:
                current.all = True
            continue
        m = CASH_RE.match(line)
        if m:
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord « Livreur A > Livreur B »")
            else:
                current.cash += float(m["amount"].replace(",", "."))
            continue
        m = ITEM_RE.match(line)
        if m:
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord « Livreur A > Livreur B »")
                continue
            match = catalog.match(m["product"]) if catalog else None
            product = match.product["name"] if match and match.product else None
            qty = int(m["qty"])
            if product is None:
                out.errors.append(f"ligne {n} : produit inconnu « {m['product']} »")
            elif qty <= 0:
                out.errors.append(f"ligne {n} : quantité à 0")
            else:
                _add(current.items, product, qty)
            continue
        h = HEADER_RE.match(line)
        if not h:
            out.errors.append(f"ligne {n} : « {line} » — attendu : « Livreur A > Livreur B », "
                              "puis quantité et produit (ex. 3 DIV) ou « tout »")
            current = None
            continue
        src, src_user = resolve_livreur(h["src"], livreur_names, livreurs)
        dst, dst_user = resolve_livreur(h["dst"], livreur_names, livreurs)
        bad = [f"livreur inconnu « {h[k].strip()} »" for k, v in (("src", src), ("dst", dst)) if v is None]
        if not bad and _key(src) == _key(dst):
            bad = ["le même livreur des deux côtés"]
        if bad:
            out.errors += [f"ligne {n} : {b}" for b in bad]
            current = None
            continue
        current = Block(src, dst, src_user, dst_user)
        out.blocks.append(current)
    for b in out.blocks:
        if b.all and b.items:
            out.errors.append(f"{b.src} > {b.dst} : « tout » ou des produits, pas les deux")
        elif not b.all and not b.items and b.cash <= 0:
            out.errors.append(f"{b.src} > {b.dst} : indique les produits (ex. 3 DIV), « tout » ou le cash (ex. cash 350)")
    return out


def stock_of(name: str, livreurs_stock: dict[str, dict]) -> dict[str, float] | None:
    """Stock d'un livreur dans le résultat de stock.livreurs_now() (noms comparés sans casse)."""
    return next((v for k, v in livreurs_stock.items() if _key(k) == _key(name)), None)


def everything(stock: dict[str, float]) -> list[dict]:
    """« tout » : chaque produit en stock positif, en entier."""
    return [{"p": p, "q": int(q)} for p, q in stock.items() if int(q or 0) > 0]


def shortages(items: list[dict], stock: dict[str, float]) -> list[tuple[str, int, float]]:
    """(produit, demandé, en stock) pour chaque produit que le livreur n'a pas en quantité suffisante."""
    have = {_key(p): float(q or 0) for p, q in stock.items()}
    return [(i["p"], i["q"], have.get(_key(i["p"]), 0.0)) for i in items if have.get(_key(i["p"]), 0.0) < i["q"]]
