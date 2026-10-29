"""Argent du ravitailleur (espace « 💼 Ravi » de l'appli Dispatch, solde dans l'appli Ravitaillement).

Ce qu'un ravitailleur doit avoir sur lui = solde de départ (dernier « départ » : fixé par l'exploitant, calé sur un
contrôle de caisse ou reporté au reset de la semaine)
  + cash récupéré auprès des livreurs (rechargements du bot, colonne Cash, depuis le départ)
  + dépôts (l'exploitant lui donne)
  − charges − invest − remis à l'exploitant (perso).
Le registre est gardé d'une semaine à l'autre ; chaque charge / invest / perso / dépôt est recopié dans l'onglet
RAVI du Compta (action « ravi_move » du script). Un contrôle de caisse note le montant compté et l'écart, sans
toucher au solde ; « caler sur le compté » crée un nouveau départ.

Va-et-vient avec les tableaux : quand le script répond à « ravi_sheet », le compte se lit DANS LES TABLEAUX
(cash récupéré des onglets du tableau Rechargement, lignes de COMPTA RAVI, y compris celles tapées à la main),
plus ce qui a été noté dans le bot et n'y est pas encore écrit. Caler le solde ou fixer un départ écrit alors
une ligne d'ajustement dans RAVI. Sans réponse du script (ancien script, tableau injoignable), le calcul se
fait comme avant, à partir de ce que le bot a noté.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta

from bot import config, db, messaging, texts
from bot.config import PARIS
from bot.timeutil import night_start_date, now_utc, parse_ts, to_paris

log = logging.getLogger(__name__)

# genre → (icône, libellé, signe dans le solde, valeur de la colonne Type de RAVI)
KINDS = {
    "depot": ("💰", "Tu lui as donné", 1, "Depot"),
    "charge": ("🧾", "Charges", -1, "Charge"),
    "invest": ("📦", "Invest (achats, dettes)", -1, "Invest"),
    "perso": ("💸", "Il t'a remis", -1, "Perso"),
}
MAX_AMOUNT = 99_999
MAX_MOTIF = 80
DAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def _key(name: str | None) -> str:
    return " ".join(str(name or "").lower().split())


def when(ts: str | datetime | None) -> str:
    at = parse_ts(ts) if isinstance(ts, str) else ts
    if at is None:
        return ""
    p = to_paris(at)
    return f"{DAYS[p.weekday()]} {p:%d/%m %Hh%M}"


def clean_amount(value) -> float | None:
    try:
        amount = round(float(str(value).replace(",", ".").replace("€", "").strip()), 2)
    except (TypeError, ValueError):
        return None
    return amount if 0 < amount <= MAX_AMOUNT else None


async def names(users: list[dict] | None = None) -> list[str]:
    """Noms des ravitailleurs : ceux de la feuille (RAVITAILLEUR_NAMES), puis les comptes hors de cette liste."""
    users = users if users is not None else await db.list_users(role="ravitailleur")
    out = list(config.get().ravitailleur_names)
    seen = {_key(n) for n in out}
    for u in users:
        name = u.get("display_name")
        if name and u.get("status") == "active" and _key(name) not in seen:
            out.append(name)
            seen.add(_key(name))
    return out


def canonical(name: str | None, known: list[str]) -> str | None:
    wanted = _key(name)
    return next((n for n in known if _key(n) == wanted), None) if wanted else None


def attribute(restock: dict, users_by_id: dict, known: list[str]) -> str | None:
    """Ravitailleur qui a récupéré le cash d'un rechargement (None : pas de ravitailleur connu)."""
    if restock.get("ravitailleur_name"):
        return canonical(restock["ravitailleur_name"], known)
    by = users_by_id.get(restock.get("by_user_id")) or {}
    return canonical(by.get("display_name"), known) if by.get("role") == "ravitailleur" else None


async def _week_start(now: datetime) -> datetime:
    last = await db.last_event("cloture")
    at = parse_ts(last["created_at"]) if last else None
    return at or now - timedelta(days=7)


async def accounts(now: datetime | None = None) -> dict:
    """{"ravis": [compte…], "unassigned": {"total", "items"}, "week_start", "source"} — un compte par
    ravitailleur, lu dans les tableaux (« tableau ») ou, faute de script à jour, d'après le bot (« bot »)."""
    now = now or now_utc()
    data = await _sheet_data()
    if data is not None:
        return await _accounts_sheet(data, now)
    return {**await _accounts_bot(now), "source": "bot"}


