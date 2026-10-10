"""Goûts (variantes) d'un produit : « MSX » en noisette, fraise, orange, banane.

Même coût, même produit pour la compta : « 12 MSX banane » reste 12 MSX dans les feuilles (le catalogue
reconnaît le produit dans le texte). Le bot retient seulement quels goûts chaque livreur a encore
(oui / non, jamais de quantité) :
- /ravi « 12 MSX banane fraise » (ou « 12 MSX (5 banane, 7 fraise) ») les coche chez le livreur ;
- le livreur coche / décoche avec /gouts ; un produit qu'il n'a plus du tout est décoché tout seul ;
- une commande « 1 MSX banane » : la liste des livreurs proposée au franchisé dit qui en a.
Fonctions pures ici.
"""
from __future__ import annotations

import re

from bot.services.catalog import normalize

SPLIT_RE = re.compile(r"\s*[,;/]\s*|\s+et\s+")
PART_RE = re.compile(r"\s*\+\s*|\s*,\s*(?=\d)|\n")     # lignes d'une commande : « 2 DIV + 1 MSX banane »


def parse_list(text: str) -> list[str]:
    """« noisette, fraise et banane » → [« noisette », « fraise », « banane »] (sans doublon)."""
    out: list[str] = []
    for v in SPLIT_RE.split(text or ""):
        v = " ".join(v.split()).strip(" .:-").lower()
        if v and normalize(v) and normalize(v) not in {normalize(x) for x in out}:
            out.append(v)
    return out


def found_in(text: str, variants: list[str]) -> list[str]:
    """Goûts du produit cités dans le texte (« 12 MSX (5 banane, 7 fraise) » → banane, fraise)."""
    padded = f" {normalize(text)} "
    return [v for v in variants if normalize(v) and f" {normalize(v)} " in padded]


def leftovers(text: str, product: dict, found: list[str]) -> list[str]:
    """Mots qui ne sont ni le produit, ni un goût connu, ni un nombre : sans doute un goût mal écrit."""
    known = set()
    for word in [product["name"], *(product.get("aliases") or []), *found]:
        known.update(normalize(word).split())
    return [w for w in normalize(text).split() if w not in known and not w.isdigit()]


def requested(products_text: str, catalog) -> list[tuple[str, str]]:
    """Goûts demandés dans une commande : [(« MSX », « banane »)]."""
    out: list[tuple[str, str]] = []
    if not catalog:
        return out
    for part in PART_RE.split(products_text or ""):
        match = catalog.match(part) if part.strip() else None
        product = match.product if match else None
        if product and product.get("variants"):
            out += [(product["name"], v) for v in found_in(part, list(product["variants"]))
                    if (product["name"], v) not in out]
    return out


def by_livreur(rows: list[dict]) -> dict[str, dict[str, set[str]]]:
    """Lignes de livreur_variants → {livreur: {produit: {goûts}}}."""
    out: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        out.setdefault(r["livreur_name"], {}).setdefault(r["product"], set()).add(r["variant"])
    return out
