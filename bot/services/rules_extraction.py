"""Lecture des commandes SANS intelligence artificielle : règles fixes et prévisibles.

Le message est découpé en morceaux (virgules, retours à la ligne, « / », « ; »,
« ~ », « | », tiret entouré d'espaces), puis chaque morceau est classé :

- adresse   : contient un mot de voie (rue, avenue, bd…) ou un code postal ;
- complément: digicode, code, interphone, étage, porte, bâtiment, gauche/droite… ;
- prix      : nombre suivi de €/euros/eur/e, « prix 60 », ou nombre seul en fin de message ;
- heure     : « vers 23h », « avant 22h30 », « 23h » ;
- produits  : tout le reste (hors salutations).

Rien n'est jamais inventé : une information absente reste absente, et le
franchisé voit la fiche avant de confirmer.
"""
from __future__ import annotations

import re

from bot.services.extraction import Order, to_order

_L = "a-zà-ÿ"  # lettres minuscules, accents compris (on travaille en re.I)

STREET_WORDS = (
    "rue", "avenue", "av", "bd", "boulevard", "blvd", "place", "allée", "allee", "impasse", "quai",
    "chemin", "route", "cours", "passage", "square", "villa", "cité", "cite", "rond-point",
    "faubourg", "fbg", "chaussée", "chaussee", "voie", "sentier", "promenade", "parvis", "esplanade",
    "résidence", "residence", "hameau", "lotissement", "sente", "ruelle",
)
STREET_RE = re.compile(r"(?<![" + _L + r"])(?:" + "|".join(map(re.escape, STREET_WORDS)) + r")(?![" + _L + r"])", re.I)
POSTCODE_RE = re.compile(r"(?<!\d)\d{5}(?!\d)")
PARIS_ARR_RE = re.compile(r"(?<![" + _L + r"])paris\s*(\d{1,2})\s*(?:er|e|ème|eme|è)?(?![" + _L + r"\d])", re.I)

DETAIL_WORDS = (
    "digicode", "code", "interphone", "interfone", "étage", "etage", "porte", "bat", "bât", "batiment",
    "bâtiment", "escalier", "esc", "appt", "appart", "appartement", "gauche", "droite", "sonner",
    "sonnez", "attend", "attends", "attendre", "fond de cour", "cour", "rdc", "rez-de-chaussée",
    "rez de chaussée", "boîte", "boite", "badge", "nom sur", "au nom",
)
DETAIL_RE = re.compile(r"(?<![" + _L + r"])(?:" + "|".join(map(re.escape, DETAIL_WORDS)) + r")(?![" + _L + r"])", re.I)
FLOOR_RE = re.compile(r"(?<!\d)\d{1,2}\s*(?:er|ère|e|ème|eme|è)(?![" + _L + r"])", re.I)

PRICE_UNIT_RE = re.compile(r"(?<![\d.,])(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|euros?|eur|e)(?![" + _L + r"\d])", re.I)
PRICE_WORD_RE = re.compile(r"(?<![" + _L + r"])(?:prix|total|tarif)\s*:?\s*(\d{1,5}(?:[.,]\d{1,2})?)", re.I)
BARE_NUMBER_RE = re.compile(r"^\d{1,5}(?:[.,]\d{1,2})?$")
TRAILING_NUMBER_RE = re.compile(r"^(.*\D)\s+(\d{1,5}(?:[.,]\d{1,2})?)$")

TIME_RE = re.compile(
    r"(?:(?<![" + _L + r"])(?:vers|avant|après|apres|pour|à|a|dès|des)\s+)?(?<!\d)\d{1,2}\s*h(?:\s*\d{2})?(?![" + _L + r"\d])",
    re.I,
)