async def _sheet_data() -> dict | None:
    """Ce que les tableaux disent de l'argent des ravitailleurs (action « ravi_sheet »), ou None."""
    from bot.services import sheets

    if not sheets.enabled():
        return None
    result = await sheets.fetch_action("ravi_sheet")
    if not result or not result.get("ok") or "recupere" not in result or "moves" not in result:
        return None                                   # ancien script (pas d'action ravi_sheet) ou injoignable
    return result


_DAYS_FULL = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_TIME_RE = re.compile(r"(\d{1,2})[:h](\d{2})")
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})")
_NOTE_RESTOCK = re.compile(r"^Bot R#(\d+)")
_NOTE_MOVE = re.compile(r"^Bot V#(\d+)")


def _monday(now: datetime) -> date:
    night = night_start_date(now, config.get().night_end_hour)
    return night - timedelta(days=night.weekday())


def _row_at(monday: date, day: str, heure: str) -> datetime:
    """Instant d'une ligne d'un onglet du jour (heure de la colonne S si elle y est, sinon midi)."""
    idx = _DAYS_FULL.index(day.lower()) if day.lower() in _DAYS_FULL else 0
    d = monday + timedelta(days=idx)
    m = _TIME_RE.search(heure or "")
    if not m:
        return datetime.combine(d, time(12), tzinfo=PARIS)
    hh, mm = int(m[1]) % 24, int(m[2]) % 60
    if hh < config.get().night_end_hour:                # 2h du matin : fin de la nuit, donc le lendemain
        d += timedelta(days=1)
    return datetime.combine(d, time(hh, mm), tzinfo=PARIS)


def _date_at(monday: date, text: str, fallback: datetime) -> datetime:
    m = _DATE_RE.search(text or "")
    if not m:
        return fallback
    try:
        d = date(monday.year, int(m[2]), int(m[1]))
    except ValueError:
        return fallback
    if d > monday + timedelta(days=180):              # 30/12 lu en janvier
        d = date(monday.year - 1, d.month, d.day)
    return datetime.combine(d, time(12), tzinfo=PARIS)


_TYPES = {_key(v[3]): k for k, v in KINDS.items()}   # « depot » → depot, « charge » → charge…


