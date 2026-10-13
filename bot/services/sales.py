"""/ventes : récapitulatif de ventes envoyé par un admin, écrit dans la feuille Dispatch.

Format (un bloc par livreur, autant de blocs que voulu) :

    Livreur A
    2 US 60
    CB 1 DIV 30
    12 rue de la Paix Paris : 1 US 50

Chaque ligne : quantité, produit (nom du catalogue ou alias), prix total de la ligne (de 10 en 10 €, ou 0 € pour un
produit offert : il sort du stock du livreur sans rien rapporter). L'adresse, facultative, se met devant, suivie de
« : » ; elle va dans la colonne Adresse de la feuille (sinon « Récap ventes »).
Dépenses du livreur dans le même bloc : « charge = 20 essence » ou « paye = 100 » (seules sur leur ligne, ou au
bout d'une ligne de vente avec « = ») → zone DÉPENSES LIVREURS de l'onglet (Charges / Paye, montant, motif).
Espèces par défaut ; sinon « CB » ou « virement » n'importe où avant le prix (CB compte comme virement :
la feuille ne connaît que Espèces et Virement). Fonctions pures ici ; l'enregistrement est dans
handlers/sales.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from bot.services.order_edit import valid_price

PAY_WORDS = {
    "especes": ("espece", "especes", "espèce", "espèces", "esp", "cash", "liquide"),
    "virement": ("virement", "virements", "vir", "vrt", "cb", "carte"),
}
DEFAULT_PAY = "especes"
LINE_RE = re.compile(r"^(?P<qty>\d{1,3})\s*[x×]?\s+(?P<product>.+?)\s+(?P<price>\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|e|eur|euros?)?$",
                     re.I)
MAX_LINES = 60
MAX_ADDRESS = 150
_AMOUNT = r"(?P<amount>\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|eur|euros?|e(?=\s|$))?"
_KIND = r"(?P<kind>charges?|pay[eé]s?|paies?)"
EXPENSE_RE = re.compile(rf"^{_KIND}\s*[=:]?\s*{_AMOUNT}\s*[-:,]?\s*(?P<motif>.*)$", re.I)
EXPENSE_TAIL_RE = re.compile(rf"\s{_KIND}\s*=\s*{_AMOUNT}\s*[-:,]?\s*(?P<motif>.*)$", re.I)   # « … 2 US 60 charge = 20 »


DAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower().strip())
    return "".join(c for c in text if not unicodedata.combining(c))


def target_night(word: str, current: date) -> date | None:
    """« lundi » (ou « lun ») → la dernière nuit de lundi, celle en cours comprise ; « hier » → la nuit
    d'avant ; « aujourd'hui » → celle en cours. None si le mot n'est pas un jour."""
    w = _plain(word).replace("’", "'")
    if w in ("hier", "veille"):
        return current - timedelta(days=1)
    if w in ("aujourd'hui", "aujourdhui", "auj", "ce soir", "cette nuit"):
        return current
    day = DAYS.get(w)
    if day is None and len(w) >= 3:
        day = next((d for name, d in DAYS.items() if name.startswith(w)), None)
    if day is None:
        return None
    return current - timedelta(days=(current.weekday() - day) % 7)


def payment_of(word: str) -> str | None:
    w = word.lower().strip(" :-")
    return next((mode for mode, words in PAY_WORDS.items() if w in words), None)


def split_payment(line: str) -> tuple[str | None, str]:
    """Retire le mot de paiement où qu'il soit (« CB 2 US 60 », « 2 CB US 60 », « 2 US CB 60 »)."""
    pay, rest = None, []
    for token in line.split():
        mode = payment_of(token) if pay is None else None
        if mode:
            pay = mode
        else:
            rest.append(token)
    return pay, " ".join(rest)


@dataclass
class Block:
    livreur: str                          # nom tel qu'écrit, puis nom de la feuille une fois reconnu
    user: dict | None = None
    lines: list[dict] = field(default_factory=list)   # {"pay": "especes", "q": 2, "p": "US", "x": 60.0[, "a": adresse]}
    expenses: list[dict] = field(default_factory=list)   # {"k": "charges" | "paye", "x": 20.0, "m": "essence"}


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(len(b.lines) for b in self.blocks)

    @property
    def expense_count(self) -> int:
        return sum(len(b.expenses) for b in self.blocks)