SEPARATORS_RE = re.compile(r"[,;/|~\n]+|\s+[-–—]\s+")
BULLET_RE = re.compile(r"^\s*(?:[-•*·–]|\d{1,2}[.)])\s+")
NOISE_RE = re.compile(
    r"^(?:\d+\s+)?(?:commandes?|cmd|bonjour|bonsoir|salut|slt|coucou|cc|hello|merci|svp|stp|"
    r"s'il te pla[iî]t|s'il vous pla[iî]t|urgent|livraison|nouvelle commande)\s*[:!.]*$",
    re.I,
)
# Mots de produits courants : un morceau qui en contient n'est jamais pris pour une ville.
PRODUCT_WORDS_RE = re.compile(
    r"(?<![" + _L + r"])(?:vodka|whisky|whiskey|gin|rhum|rh[uû]m|tequila|champagne|champ|ros[ée]|vin|bi[èe]re|"
    r"bieres|mousseux|prosecco|cidre|coca|pepsi|red\s*bull|redbull|jus|soda|eau|glace|gla[çc]ons|jack|"
    r"daniels|ricard|pastis|martini|get|liqueur|cognac|hennessy|absolut|bouteilles?|canettes?|packs?|"
    r"cigarettes?|clopes?|chips|feuilles?|briquet)(?![" + _L + r"])",
    re.I,
)


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip(" .:-–—")


def _normalize_paris(text: str) -> str:
    """« Paris 4 », « paris 4e » → « 75004 Paris » (règle de la spécification)."""
    def repl(m):
        n = int(m.group(1))
        return f"750{n:02d} Paris" if 1 <= n <= 20 else m.group(0)
    return PARIS_ARR_RE.sub(repl, text)


def _split_orders(text: str) -> list[str]:
    """Plusieurs commandes : lignes à puces/numérotées, ou plusieurs lignes « adresse + prix »."""
    lines = [l for l in text.splitlines() if l.strip()]
    bullets = [i for i, l in enumerate(lines) if BULLET_RE.match(l)]
    if len(bullets) >= 2:
        orders: list[str] = []
        for i, line in enumerate(lines):
            if i in bullets:
                orders.append(BULLET_RE.sub("", line))
            elif orders:  # ligne de suite rattachée à la commande précédente
                orders[-1] += "\n" + line
        return orders
    # Plusieurs lignes qui ont chacune leur adresse : une commande par ligne.
    if len(lines) >= 2 and all(STREET_RE.search(l) for l in lines):
        return lines
    return [text]


_STOP_WORDS = {"il", "elle", "ils", "au", "aux", "le", "la", "les", "un", "une", "des", "du", "de", "pour",
               "et", "avec", "sans", "svp", "stp", "merci", "prix", "total"}


def _split_address_run(segment: str) -> list[str]:
    """« 54 rue du point du jour Boulogne 50 mousseux » : l'adresse a déjà son numéro,
    un second nombre suivi d'un mot ouvre la suite (produits). On coupe aussi juste
    après un code postal suivi d'autres mots."""
    street = STREET_RE.search(segment)
    if not street:
        return [segment]
    before = segment[: street.start()]
    after = segment[street.end():]
    has_number = re.search(r"(?<!\d)\d{1,4}(?:\s?(?:bis|ter))?\s*$", before.strip()) is not None or \
        re.match(r"^\s*\d{1,4}\b", before) is not None
    pc = POSTCODE_RE.search(after)
    if pc and after[pc.end():].strip():
        # La ville qui suit le code postal reste dans l'adresse (« 94300 Vincennes »),
        # jusqu'au premier chiffre, mot de produit, de complément ou petit mot.
        cut = pc.end()
        for word in re.finditer(r"\S+", after[pc.end():]):
            w = word.group(0)
            if re.search(r"\d", w) or PRODUCT_WORDS_RE.search(w) or DETAIL_RE.search(w) \
                    or w.lower() in _STOP_WORDS or word.start() > 40:
                break
            cut = pc.end() + word.end()
        head, rest = segment[: street.end() + cut], segment[street.end() + cut:]
        if rest.strip():
            return [head, rest]
    if has_number:
        m = re.search(r"\s(\d{1,4}(?:[.,]\d{1,2})?)\s+[" + _L + r"]", after, re.I)
        if m:
            cut = street.end() + m.start()
            return [segment[:cut], segment[cut:]]
    return [segment]


