"""Caisse des livreurs : dépenses (/depense) et cash à récupérer (/caisse, /macaisse).

Cash qu'un livreur doit avoir sur lui = ventes OK payées en espèces − dépenses (charges et
avances sur paye) − cash déjà récupéré par un ravitailleur. Même calcul que SOLDES ④ du
tableau Rechargement, mais fait en direct par le script (action « cash_livreurs ») pour ne
pas dépendre des IMPORTRANGE ; le bot y ajoute ses propres mouvements pas encore écrits
dans les feuilles (livraisons en espèces, dépenses, cash récupéré : « en vol »).
"""
from __future__ import annotations

from bot import db
from bot.services import sheets, stock

KINDS = {"c": "charges", "p": "paye"}
KIND_SHEET = {"charges": "Charges", "paye": "Paye"}   # valeurs de la liste « Type dépense » de la feuille
MOTIFS = ("Essence", "Parking", "Repas", "Taxi / VTC", "Téléphone", "Péage")
AMOUNT_STEPS = (-50, -10, -5, 5, 10, 50)
MAX_AMOUNT = 9_999
MAX_MOTIF = 80
CASH = "€"   # clé des mouvements de cash dans le registre « en vol »


def key(livreur_id: str) -> str:
    return f"cash:{livreur_id}"


def change_amount(amount: float, delta: float) -> float:
    return float(min(MAX_AMOUNT, max(0.0, round(float(amount or 0) + delta, 2))))


def clean_motif(text: str) -> str:
    return " ".join((text or "").split())[:MAX_MOTIF]


# ---------------------------------------------------------------- mouvements en vol

def track(livreur_id: str | None, token: str, amount: float) -> None:
    """+ encaissé par le livreur, − sorti de sa poche (dépense, cash remis)."""
    if livreur_id and amount:
        stock.add_inflight(key(livreur_id), token, {CASH: amount})


def done(livreur_id: str | None, token: str) -> None:
    if livreur_id:
        stock.remove_inflight(key(livreur_id), token)


def inflight(livreur_id: str) -> float:
    return float(stock.inflight_deltas(key(livreur_id)).get(CASH, 0))


def delivery_amount(course: dict) -> float:
    """Ce que la livraison met dans la poche du livreur (espèces seulement)."""
    return float(course.get("price") or 0) if course.get("payment") == "especes" else 0.0


# ---------------------------------------------------------------- lecture

def _blank(name: str) -> dict:
    return {"nom": name, "especes": 0.0, "virement": 0.0, "depenses": 0.0, "recupere": 0.0, "cash": 0.0,
            "en_vol": 0.0}


async def livreurs_now() -> dict:
    """{"ok": True, "livreurs": {nom feuille: {especes, virement, depenses, recupere, en_vol, cash, user}}}
    ou {"ok": False, "error": …}. Chaque livreur actif du bot y figure, même sans mouvement."""
    data = await sheets.fetch_action("cash_livreurs")
    if not data or not data.get("ok"):
        return {"ok": False, "error": (data or {}).get("error") or "Google Sheets non relié"}
    rows: dict[str, dict] = {}
    for name, values in (data.get("livreurs") or {}).items():
        row = _blank(name)
        for field in ("especes", "virement", "depenses", "recupere", "cash"):
            row[field] = float((values or {}).get(field) or 0)
        rows[stock._key(name)] = row
    for user in await db.list_users(role="livreur"):
        name = user.get("display_name")
        if not name:
            continue
        row = rows.get(stock._key(name))
        if row is None:
            if user["status"] != "active":
                continue
            row = rows[stock._key(name)] = _blank(name)
        row["user"] = user
        row["en_vol"] = inflight(user["id"])
        row["cash"] = round(row["cash"] + row["en_vol"], 2)
    ordered = sorted(rows.values(), key=lambda r: r["nom"].lower())
    return {"ok": True, "livreurs": {r["nom"]: r for r in ordered}}


async def livreur_now(livreur: dict) -> dict:
    """{"ok": True, "row": {...}} pour un seul livreur."""
    data = await livreurs_now()
    if not data["ok"]:
        return data
    wanted = stock._key(livreur.get("display_name") or "")
    row = next((r for r in data["livreurs"].values() if stock._key(r["nom"]) == wanted), None)
    if row is None:
        row = _blank(livreur.get("display_name") or "?")
        row["en_vol"] = row["cash"] = inflight(livreur["id"])
    return {"ok": True, "row": row}


# ---------------------------------------------------------------- envoi des dépenses

async def push_expense_tracked(expense: dict) -> None:
    token = f"expense:{expense['id']}"
    track(expense["livreur_id"], token, -float(expense["amount"]))
    if await sheets.push_expense(expense):
        done(expense["livreur_id"], token)


def push_expense_later(expense: dict) -> None:
    if sheets.enabled():
        stock._later(push_expense_tracked(dict(expense)))