async def _accounts_sheet(data: dict, now: datetime) -> dict:
    """Comptes lus dans les tableaux + ce que le bot a noté et qui n'y est pas encore."""
    users = await db.list_users()
    users_by_id = {u["id"]: u for u in users}
    known = await names([u for u in users if u["role"] == "ravitailleur"])
    moves = await db.list_ravi_moves()
    leaving = {m["id"] for m in await db.list_ravi_moves_to_sync() if m["sheet_state"] == "remove"}
    week_start = await _week_start(now)
    monday = _monday(now)

    def target(name: str | None) -> str | None:
        found = canonical(name, known)
        if found or name:
            return found
        return known[0] if len(known) == 1 else None    # colonne vide : le seul ravitailleur

    acc = {n: {"start": 0.0, "start_rows": 0, "totals": {"recupere": 0.0, **{k: 0.0 for k in KINDS}},
               "history": []} for n in known}
    lost, in_sheet = [], set()
    for r in data.get("recupere") or []:
        note = _NOTE_RESTOCK.match(str(r.get("note") or ""))
        if note:
            in_sheet.add(int(note[1]))
        at = _row_at(monday, str(r.get("jour") or ""), str(r.get("heure") or ""))
        amount = round(float(r.get("cash") or 0), 2)
        name = target(r.get("ravitailleur"))
        if name is None:
            lost.append({"id": int(note[1]) if note else None, "when": when(at), "livreur": r.get("livreur") or "?",
                         "amount": amount, "by": None})
            continue
        acc[name]["totals"]["recupere"] += amount
        acc[name]["history"].append({"at": at.isoformat(), "when": when(at), "kind": "recupere", "icon": "💶",
                                     "label": f"Récupéré chez {r.get('livreur') or '?'}", "amount": amount,
                                     "sheet": "done"})
    by_id = {m["id"]: m for m in moves}
    for row in data.get("moves") or []:
        note = _NOTE_MOVE.match(str(row.get("note") or ""))
        move_id = int(note[1]) if note else None
        if move_id in leaving:                         # annulé dans le bot, ligne pas encore effacée
            continue
        kind = _TYPES.get(_key(row.get("type")))
        name = target(row.get("ravitailleur"))
        if kind is None or name is None:
            continue
        amount = round(float(row.get("montant") or 0), 2)
        motif = str(row.get("motif") or "")
        at = _date_at(monday, str(row.get("date") or ""), week_start)
        a = acc[name]
        if motif.lower().startswith("report"):          # solde de la semaine d'avant, écrit au reset
            a["start"] += KINDS[kind][2] * amount
            a["start_rows"] += 1
            continue
        a["totals"][kind] += amount
        move = by_id.get(move_id)
        a["history"].append({"at": at.isoformat(), "when": when(at) if _DATE_RE.search(str(row.get("date") or ""))
                             else "cette semaine", "kind": kind, "id": move["id"] if move else None,
                             "icon": KINDS[kind][0], "label": KINDS[kind][1] + (f" — {motif}" if motif else ""),
                             "amount": KINDS[kind][2] * amount, "sheet": "done",
                             "by": (users_by_id.get(move["by_user_id"]) or {}).get("display_name") if move else None})
    # Noté dans le bot, pas encore dans les tableaux : compté en attendant (⏳).
    pending_restocks = [r for r in await db.list_restocks_between(week_start, now + timedelta(days=1))
                        if r.get("kind") != "swipe" and float(r.get("cash") or 0) > 0 and r["id"] not in in_sheet]

    def who(r):
        return r.get("livreur_name") or (users_by_id.get(r.get("livreur_id")) or {}).get("display_name") or "?"

    for r in pending_restocks:
        name = attribute(r, users_by_id, known)
        amount = float(r["cash"])
        if name is None:
            lost.append({"id": r["id"], "when": when(r["created_at"]), "livreur": who(r), "amount": amount,
                         "by": (users_by_id.get(r.get("by_user_id")) or {}).get("display_name")})
            continue
        acc[name]["totals"]["recupere"] += amount
        acc[name]["history"].append({"at": r["created_at"], "when": when(r["created_at"]), "kind": "recupere",
                                     "icon": "💶", "label": f"Récupéré chez {who(r)}", "amount": amount,
                                     "sheet": "pending"})
    for m in moves:
        if m["sheet_state"] != "pending" or m["kind"] not in KINDS or m["ravitailleur"] not in acc:
            continue
        icon, label, sign, _ = KINDS[m["kind"]]
        a = acc[m["ravitailleur"]]
        a["totals"][m["kind"]] += float(m["amount"])
        a["history"].append({"at": m["created_at"], "when": when(m["created_at"]), "kind": m["kind"], "id": m["id"],
                             "icon": icon, "label": label + (f" — {m['motif']}" if m.get("motif") else ""),
                             "amount": sign * float(m["amount"]), "sheet": "pending",
                             "by": (users_by_id.get(m["by_user_id"]) or {}).get("display_name")})
    out = []
    for name in known:
        a, t = acc[name], acc[name]["totals"]
        balance = a["start"] + t["recupere"] + t["depot"] - t["charge"] - t["invest"] - t["perso"]
        a["history"].sort(key=lambda h: str(h["at"]), reverse=True)
        out.append({
            "name": name,
            "account": any(u["role"] == "ravitailleur" and _key(u.get("display_name")) == _key(name) for u in users),
            "start": {"amount": round(a["start"], 2), "when": when(week_start), "set": bool(a["start_rows"]),
                      "motif": "Report semaine passée" if a["start_rows"] else None},
            "totals": {k: round(v, 2) for k, v in t.items()},
            "balance": round(balance, 2),
            "control": _control(moves, name),
            "pending_sheet": sum(1 for m in moves if m["ravitailleur"] == name and m["sheet_state"] == "pending"),
            "history": a["history"][:80],
        })
    return {"ravis": out, "week_start": when(week_start), "source": "tableau",
            "unassigned": {"total": round(sum(u["amount"] for u in lost), 2), "items": lost}}


def _control(moves: list[dict], name: str) -> dict | None:
    """Dernier contrôle de caisse ; calé : un départ ou une ligne d'ajustement l'a suivi."""
    controls = [m for m in moves if m["ravitailleur"] == name and m["kind"] == "controle"]
    if not controls:
        return None
    last = controls[-1]
    after = [m for m in moves if m["ravitailleur"] == name and parse_ts(m["created_at"]) >= parse_ts(last["created_at"])
             and (m["kind"] == "depart" or str(m.get("motif") or "").startswith("Calé sur le contrôle"))]
    return {"id": last["id"], "when": when(last["created_at"]), "counted": float(last["amount"]),
            "expected": float(last["expected"] or 0),
            "gap": round(float(last["amount"]) - float(last["expected"] or 0), 2), "aligned": bool(after)}