def _reorder_address(addr: str) -> str:
    """Met le numéro devant la voie pour aider le géocodage :
    « 75012 rue de charenton 45 » → « 45 rue de charenton 75012 » ;
    « montreuil 12 rue de paris » → « 12 rue de paris montreuil »."""
    addr = _clean(addr)
    m = re.match(r"^(\d{5})\s+(.*?)\s+(\d{1,4}(?:\s?(?:bis|ter))?)$", addr, re.I)
    if m and STREET_RE.search(m.group(2)):
        return f"{m.group(3)} {m.group(2)} {m.group(1)}"
    m = re.match(r"^(.*?[" + _L + r"])\s+(\d{1,4}(?:\s?(?:bis|ter))?)$", addr, re.I)
    if m and STREET_RE.match(m.group(1).split()[0] if m.group(1).split() else "") and not re.search(r"\d", m.group(1)):
        return f"{m.group(2)} {m.group(1)}"
    m = re.match(r"^([A-Za-zÀ-ÿ'\-]+(?:\s[A-Za-zÀ-ÿ'\-]+)?)\s+(\d{1,4}(?:\s?(?:bis|ter))?\s+.*)$", addr)
    if m and STREET_RE.search(m.group(2)) and not STREET_RE.search(m.group(1)):
        return f"{m.group(2)} {m.group(1)}"
    return addr


def _is_city_like(seg: str) -> bool:
    """Morceau court sans chiffre ni mot de produit juste après l'adresse : probablement la ville."""
    words = seg.split()
    return (0 < len(words) <= 3 and not re.search(r"\d", seg) and not PRODUCT_WORDS_RE.search(seg)
            and not DETAIL_RE.search(seg) and not NOISE_RE.match(seg))


# « 12,50 € » : la virgule décimale d'un prix n'est pas un séparateur.
DECIMAL_COMMA_RE = re.compile(r"(?<!\d)(\d{1,5}),(\d{1,2})(?=\s*(?:€|euros?(?![" + _L + r"])|eur(?![" + _L + r"]))|\s*$)", re.I)


def parse_order(text: str) -> dict:
    """Une commande (texte libre) → dict aux clés de l'extraction IA."""
    text = DECIMAL_COMMA_RE.sub(r"\1.\2", _normalize_paris(text))
    raw_segments = [s for s in (_clean(x) for x in SEPARATORS_RE.split(text)) if s]
    segments: list[str] = []
    for seg in raw_segments:
        segments.extend(s for s in (_clean(x) for x in _split_address_run(seg)) if s)

    address_parts: list[str] = []
    details: list[str] = []
    products: list[str] = []
    price: str | None = None
    requested_time: str | None = None
    address_index: int | None = None

    for i, seg in enumerate(segments):
        if NOISE_RE.match(seg):
            continue
        # Heure souhaitée
        t = TIME_RE.search(seg)
        if t and requested_time is None:
            requested_time = _clean(t.group(0))
            seg = _clean(seg[: t.start()] + " " + seg[t.end():])
            if not seg:
                continue
        # Complément d'adresse (avant le prix : « 3e étage » n'est pas 3 €)
        is_address = bool(STREET_RE.search(seg)) or bool(POSTCODE_RE.search(seg))
        if not is_address and (DETAIL_RE.search(seg) or (FLOOR_RE.search(seg) and not PRODUCT_WORDS_RE.search(seg)
                                                        and len(seg.split()) <= 4)):
            details.append(seg)
            continue
        # Prix explicite
        if price is None:
            m = PRICE_WORD_RE.search(seg) or PRICE_UNIT_RE.search(seg)
            if m:
                price = m.group(1)
                seg = _clean(seg[: m.start()] + " " + seg[m.end():])
                if not seg:
                    continue
        if is_address and address_index is None:
            address_parts.append(seg)
            address_index = i
            continue
        if address_index is not None and i == address_index + 1 and not POSTCODE_RE.search(" ".join(address_parts)) \
                and (POSTCODE_RE.fullmatch(seg) or _is_city_like(seg)):
            address_parts.append(seg)
            continue
        # Code postal ou « 75011 Paris » à part, alors que l'adresse n'en a pas encore
        if is_address and address_parts and not STREET_RE.search(seg) \
                and not POSTCODE_RE.search(" ".join(address_parts)):
            address_parts.append(seg)
            continue
        products.append(seg)

    # Nombre seul en fin de message = prix (s'il n'y en a pas d'autre)
    if price is None and products:
        last = products[-1]
        if BARE_NUMBER_RE.match(last):
            price = products.pop()
        else:
            m = TRAILING_NUMBER_RE.match(last)
            if m and segments and segments[-1] == last:
                products[-1], price = _clean(m.group(1)), m.group(2)

    address = _reorder_address(" ".join(address_parts)) if address_parts else None
    if address and len(address_parts) > 1:
        address = _clean(address)
    return {
        "address": address,
        "address_detail": ", ".join(details) or None,
        "products": ", ".join(p for p in products if p) or None,
        "price": price.replace(",", ".") if price else None,
        "requested_time": requested_time,
    }


