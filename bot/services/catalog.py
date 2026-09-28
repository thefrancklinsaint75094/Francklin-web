"""Catalogue des produits : saisie par le dispatch et reconnaissance dans les commandes.

Reconnaissance déterministe (sans IA), dans cet ordre :
1. nom ou autre écriture identique, une fois normalisés (majuscules, accents,
   pluriels, contenances « 70cl », « 1,5L » ignorés) ;
2. nom ou autre écriture contenu dans le texte (« bouteille de jack daniels ») ;
3. faute de frappe proche (« absolu » pour « absolut »), par similarité de lettres.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from bot import db

FUZZY_CUTOFF = 0.82
VOLUME_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:cl|ml|l|litres?|lt)\b")
SPLIT_NAME_RE = re.compile(r"\s*[:=]\s*")
SPLIT_ALIASES_RE = re.compile(r"\s*[,;/|]\s*")
BULLET_RE = re.compile(r"^\s*(?:[-•*·–]|\d{1,3}[.)])\s+")


def normalize(text: str) -> str:
    """« Jack Daniel's 70cl » → « jack daniel »."""
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = VOLUME_RE.sub(" ", t)
    t = re.sub(r"['’`]s\b", "", t)          # « daniel's » → « daniel »
    t = re.sub(r"['’`]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    words = []
    for w in t.split():
        if len(w) > 3 and w[-1] in "sx" and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def parse_input(text: str) -> list[tuple[str, list[str]]]:
    """Saisie du dispatch, un produit par ligne :
        Vodka Absolut : absolut, abso
        Coca-Cola 1,5L = coca, coca cola
    """
    out = []
    for line in (text or "").splitlines():
        line = BULLET_RE.sub("", line).strip()
        if not line or line.startswith("/"):
            continue
        parts = SPLIT_NAME_RE.split(line, maxsplit=1)
        name = " ".join(parts[0].split())
        aliases = [a for a in (" ".join(x.split()) for x in SPLIT_ALIASES_RE.split(parts[1])) if a] if len(parts) > 1 else []
        if normalize(name):
            out.append((name, aliases))
    return out


@dataclass
class Match:
    product: dict | None = None
    ambiguous: list[str] = field(default_factory=list)
    fuzzy: bool = False


class Catalog:
    def __init__(self, products: list[dict]):
        self.products = products
        self.index: dict[str, list[dict]] = {}
        for p in products:
            for key in {normalize(p["name"]), *(normalize(a) for a in p.get("aliases") or [])}:
                if key:
                    self.index.setdefault(key, [])
                    if p not in self.index[key]:
                        self.index[key].append(p)

    def __bool__(self) -> bool:
        return bool(self.products)

    def _result(self, candidates: list[dict], fuzzy: bool = False) -> Match:
        if len(candidates) == 1:
            return Match(product=candidates[0], fuzzy=fuzzy)
        return Match(ambiguous=sorted(p["name"] for p in candidates), fuzzy=fuzzy)

    def match(self, text: str) -> Match:
        key = normalize(text)
        if not key:
            return Match()
        if key in self.index:
            return self._result(self.index[key])
        # Nom contenu dans le texte, le plus long d'abord (« jack daniel » avant « jack »)
        padded = f" {key} "
        contained = sorted((k for k in self.index if f" {k} " in padded), key=len, reverse=True)
        if contained:
            best = contained[0]
            return self._result(self.index[best])
        close = difflib.get_close_matches(key, list(self.index), n=1, cutoff=FUZZY_CUTOFF)
        if close:
            return self._result(self.index[close[0]], fuzzy=True)
        return Match()

    def conflicts(self, key: str, product_id: int | None) -> list[str]:
        """Produits (autres que product_id) qui utilisent déjà cette écriture."""
        return [p["name"] for p in self.index.get(key, []) if p["id"] != product_id]


async def load() -> Catalog:
    return Catalog(await db.list_products())


async def add_products(entries: list[tuple[str, list[str]]]) -> dict:
    """Ajoute ou complète des produits. Renvoie un résumé pour le dispatch."""
    summary = {"added": [], "updated": [], "unchanged": [], "conflicts": []}
    catalog = await load()
    by_key = {normalize(p["name"]): p for p in catalog.products}
    for name, aliases in entries:
        key = normalize(name)
        existing = by_key.get(key)
        clean_aliases = []
        for alias in aliases:
            akey = normalize(alias)
            if not akey or akey == key:
                continue
            others = catalog.conflicts(akey, existing["id"] if existing else None)
            if others:
                summary["conflicts"].append((alias, others))
            clean_aliases.append(alias)
        if existing:
            known = {normalize(a) for a in existing.get("aliases") or []}
            new = [a for a in clean_aliases if normalize(a) not in known]
            if new:
                existing = await db.update_product(existing["id"], {"aliases": (existing.get("aliases") or []) + new})
                summary["updated"].append(existing["name"])
            else:
                summary["unchanged"].append(existing["name"])
        else:
            existing = await db.create_product(name, key, clean_aliases)
            summary["added"].append(existing["name"])
        by_key[key] = existing
        catalog = Catalog(list(by_key.values()))
    return summary