async def _accounts_bot(now: datetime) -> dict:
    """Calcul d'avant le va-et-vient : seulement ce que le bot a noté (script pas à jour)."""
    users = await db.list_users()
    users_by_id = {u["id"]: u for u in users}
    known = await names([u for u in users if u["role"] == "ravitailleur"])
    moves = await db.list_ravi_moves()
    week_start = await _week_start(now)
    starts = {}
    for name in known:
        anchors = [m for m in moves if m["ravitailleur"] == name and m["kind"] == "depart"]
        starts[name] = anchors[-1] if anchors else None
    oldest = min([parse_ts(a["created_at"]) for a in starts.values() if a] + [week_start])
    restocks = [r for r in await db.list_restocks_between(oldest, now + timedelta(days=1))
                if r.get("kind") != "swipe" and float(r.get("cash") or 0) > 0]

    def who(r):
        return r.get("livreur_name") or (users_by_id.get(r.get("livreur_id")) or {}).get("display_name") or "?"

    out = []
    for name in known:
        anchor = starts[name]
        start = parse_ts(anchor["created_at"]) if anchor else week_start
        base = float(anchor["amount"]) if anchor else 0.0
        history = []
        totals = {"recupere": 0.0, **{k: 0.0 for k in KINDS}}
        for r in restocks:
            if parse_ts(r["created_at"]) > start and attribute(r, users_by_id, known) == name:
                amount = float(r["cash"])
                totals["recupere"] += amount
                history.append({"at": r["created_at"], "when": when(r["created_at"]), "kind": "recupere",
                                "icon": "💶", "label": f"Récupéré chez {who(r)}", "amount": amount})
        for m in moves:
            if m["ravitailleur"] != name or m["kind"] in ("depart", "controle"):
                continue
            if parse_ts(m["created_at"]) <= start:
                continue
            icon, label, sign, _ = KINDS[m["kind"]]
            amount = float(m["amount"])
            totals[m["kind"]] += amount
            history.append({"at": m["created_at"], "when": when(m["created_at"]), "kind": m["kind"], "id": m["id"],
                            "icon": icon, "label": label + (f" — {m['motif']}" if m.get("motif") else ""),
                            "amount": sign * amount, "sheet": m["sheet_state"],
                            "by": (users_by_id.get(m["by_user_id"]) or {}).get("display_name")})
        balance = base + totals["recupere"] + totals["depot"] - totals["charge"] - totals["invest"] - totals["perso"]
        history.sort(key=lambda h: h["at"], reverse=True)
        has_account = any(u["role"] == "ravitailleur" and _key(u.get("display_name")) == _key(name) for u in users)
        out.append({
            "name": name, "account": has_account,
            "start": {"amount": round(base, 2), "when": when(start), "set": bool(anchor),
                      "motif": anchor.get("motif") if anchor else None},
            "totals": {k: round(v, 2) for k, v in totals.items()},
            "balance": round(balance, 2),
            "control": _control(moves, name),
            "pending_sheet": sum(1 for m in moves if m["ravitailleur"] == name and m["sheet_state"] == "pending"),
            "history": history[:80],
        })
    lost = [r for r in restocks if parse_ts(r["created_at"]) > week_start and attribute(r, users_by_id, known) is None]
    return {"ravis": out, "week_start": when(week_start),
            "unassigned": {"total": round(sum(float(r["cash"]) for r in lost), 2),
                           "items": [{"id": r["id"], "when": when(r["created_at"]), "livreur": who(r),
                                     "amount": float(r["cash"]),
                                     "by": (users_by_id.get(r.get("by_user_id")) or {}).get("display_name")}
                                    for r in lost]}}


async def account(name: str) -> dict | None:
    data = await accounts()
    return next((a for a in data["ravis"] if _key(a["name"]) == _key(name)), None)


async def total_balance() -> float | None:
    """Total de ce qui reste chez les ravitailleurs (None si aucun)."""
    data = await accounts()
    return round(sum(a["balance"] for a in data["ravis"]), 2) if data["ravis"] else None


# ---------------------------------------------------------------- actions

