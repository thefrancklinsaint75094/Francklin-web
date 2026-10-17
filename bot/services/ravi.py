"""Argent du ravitailleur (espace « 💼 Ravi » de l'appli Dispatch, solde dans l'appli Ravitaillement).

Ce qu'un ravitailleur doit avoir sur lui = solde de départ (dernier « départ » : fixé par l'exploitant, calé sur un
contrôle de caisse ou reporté au reset de la semaine)
  + cash récupéré auprès des livreurs (rechargements du bot, colonne Cash, depuis le départ)
  + dépôts (l'exploitant lui donne)
  − charges − invest − remis à l'exploitant (perso).
Le registre est gardé d'une semaine à l'autre ; chaque charge / invest / perso / dépôt est recopié dans l'onglet
RAVI du Compta (action « ravi_move » du script). Un contrôle de caisse note le montant compté et l'écart, sans
toucher au solde ; « caler sur le compté » crée un nouveau départ.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from bot import config, db, messaging, texts
from bot.timeutil import now_utc, parse_ts, to_paris

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
    """{"ravis": [compte…], "unassigned": {"total", "items"}, "week_start"} — un compte par ravitailleur."""
    now = now or now_utc()
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
        controls = [m for m in moves if m["ravitailleur"] == name and m["kind"] == "controle"]
        last = controls[-1] if controls else None
        history.sort(key=lambda h: h["at"], reverse=True)
        has_account = any(u["role"] == "ravitailleur" and _key(u.get("display_name")) == _key(name) for u in users)
        out.append({
            "name": name, "account": has_account,
            "start": {"amount": round(base, 2), "when": when(start), "set": bool(anchor),
                      "motif": anchor.get("motif") if anchor else None},
            "totals": {k: round(v, 2) for k, v in totals.items()},
            "balance": round(balance, 2),
            "control": {"id": last["id"], "when": when(last["created_at"]), "counted": float(last["amount"]),
                        "expected": float(last["expected"] or 0),
                        "gap": round(float(last["amount"]) - float(last["expected"] or 0), 2),
                        "aligned": any(m["kind"] == "depart" and m["ravitailleur"] == name
                                       and parse_ts(m["created_at"]) >= parse_ts(last["created_at"]) for m in moves)}
            if last else None,
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


async def carry_over(actor: dict, label: str) -> list[str]:
    """Reset de la semaine : chaque solde devient un départ (le registre continue), et une ligne Dépôt
    « Report semaine passée » est écrite dans RAVI (vidé par le reset) pour que la feuille reste juste."""
    from bot.services import sheets

    lines = []
    for acc in (await accounts())["ravis"]:
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
