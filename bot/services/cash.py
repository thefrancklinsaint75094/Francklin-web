"""Caisse des livreurs : dépenses (/depense) et cash à récupérer (/caisse, /macaisse).

Cash qu'un livreur doit avoir sur lui = ventes OK payées en espèces − dépenses (charges et
avances sur paye) − cash déjà récupéré par un ravitailleur. Même calcul que SOLDES ④ du
tableau Rechargement, mais fait en direct par le script (action « cash_livreurs ») pour ne
pas dépendre des IMPORTRANGE ; le bot y ajoute ses propres mouvements pas encore écrits
dans les feuilles (livraisons en espèces, dépenses, cash récupéré : « en vol »).

Compte du jour (la nuit en cours, jusqu'à NIGHT_END_HOUR) : ce que le livreur avait encore des jours
d'avant (« avant », pas remis au ravitailleur) + encaissé aujourd'hui (espèces) − charges du jour
(− cash remis aujourd'hui, + cash reçu d'un autre livreur) = ce qui lui reste. Les mouvements du jour
viennent de la base du bot ; « avant » = total de la semaine − mouvements du jour.
"""
from __future__ import annotations

from bot import config, db
from bot.services import sheets, stock
from bot.timeutil import night_bounds, night_start_date, now_utc

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

DAY_FIELDS = ("jour_especes", "jour_depenses", "jour_remis", "jour_recu")


def _blank(name: str) -> dict:
    return {"nom": name, "especes": 0.0, "virement": 0.0, "depenses": 0.0, "recupere": 0.0, "cash": 0.0,
            "en_vol": 0.0, "avant": 0.0, **{f: 0.0 for f in DAY_FIELDS}}


async def today_moves() -> dict[str, dict[str, float]]:
    """Nom du livreur (clé sans casse) → mouvements de cash de la nuit en cours, d'après la base."""
    end_hour = config.get().night_end_hour
    night = night_start_date(now_utc(), end_hour)
    start, end = night_bounds(night, end_hour)
    users = {u["id"]: u.get("display_name") or "" for u in await db.list_users(role="livreur")}
    out: dict[str, dict[str, float]] = {}

    def add(livreur_id, name, field: str, amount) -> None:
        name = users.get(livreur_id) or name
        if name and amount:
            row = out.setdefault(stock._key(name), {f: 0.0 for f in DAY_FIELDS})
            row[field] = round(row[field] + float(amount), 2)

    for c in await db.list_delivered_between(start, end):
        add(c.get("livreur_id"), None, "jour_especes", delivery_amount(c))
    for r in await db.list_sales_for_night(night, start, end):
        if r.get("payment") == "especes":
            add(r.get("livreur_id"), r.get("livreur_name"), "jour_especes", r.get("price"))
    for e in await db.list_expenses_for_night(night, start, end):
        add(e.get("livreur_id"), e.get("livreur_name"), "jour_depenses", e.get("amount"))
    for r in await db.list_restocks_between(start, end):
        add(r.get("livreur_id"), r.get("livreur_name"), "jour_remis", r.get("cash"))
        if r.get("kind") == "swipe":                     # le cash passe à celui qui reçoit
            add(r.get("to_livreur_id"), r.get("to_livreur_name"), "jour_recu", r.get("cash"))
    return out


def _split_day(row: dict, moves: dict | None) -> None:
    for f in DAY_FIELDS:
        row[f] = float((moves or {}).get(f) or 0)
    today = row["jour_especes"] + row["jour_recu"] - row["jour_depenses"] - row["jour_remis"]
    row["avant"] = round(row["cash"] - today, 2)


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
    moves = await today_moves()
    for k, row in rows.items():
        _split_day(row, moves.get(k))
    ordered = sorted(rows.values(), key=lambda r: r["nom"].lower())
    ravi = data.get("ravitailleur")
    return {"ok": True, "livreurs": {r["nom"]: r for r in ordered},
            "ravitailleur": float(ravi) if isinstance(ravi, (int, float)) else None}


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
        _split_day(row, (await today_moves()).get(wanted))
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