async def _ravi_user(name: str) -> dict | None:
    return next((u for u in await db.list_users(role="ravitailleur", status="active")
                 if _key(u.get("display_name")) == _key(name)), None)


async def add_move(bot, actor: dict, name: str, kind: str, amount, motif: str | None) -> tuple[dict | None, str]:
    """Charge, invest, perso ou dépôt ; le ravitailleur ne note que ses charges. → (mouvement, message)."""
    from bot.services import sheets

    known = await names()
    name = canonical(name, known)
    amount = clean_amount(amount)
    if name is None:
        return None, texts.RAVI_UNKNOWN
    if kind not in KINDS or (actor["role"] == "ravitailleur" and kind != "charge"):
        return None, texts.NOT_FOR_YOU
    if amount is None:
        return None, texts.RAVI_AMOUNT
    motif = " ".join(str(motif or "").split())[:MAX_MOTIF] or None
    move = await db.create_ravi_move({"ravitailleur": name, "kind": kind, "amount": amount, "motif": motif,
                                      "by_user_id": actor["id"],
                                      "sheet_state": "pending" if sheets.enabled() else "none"})
    await db.log_event("ravi_move", user_id=actor["id"], payload={"move": move["id"], "kind": kind})
    acc = await account(name)
    balance = acc["balance"] if acc else 0.0
    if actor["role"] == "ravitailleur":
        await messaging.notify_dispatch(bot, texts.d_ravi_charge(name, amount, motif, balance), team=False,
                                        skip=[actor["id"]])
    else:
        target = await _ravi_user(name)
        if target and target["id"] != actor["id"]:
            await messaging.send(bot, target, texts.ravi_move_for_ravi(kind, amount, motif, balance))
    await sync_sheet()
    return move, texts.ravi_saved(name, balance)


async def set_start(actor: dict, name: str, amount, motif: str | None = None) -> tuple[dict | None, str]:
    """Nouveau départ : à partir de maintenant, le solde part de ce montant (0 accepté, négatif refusé)."""
    name = canonical(name, await names())
    try:
        value = round(float(str(amount).replace(",", ".").replace("€", "").strip()), 2)
    except (TypeError, ValueError):
        value = -1
    if name is None:
        return None, texts.RAVI_UNKNOWN
    if not 0 <= value <= MAX_AMOUNT:
        return None, texts.RAVI_AMOUNT
    data = await accounts()
    if data.get("source") == "tableau":
        # Les tableaux font foi : l'écart devient une ligne d'ajustement dans RAVI (Dépôt s'il manque,
        # Perso s'il y en a trop), visible et modifiable dans le tableau.
        current = next((a["balance"] for a in data["ravis"] if a["name"] == name), 0.0)
        delta = round(value - current, 2)
        if abs(delta) < 0.01:
            return None, texts.ravi_saved(name, value)
        motif = " ".join(str(motif or "").split())[:MAX_MOTIF] or f"Ajustement : solde fixé à {texts.eur(value)}"
        from bot.services import sheets

        move = await db.create_ravi_move({"ravitailleur": name, "kind": "depot" if delta > 0 else "perso",
                                          "amount": abs(delta), "motif": motif, "by_user_id": actor["id"],
                                          "sheet_state": "pending" if sheets.enabled() else "none"})
        await db.log_event("ravi_start", user_id=actor["id"], payload={"move": move["id"], "delta": delta})
        await sync_sheet()
        return move, texts.ravi_saved(name, value)
    move = await db.create_ravi_move({"ravitailleur": name, "kind": "depart", "amount": value,
                                      "motif": " ".join(str(motif or "").split())[:MAX_MOTIF] or "Solde de départ",
                                      "by_user_id": actor["id"]})
    await db.log_event("ravi_start", user_id=actor["id"], payload={"move": move["id"]})
    return move, texts.ravi_saved(name, value)


async def count(actor: dict, name: str, counted) -> tuple[dict | None, str]:
    """Contrôle de caisse : montant compté, écart avec ce qu'il devait avoir (le solde ne change pas)."""
    name = canonical(name, await names())
    try:
        value = round(float(str(counted).replace(",", ".").replace("€", "").strip()), 2)
    except (TypeError, ValueError):
        value = -1
    if name is None:
        return None, texts.RAVI_UNKNOWN
    if not 0 <= value <= MAX_AMOUNT:
        return None, texts.RAVI_AMOUNT
    acc = await account(name)
    expected = acc["balance"] if acc else 0.0
    move = await db.create_ravi_move({"ravitailleur": name, "kind": "controle", "amount": value,
                                      "expected": expected, "by_user_id": actor["id"]})
    await db.log_event("ravi_count", user_id=actor["id"], payload={"move": move["id"], "gap": value - expected})
    return move, texts.ravi_counted(name, value, expected)


