"""/ravi : rechargements saisis en texte, au même format que /ventes.

    /ravi 1              ← le ravitailleur (Ravitailleur 1) ; sans numéro : celui qui écrit
    Livreur A            ← le livreur qui reçoit
    Box 1                ← le box d'où sortent les produits (gardé pour les livreurs suivants)
    12 DIV               ← chargé au livreur
    12 MSX banane fraise ← avec les goûts (ou « 12 MSX (5 banane, 7 fraise) ») : cochés chez le livreur
    -2 MSX               ← repris au livreur (retourne dans le box)
    cash 300             ← cash récupéré auprès du livreur

Plusieurs livreurs : un bloc après l'autre. Fonctions pures ici ; l'enregistrement est dans
handlers/restock_text.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.services import variants as variants_svc
from bot.services.sales import resolve_livreur

ITEM_RE = re.compile(r"^(?P<sign>[-−+]?)\s*(?P<qty>\d{1,3})\s*[x×]?\s+(?P<product>.+?)$", re.I)
CASH_RE = re.compile(r"^(?:cash|esp[eè]ces?|r[eé]cup[eé]r[eé]|argent)\s*:?\s*(?P<amount>\d{1,6}(?:[.,]\d{1,2})?)\s*(?:€|e|eur|euros?)?$",
                     re.I)
BOX_RE = re.compile(r"^box\s*(?P<num>\S+)$", re.I)


@dataclass
class Block:
    livreur: str
    user: dict | None = None
    box: str | None = None
    load: list[dict] = field(default_factory=list)      # [{"p": "DIV", "q": 12}]
    unload: list[dict] = field(default_factory=list)
    cash: float = 0.0

    def empty(self) -> bool:
        return not self.load and not self.unload and self.cash <= 0


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # goût inconnu : la ligne passe, le goût est ignoré


def _key(text: str) -> str:
    return " ".join((text or "").lower().split())


def resolve_ravitailleur(arg: str, names: list[str], users: list[dict]) -> tuple[str | None, dict | None]:
    """« 1 », « ravi 1 », « Ravitailleur 1 » → (nom de la feuille, compte du bot s'il existe)."""
    wanted = _key(re.sub(r"^ravi(tailleur)?\s*", "", _key(arg)))
    by_name = {_key(u.get("display_name") or ""): u for u in users if u.get("display_name")}
    for name in list(names) + [u["display_name"] for u in users if u.get("display_name")]:
        k = _key(name)
        if wanted and (wanted == k or k.endswith(" " + wanted)):
            return name, by_name.get(k)
    return None, None


def resolve_box(num: str, boxes: tuple[str, ...]) -> str | None:
    wanted = _key(num)
    return next((b for b in boxes if _key(b) in (wanted, f"box {wanted}") or _key(b).endswith(" " + wanted)), None)


def _add(items: list[dict], name: str, qty: int, variants: list[str] | None = None) -> None:
    for item in items:
        if item["p"] == name:
            item["q"] += qty
            if variants:
                item["v"] = list(dict.fromkeys([*item.get("v", []), *variants]))
            return
    items.append({"p": name, "q": qty, **({"v": list(variants)} if variants else {})})


def parse(lines: list[str], catalog, livreur_names: list[str], livreurs: list[dict],
          boxes: tuple[str, ...]) -> Parsed:
    out = Parsed()
    current: Block | None = None
    box: str | None = None
    for n, raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = BOX_RE.match(line)
        if m:
            box = resolve_box(m["num"], boxes)
            if box is None:
                out.errors.append(f"ligne {n} : box inconnu « {line} » (box : {', '.join(boxes)})")
            elif current is not None:
                current.box = box
            continue
        m = CASH_RE.match(line)
        if m:
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord le nom du livreur (ex. « Livreur A »)")
            else:
                current.cash += float(m["amount"].replace(",", "."))
            continue
        m = ITEM_RE.match(line)
        if m:
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord le nom du livreur (ex. « Livreur A »)")
                continue
            match = catalog.match(m["product"]) if catalog else None
            product = match.product["name"] if match and match.product else None
            qty = int(m["qty"])
            if product is None:
                out.errors.append(f"ligne {n} : produit inconnu « {m['product']} »")
            elif qty <= 0:
                out.errors.append(f"ligne {n} : quantité à 0")
            else:
                known = list(match.product.get("variants") or [])
                found = variants_svc.found_in(m["product"], known) if known else []
                odd = variants_svc.leftovers(m["product"], match.product, found) if known else []
                if odd:
                    out.warnings.append(f"ligne {n} : goût « {' '.join(odd)} » inconnu pour {product} "
                                        f"(goûts : {', '.join(known)}) — ignoré")
                _add(current.unload if m["sign"] in ("-", "−") else current.load, product, qty, found)
            continue
        if line[:1].isdigit() or line[:1] in "-−+":
            out.errors.append(f"ligne {n} : « {line} » — attendu : quantité puis produit (ex. 12 DIV, ou -2 MSX)")
            continue
        name, user = resolve_livreur(line, livreur_names, livreurs)
        if name is None:
            out.errors.append(f"ligne {n} : livreur inconnu « {line} »")
            current = None
            continue
        current = Block(name, user, box)
        out.blocks.append(current)
    for b in out.blocks:
        if (b.load or b.unload) and not b.box:
            out.errors.append(f"{b.livreur} : indique le box (ex. « Box 1 ») avant les produits")
    out.blocks = [b for b in out.blocks if not b.empty()]
    return out