def parse_message(text: str) -> list[dict]:
    """Message complet → liste de commandes (vide si rien d'exploitable)."""
    orders = []
    for chunk in _split_orders(text or ""):
        data = parse_order(chunk)
        if any(data.values()):
            orders.append(data)
    return orders


class RuleExtractor:
    """Même interface que l'extracteur IA, sans aucun appel réseau."""

    async def extract_text(self, text: str) -> list[Order]:
        return [to_order(d) for d in parse_message(text)]

    async def extract_image(self, image: bytes, media_type: str, caption: str | None = None) -> list[Order]:
        raise NotImplementedError("Les images demandent l'IA")


# ================================================================ modèle de commande
#
#   12 rue de Rivoli 75004 Paris        ← adresse (et éventuellement digicode, heure)
#   2 vodka 60                          ← quantité, produit, prix TOTAL de la ligne
#   1 coca 10
#                                        ← ligne vide
#   Digicode 45A32, 3e étage             ← commentaire (vu par le livreur seulement)

PRODUCT_LINE_RE = re.compile(
    r"^\s*(\d{1,3})\s*(?:[x×*]\s*)?(?=\D)(.+?)(?:\s+(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|euros?|eur|e)?)?\s*$",
    re.I,
)


def _product_line(line: str):
    """« 2 vodka 60 » → (2, "vodka", "60"). None si la ligne n'est pas une ligne produit."""
    m = PRODUCT_LINE_RE.match(line)
    if not m:
        return None
    qty, name, price = m.group(1), _clean(m.group(2)), m.group(3)
    if not name or STREET_RE.search(name) or POSTCODE_RE.search(name) or NOISE_RE.match(name) \
            or DETAIL_RE.search(name) or TIME_RE.fullmatch(f"{qty}{name}"):
        return None
    if FLOOR_RE.match(f"{qty}{name.split()[0]}") and len(name.split()) <= 3:
        return None  # « 3e étage gauche »
    return int(qty), name, price.replace(",", ".") if price else None


def _eur(value: float) -> str:
    return (f"{value:.2f}".replace(".", ",").replace(",00", "")) + " €"


def parse_template(text: str, catalog=None) -> list[dict] | None:
    """Lit une commande au format du modèle. None si le message n'est pas à ce format
    (on retombe alors sur la lecture libre)."""
    lines = (text or "").splitlines()
    if any(BULLET_RE.match(l) for l in lines):
        return None  # plusieurs commandes à puces : lecture libre
    blank = next((i for i, l in enumerate(lines) if not l.strip()), None)
    head = [l for l in (lines if blank is None else lines[:blank]) if l.strip()]
    comment = " ".join(_clean(l) for l in ([] if blank is None else lines[blank + 1:]) if l.strip())

    items, other = [], []
    for line in head:
        parsed = _product_line(line)
        if parsed:
            items.append(parsed)
        elif items:
            return None  # texte libre après les produits : ce n'est pas le modèle
        else:
            other.append(line)
    if not items:
        return None

    warnings: list[str] = []
    labels: list[str] = []
    for qty, name, _ in items:
        label = name
        if catalog:
            m = catalog.match(name)
            if m.product:
                label = m.product["name"]
            elif m.ambiguous:
                warnings.append(f"⚠️ « {name} » peut être : {', '.join(m.ambiguous)}")
            else:
                warnings.append(f"⚠️ Produit pas dans le catalogue : « {name} »")
        labels.append(f"{qty} {label}")

    if all(p is not None for _, _, p in items):
        prices = [float(p) for _, _, p in items]
        total = f"{sum(prices):.2f}"
        if len(items) > 1:
            labels = [f"{lab} ({_eur(p)})" for lab, p in zip(labels, prices)]
    else:
        total = None

    head_info = parse_order(", ".join(other)) if other else {}
    details = [d for d in (head_info.get("address_detail"), head_info.get("products"), comment) if d]
    return [{
        "address": head_info.get("address"),
        "address_detail": " · ".join(details) or None,
        "products": " + ".join(labels),
        "price": total,
        "requested_time": head_info.get("requested_time"),
        "warnings": warnings,
    }]


MODEL_EXAMPLE = "12 rue de Rivoli 75004 Paris\n2 vodka 60\n1 coca 10\n\nDigicode 45A32, 3e étage"