async def align(actor: dict, control_id: int) -> tuple[dict | None, str]:
    """Cale le solde sur le dernier contrôle : nouveau départ au montant compté."""
    control = await db.get_ravi_move(int(control_id))
    if not control or control["kind"] != "controle" or control.get("deleted_at"):
        return None, texts.ALREADY_HANDLED
    gap = float(control["amount"]) - float(control["expected"] or 0)
    return await set_start(actor, control["ravitailleur"], control["amount"],
                           f"Calé sur le contrôle du {when(control['created_at'])} (écart {texts.eur(gap)})")


async def delete(actor: dict, move_id: int) -> tuple[bool, str]:
    """Annule un mouvement (gardé en base) ; s'il est déjà dans RAVI, sa ligne est effacée."""
    move = await db.get_ravi_move(int(move_id))
    if not move or move.get("deleted_at"):
        return False, texts.ALREADY_HANDLED
    state = {"done": "remove", "pending": "none"}.get(move["sheet_state"], move["sheet_state"])
    await db.update_ravi_move(move["id"], {"deleted_at": now_utc().isoformat(), "sheet_state": state})
    await db.log_event("ravi_delete", user_id=actor["id"], payload={"move": move["id"]})
    await sync_sheet()
    return True, texts.RAVI_DELETED


# ---------------------------------------------------------------- copie dans le Compta (onglet RAVI)

def _sheet_row(move: dict) -> dict:
    return {"numero": move["id"], "date": f"{to_paris(parse_ts(move['created_at'])):%d/%m} (bot)",
            "type": KINDS[move["kind"]][3], "montant": float(move["amount"]), "motif": move.get("motif") or "",
            "ravitailleur": move["ravitailleur"]}


async def sync_sheet(context=None) -> int:
    """Écrit (ou efface) dans RAVI les mouvements en attente. Script pas à jour : ils restent en attente."""
    from bot.services import sheets

    if not sheets.enabled():
        return 0
    done = 0
    for move in await db.list_ravi_moves_to_sync():
        if move["sheet_state"] == "pending":
            result = await sheets.fetch_action("ravi_move", payload={"op": "add", **_sheet_row(move)})
            if result and result.get("ok") and (result.get("written") or result.get("exists")):
                await db.update_ravi_move(move["id"], {"sheet_state": "done"})
                done += 1
            elif result and not result.get("ok"):
                log.warning("RAVI : mouvement %s pas écrit : %s", move["id"], result.get("error"))
                break
            else:
                break                                   # ancien script : pas d'action ravi_move
        else:
            result = await sheets.fetch_action("ravi_move", payload={"op": "remove", "numero": move["id"]})
            if result and result.get("ok") and (result.get("removed") or result.get("missing")):
                await db.update_ravi_move(move["id"], {"sheet_state": "removed"})
                done += 1
            else:
                break
    return done


async def carry_over(actor: dict, label: str, before: dict | None = None) -> list[str]:
    """Reset de la semaine : chaque solde devient un départ (le registre continue), et une ligne Dépôt
    « Report semaine passée » est écrite dans RAVI (vidé par le reset) pour que la feuille reste juste.
    before : les comptes lus AVANT que le reset vide les tableaux."""
    from bot.services import sheets

    lines = []
    for acc in (before or await accounts())["ravis"]:
        if not acc["start"]["set"] and abs(acc["balance"]) < 0.01:
            continue
        move = await db.create_ravi_move({"ravitailleur": acc["name"], "kind": "depart",
                                          "amount": acc["balance"], "motif": f"Report ({label})",
                                          "by_user_id": actor["id"]})
        lines.append(f"{acc['name']} : {texts.eur(acc['balance'])}")
        if sheets.enabled() and abs(acc["balance"]) >= 0.01:
            await sheets.fetch_action("ravi_move", payload={
                "op": "add", "numero": f"R{move['id']}", "date": f"{to_paris(now_utc()):%d/%m} (bot)",
                "type": "Depot", "montant": acc["balance"], "motif": "Report semaine passée",
                "ravitailleur": acc["name"]})
    return lines