def split_expense(line: str) -> tuple[dict | None, str]:
    """« charge = 20 essence » → ({k, x, m}, « ») ; « 12 rue X : 2 US 60 paye = 50 » → ({…}, « 12 rue X : 2 US
    60 ») ; sinon (None, ligne). Montant 0 ou illisible : {"bad": texte}."""
    m = EXPENSE_RE.match(line)
    rest = ""
    if not m:
        m = EXPENSE_TAIL_RE.search(line)
        if not m:
            return None, line
        rest = line[:m.start()].strip()
    amount = float(m["amount"].replace(",", "."))
    if amount <= 0:
        return {"bad": m.group(0).strip()}, rest
    kind = "charges" if m["kind"].lower().startswith("charge") else "paye"
    return {"k": kind, "x": amount, "m": " ".join(m["motif"].split())[:80]}, rest


def _key(text: str) -> str:
    return " ".join((text or "").lower().split())


def resolve_livreur(text: str, names: list[str], users: list[dict]) -> tuple[str | None, dict | None]:
    """« Livreur A », « livreur a », « A », « Livreur A (Ketur) » ou le prénom « Ketur » → nom de la
    feuille (+ compte du bot s'il existe)."""
    from bot.services import names as prenoms

    wanted = _key(prenoms.strip_prenom(text))
    by_prenom = prenoms.by_prenom(wanted)
    if by_prenom and not any(_key(n) == wanted for n in names):
        wanted = _key(by_prenom)
    by_name = {_key(u.get("display_name") or ""): u for u in users if u.get("display_name")}
    for name in list(names) + [u["display_name"] for u in users if u.get("display_name")]:
        k = _key(name)
        if wanted == k or (len(wanted) <= 2 and k.endswith(" " + wanted)):
            return name, by_name.get(k)
    return None, None


def split_address(line: str) -> tuple[str | None, str]:
    """« 12 rue de la Paix : 2 US 60 » → (« 12 rue de la Paix », « 2 US 60 ») ; sans « : », pas d'adresse.
    « Livreur A : » → (« Livreur A », « ») : un nom de livreur suivi de deux-points."""
    address, sep, rest = line.rpartition(":")
    if not sep:
        return None, line
    return " ".join(address.split()) or None, rest.strip()


def parse(text: str, catalog, names: list[str], users: list[dict]) -> Parsed:
    out = Parsed()
    current: Block | None = None
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.lower().startswith("/vente"):
            continue
        expense, line = split_expense(line)
        if expense is not None:
            if "bad" in expense:
                out.errors.append(f"ligne {n} : « {expense['bad']} » — montant à 0")
            elif current is None:
                out.errors.append(f"ligne {n} : écris d'abord le nom du livreur (ex. « Livreur A »)")
            else:
                current.expenses.append(expense)
            if not line:
                continue
        address, sale = split_address(line)
        if address and not sale:                         # « Livreur A : »
            line = sale = address
            address = None
        elif address and resolve_livreur(address, names, users)[0]:   # « Livreur A : 2 US 60 »
            name, user = resolve_livreur(address, names, users)
            current = next((b for b in out.blocks if b.livreur == name), None) or Block(name, user)
            if current not in out.blocks:
                out.blocks.append(current)
            address = None
        pay, rest = split_payment(sale)
        m = LINE_RE.match(rest)
        if m:
            if current is None:
                out.errors.append(f"ligne {n} : écris d'abord le nom du livreur (ex. « Livreur A »)")
                continue
            price = float(m["price"].replace(",", "."))
            match = catalog.match(m["product"]) if catalog else None
            product = match.product["name"] if match and match.product else None
            if product is None:
                out.errors.append(f"ligne {n} : produit inconnu « {m['product']} »")
            elif price != 0 and not valid_price(price):   # 0 € : produit offert
                out.errors.append(f"ligne {n} : prix {m['price']} € — les prix vont de 10 en 10 €")
            elif int(m["qty"]) <= 0:
                out.errors.append(f"ligne {n} : quantité à 0")
            else:
                current.lines.append({"pay": pay or DEFAULT_PAY, "q": int(m["qty"]), "p": product, "x": price,
                                      **({"a": address[:MAX_ADDRESS]} if address else {})})
            continue
        if address:
            out.errors.append(f"ligne {n} : « {line} » — attendu : adresse : quantité, produit, prix "
                              "(ex. 12 rue de la Paix : 2 US 60)")
            continue
        if pay is not None or rest[:1].isdigit():
            out.errors.append(f"ligne {n} : « {line} » — attendu : quantité, produit, prix "
                              "(ex. 2 US 60, ou CB 2 US 60)")
            continue
        name, user = resolve_livreur(line, names, users)
        if name is None:
            out.errors.append(f"ligne {n} : livreur inconnu « {line} »")
            current = None
            continue
        current = Block(name, user)
        out.blocks.append(current)
    out.blocks = [b for b in out.blocks if b.lines or b.expenses]
    if out.count + out.expense_count > MAX_LINES:
        out.errors.append(f"trop de lignes ({out.count + out.expense_count}) : envoie-les en plusieurs fois (au plus {MAX_LINES})")
    return out
