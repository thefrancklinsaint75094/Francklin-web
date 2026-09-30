"""/ventes : récapitulatif de ventes envoyé par un admin, écrit dans la feuille Dispatch.

Format (un bloc par livreur, autant de blocs que voulu) :

    Livreur A
    Espèces 2 US 60
    Virement 1 DIV 30

Chaque ligne : mode de paiement (espèces / virement), quantité, produit (nom du catalogue ou alias),
prix total de la ligne (de 10 en 10 €). Fonctions pures ici ; l'enregistrement est dans handlers/sales.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.services.order_edit import valid_price

PAY_WORDS = {
    "especes": ("espece", "especes", "espèce", "espèces", "esp", "cash", "liquide"),
    "virement": ("virement", "virements", "vir", "vrt"),
}
LINE_RE = re.compile(r"^(?P<pay>\S+)\s+(?P<qty>\d{1,3})\s*[x×]?\s+(?P<product>.+?)\s+(?P<price>\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|e|eur|euros?)?$",
                     re.I)
MAX_LINES = 60


def payment_of(word: str) -> str | None:
    w = word.lower().strip(" :-")
    return next((mode for mode, words in PAY_WORDS.items() if w in words), None)


@dataclass
class Block:
    livreur: str                          # nom tel qu'écrit, puis nom de la feuille une fois reconnu
    user: dict | None = None
    lines: list[dict] = field(default_factory=list)   # {"pay": "especes", "q": 2, "p": "US", "x": 60.0}


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(len(b.lines) for b in self.blocks)


def _key(text: str) -> str:
    return " ".join((text or "").lower().split())


def resolve_livreur(text: str, names: list[str], users: list[dict]) -> tuple[str | None, dict | None]:
    """« Livreur A », « livreur a », « A » → nom de la feuille (+ compte du bot s'il existe)."""
    wanted = _key(text)
    by_name = {_key(u.get("display_name") or ""): u for u in users if u.get("display_name")}
    for name in list(names) + [u["display_name"] for u in users if u.get("display_name")]:
        k = _key(name)
        if wanted == k or (len(wanted) <= 2 and k.endswith(" " + wanted)):
            return name, by_name.get(k)
    return None, None


def parse(text: str, catalog, names: list[str], users: list[dict]) -> Parsed:
    out = Parsed()
    current: Block | None = None
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.lower().startswith("/ventes"):
            continue
        m = LINE_RE.match(line)
        if m and payment_of(m["pay"]):
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord le nom du livreur (ex. « Livreur A »)")
                continue
            price = float(m["price"].replace(",", "."))
            match = catalog.match(m["product"]) if catalog else None
            product = match.product["name"] if match and match.product else None
            if product is None:
                out.errors.append(f"ligne {n} : produit inconnu « {m['product']} »")
            elif not valid_price(price):
                out.errors.append(f"ligne {n} : prix {m['price']} € — les prix vont de 10 en 10 €")
            elif int(m["qty"]) <= 0:
                out.errors.append(f"ligne {n} : quantité à 0")
            else:
                current.lines.append({"pay": payment_of(m["pay"]), "q": int(m["qty"]), "p": product, "x": price})
            continue
        if payment_of(line.split()[0]):
            out.errors.append(f"ligne {n} : « {line} » — attendu : paiement, quantité, produit, prix "
                              "(ex. Espèces 2 US 60)")
            continue
        name, user = resolve_livreur(line, names, users)
        if name is None:
            out.errors.append(f"ligne {n} : livreur inconnu « {line} »")
            current = None
            continue
        current = Block(name, user)
        out.blocks.append(current)
    out.blocks = [b for b in out.blocks if b.lines]
    if out.count > MAX_LINES:
        out.errors.append(f"trop de lignes ({out.count}) : envoie-les en plusieurs fois (au plus {MAX_LINES})")
    return out
