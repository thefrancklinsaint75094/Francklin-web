"""Onglet ⚡ Actions de l'appli Dispatch : ce qui a été saisi depuis le dernier reset (rechargements et swipes,
ventes de /ventes, dépenses des livreurs, argent du ravitailleur), à corriger ou supprimer.

Une correction change la base ET la ligne du tableau (action `edit_row` du script, ligne retrouvée par sa note
« Bot R#… », « Bot #V… », « Bot D#… » ; RAVI : action `ravi_move`). Si le tableau ne peut pas être modifié
(script pas à jour, injoignable), rien n'est changé : la base et le tableau restent d'accord. Les produits se
choisissent dans le catalogue (jamais tapés) : un produit inconnu ne peut pas entrer par ici.
"""
from __future__ import annotations

import logging
from datetime import datetime

from bot import config, db, texts
from bot.services import names as prenoms
from bot.timeutil import now_utc, parse_ts, to_paris

log = logging.getLogger(__name__)

MAX_QTY = 999
MAX_CASH = 99_999
MAX_MOTIF = 80
MAX_ADDRESS = 150
DAYS = ("Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim.")
SCRIPT_OLD = ("⚠️ Le script Google n'est pas à jour : le tableau ne peut pas être corrigé, rien n'est changé. "
              "Colle la dernière version du script (Déployer → Gérer les déploiements → Nouvelle version).")
WEEK_CLOSED = "Cette action est d'avant le dernier reset : elle n'est plus dans les tableaux."


def _when(ts) -> str:
    at = to_paris(parse_ts(ts))
    return f"{DAYS[at.weekday()]} {at:%H:%M}"


def _num(value, cast=float):
    try:
        return cast(str(value).replace(",", ".").replace("€", "").strip())
    except (TypeError, ValueError):
        return None


def _items_text(items: list[dict]) -> str:
    return ", ".join(f"{int(i['q'])} {i['p']}" for i in items or [])


def _euros(value) -> str:
    v = float(value or 0)
    return f"{v:g} €" if v == int(v) else f"{v:.2f} €".replace(".", ",")


# ---------------------------------------------------------------- historique

async def history(now: datetime | None = None) -> dict:
    """Actions depuis le dernier reset, de la plus récente à la plus ancienne."""
    from bot.services import ravi

    now = now or now_utc()
    since = await ravi._week_start(now)
    restocks = await db.list_restocks_between(since, now)
    sales = await db.list_sales_between(since, now)
    expenses = await db.list_expenses_between(since, now)
    moves = [m for m in await db.list_ravi_moves() if parse_ts(m["created_at"]) >= since]
    users = await db.get_users([r.get("livreur_id") for r in restocks] + [r.get("to_livreur_id") for r in restocks]
                               + [r["by_user_id"] for r in restocks + sales + expenses + moves]
                               + [e.get("livreur_id") for e in expenses])
    items = ([_restock_item(r, users) for r in restocks] + [_sale_item(s, users) for s in sales]
             + [_expense_item(e, users) for e in expenses] + [_ravi_item(m, users) for m in moves])
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"ok": True, "since": _when(since), "items": items}


def _by(row: dict, users: dict) -> str:
    return (users.get(row.get("by_user_id")) or {}).get("display_name") or ""


def _livreur(row: dict, users: dict, key: str = "livreur") -> str:
    return (users.get(row.get(f"{key}_id")) or {}).get("display_name") or row.get(f"{key}_name") or "?"


def _restock_item(r: dict, users: dict) -> dict:
    who = _livreur(r, users)
    cash = float(r.get("cash") or 0)
    if r["kind"] == "swipe":
        icon, title = "↔️", f"Swipe {prenoms.label(who)} → {prenoms.label(_livreur(r, users, 'to_livreur'))}"
    elif r["kind"] == "unload":
        icon, title = "↩️", f"Repris à {prenoms.label(who)}"
    elif r["kind"] == "cash":
        icon, title = "💶", f"Cash récupéré chez {prenoms.label(who)}"
    else:
        icon, title = "🔄", f"Chargé à {prenoms.label(who)}"
    detail = [x for x in (_items_text(r.get("items")), f"{_euros(cash)} récupérés" if cash else "",
                          r.get("box") if r.get("box") and r["kind"] in ("load", "unload") else "") if x]
    return {"type": "restock", "id": r["id"], "at": r["created_at"], "when": _when(r["created_at"]), "icon": icon,
            "title": title, "detail": " · ".join(detail), "by": r.get("ravitailleur_name") or _by(r, users),
            "fields": {"kind": r["kind"], "livreur": who, "box": r.get("box") or "",
                       "items": [{"p": i["p"], "q": int(i["q"])} for i in r.get("items") or []], "cash": cash}}


def _sale_item(s: dict, users: dict) -> dict:
    pay = "CB / virement" if s["payment"] == "virement" else "espèces"
    detail = [f"{int(s['qty'])} {s['product']}", f"{_euros(s['price'])} ({pay})", s.get("address") or ""]
    return {"type": "sale", "id": s["id"], "at": s["created_at"], "when": _when(s["created_at"]), "icon": "🧾",
            "title": f"Vente — {prenoms.label(s['livreur_name'])}", "detail": " · ".join(x for x in detail if x),
            "by": _by(s, users),
            "fields": {"livreur": s["livreur_name"], "product": s["product"], "qty": int(s["qty"]),
                       "price": float(s["price"]), "payment": s["payment"], "address": s.get("address") or ""}}


def _expense_item(e: dict, users: dict) -> dict:
    who = _livreur(e, users)
    label = "Paye" if e["kind"] == "paye" else "Charge"
    return {"type": "expense", "id": e["id"], "at": e["created_at"], "when": _when(e["created_at"]),
            "icon": "💸" if e["kind"] == "paye" else "🧾", "title": f"{label} — {prenoms.label(who)}",
            "detail": " · ".join(x for x in (_euros(e["amount"]), e.get("motif") or "") if x), "by": _by(e, users),
            "fields": {"livreur": who, "kind": e["kind"], "amount": float(e["amount"]), "motif": e.get("motif") or ""}}


def _ravi_item(m: dict, users: dict) -> dict:
    from bot.services import ravi

    label = {"depart": "Solde de départ", "controle": "Caisse comptée"}.get(m["kind"]) or ravi.KINDS[m["kind"]][1]
    icon = {"depart": "🏁", "controle": "🧮"}.get(m["kind"]) or ravi.KINDS[m["kind"]][0]
    return {"type": "ravi", "id": m["id"], "at": m["created_at"], "when": _when(m["created_at"]), "icon": icon,
            "title": f"{label} — {m['ravitailleur']}",
            "detail": " · ".join(x for x in (_euros(m["amount"]), m.get("motif") or "") if x), "by": _by(m, users),
            "pending": m.get("sheet_state") == "pending", "editable": m["kind"] in ravi.KINDS,
            "fields": {"kind": m["kind"], "amount": float(m["amount"]), "motif": m.get("motif") or ""}}


# ---------------------------------------------------------------- tableau

async def _sheet(kind: str, numero, row: dict | None) -> str | None:
    """Corrige (row) ou efface (None) la ligne du tableau. None si c'est fait (ou sans tableau), sinon l'erreur."""
    from bot.services import sheets

    if not sheets.enabled():
        return None
    result = await sheets.fetch_action("edit_row", payload={"kind": kind, "numero": numero,
                                                            "op": "replace" if row else "remove", "row": row})
    if result and result.get("ok") and "removed" in result:
        return None
    if result and result.get("ok"):
        return SCRIPT_OLD                                  # ancien script : action inconnue
    return f"⚠️ Tableau injoignable, rien n'est changé : {(result or {}).get('error') or 'pas de réponse'}"


def _forget(token: str, *keys) -> None:
    """Les mouvements « en vol » de l'action ne comptent plus : le tableau est à jour."""
    from bot.services import stock

    for key in keys:
        if key:
            stock.remove_inflight(key, token)


async def _in_week(row: dict) -> bool:
    from bot.services import ravi

    return parse_ts(row["created_at"]) >= await ravi._week_start(now_utc())


async def _resolve_livreur(name: str) -> tuple[str | None, dict | None]:
    from bot.services.sales import resolve_livreur

    return resolve_livreur(str(name or ""), list(config.get().livreur_names),
                           await db.list_users(role="livreur", status="active"))


async def _products() -> dict[str, str]:
    return {p["name"].lower(): p["name"] for p in await db.list_products()}


# ---------------------------------------------------------------- corriger

async def edit(actor: dict, kind: str, ident, body: dict) -> tuple[bool, str]:
    ident = _num(ident, int)
    if ident is None:
        return False, texts.ALREADY_HANDLED
    if kind == "ravi":
        from bot.services import ravi

        return await ravi.edit(actor, ident, str(body.get("kind") or ""), body.get("amount"), body.get("motif"))
    handler = {"restock": _edit_restock, "sale": _edit_sale, "expense": _edit_expense}.get(kind)
    if handler is None:
        return False, "Action inconnue."
    return await handler(actor, ident, body)


async def _edit_restock(actor: dict, ident: int, body: dict) -> tuple[bool, str]:
    from bot.services import cash as cash_svc
    from bot.services import restock as rs
    from bot.services import sheets, stock

    r = await db.get_restock(ident)
    if not r:
        return False, texts.ALREADY_HANDLED
    if not await _in_week(r):
        return False, WEEK_CLOSED
    cash = _num(body.get("cash") or 0)
    if cash is None or not 0 <= cash <= MAX_CASH:
        return False, "Montant de cash illisible."
    products = await _products()
    merged: dict[str, int] = {}
    for item in (body.get("items") or []) if r["kind"] != "cash" else []:
        name = products.get(str(item.get("p") or "").strip().lower())
        qty = _num(item.get("q"), int)
        if name is None:
            return False, "Choisis chaque produit dans la liste."
        if qty is None or not 0 < qty <= MAX_QTY:
            return False, f"Quantité illisible pour {name}."
        merged[name] = merged.get(name, 0) + qty
    if not merged and cash <= 0:
        return False, "Il ne reste rien : supprime plutôt l'action (🗑)."
    fields = {"items": [{"p": p, "q": q} for p, q in merged.items()], "cash": round(cash, 2)}
    if r["kind"] != "swipe":
        name, livreur = await _resolve_livreur(body.get("livreur")
                                               or _livreur(r, await db.get_users([r.get("livreur_id")])))
        if name is None:
            return False, "Choisis le livreur dans la liste."
        fields.update({"livreur_id": livreur["id"] if livreur else None, "livreur_name": name})
    if r["kind"] in ("load", "unload"):
        box = str(body.get("box") or r.get("box") or "")
        if box not in config.get().boxes and box != r.get("box"):
            return False, "Choisis le box dans la liste."
        fields["box"] = box or None
    new = {**r, **fields}
    users = await db.get_users([new.get("livreur_id"), new["by_user_id"]])
    error = await _sheet("recharge", r["id"], sheets.restock_row(new, users))
    if error:
        return False, error
    await db.update_restock(r["id"], fields)
    token = f"restock:{r['id']}"
    _forget(token, r.get("livreur_id"), r.get("to_livreur_id"),
            stock.box_key(r["box"]) if r.get("box") and r["box"] != rs.SWIPE_BOX else None,
            cash_svc.key(r["livreur_id"]) if r.get("livreur_id") else None,
            cash_svc.key(r["to_livreur_id"]) if r.get("to_livreur_id") else None)
    await _logged(actor, "restock", r, fields)
    return True, "✅ Rechargement corrigé (base et tableau)."


async def _edit_sale(actor: dict, ident: int, body: dict) -> tuple[bool, str]:
    from bot.services import cash as cash_svc
    from bot.services import sheets
    from bot.services.order_edit import valid_price

    s = await db.get_sale(ident)
    if not s:
        return False, texts.ALREADY_HANDLED
    if not await _in_week(s):
        return False, WEEK_CLOSED
    name, livreur = await _resolve_livreur(body.get("livreur") or s["livreur_name"])
    product = (await _products()).get(str(body.get("product") or "").strip().lower())
    qty = _num(body.get("qty"), int)
    price = _num(body.get("price"))
    if name is None:
        return False, "Choisis le livreur dans la liste."
    if product is None:
        return False, "Choisis le produit dans la liste."
    if qty is None or not 0 < qty <= MAX_QTY:
        return False, "Quantité illisible."
    if price is None or (price != 0 and not valid_price(price)):
        return False, "Prix : de 10 en 10 € (ou 0 si c'est offert)."
    fields = {"livreur_name": name, "livreur_id": livreur["id"] if livreur else None, "product": product,
              "qty": qty, "price": price, "payment": "virement" if body.get("payment") == "virement" else "especes",
              "address": " ".join(str(body.get("address") or "").split())[:MAX_ADDRESS] or None}
    error = await _sheet("vente", f"V{s['id']}", sheets.sale_row({**s, **fields}))
    if error:
        return False, error
    await db.update_sale(s["id"], fields)
    token = f"sale:{s['id']}"
    _forget(token, s.get("livreur_id"), cash_svc.key(s["livreur_id"]) if s.get("livreur_id") else None)
    await _logged(actor, "sale", s, fields)
    return True, "✅ Vente corrigée (base et tableau)."


async def _edit_expense(actor: dict, ident: int, body: dict) -> tuple[bool, str]:
    from bot.services import cash as cash_svc
    from bot.services import sheets

    e = await db.get_expense(ident)
    if not e:
        return False, texts.ALREADY_HANDLED
    if not await _in_week(e):
        return False, WEEK_CLOSED
    name, livreur = await _resolve_livreur(body.get("livreur") or _livreur(e, await db.get_users([e.get("livreur_id")])))
    amount = _num(body.get("amount"))
    if name is None:
        return False, "Choisis le livreur dans la liste."
    if amount is None or not 0 < amount <= MAX_CASH:
        return False, texts.EXPENSE_ZERO
    fields = {"livreur_name": name, "livreur_id": livreur["id"] if livreur else None,
              "kind": "paye" if body.get("kind") == "paye" else "charges", "amount": round(amount, 2),
              "motif": " ".join(str(body.get("motif") or "").split())[:MAX_MOTIF] or None}
    new = {**e, **fields}
    error = await _sheet("depense", e["id"], sheets.expense_row(new, await db.get_users([new.get("livreur_id")])))
    if error:
        return False, error
    await db.update_expense(e["id"], fields)
    _forget(f"expense:{e['id']}", cash_svc.key(e["livreur_id"]) if e.get("livreur_id") else None)
    await _logged(actor, "expense", e, fields)
    return True, "✅ Dépense corrigée (base et tableau)."


async def _logged(actor: dict, kind: str, before: dict, fields: dict | None) -> None:
    keep = {k: before.get(k) for k in (fields or before) if k in before and k != "created_at"}
    await db.log_event("action_edit" if fields is not None else "action_delete", user_id=actor["id"],
                       payload={"type": kind, "id": before["id"], "before": keep})


# ---------------------------------------------------------------- supprimer

async def delete(actor: dict, kind: str, ident) -> tuple[bool, str]:
    from bot.services import cash as cash_svc
    from bot.services import restock as rs
    from bot.services import stock

    ident = _num(ident, int)
    if ident is None:
        return False, texts.ALREADY_HANDLED
    if kind == "ravi":
        from bot.services import ravi

        return await ravi.delete(actor, ident)
    getter, sheet_kind, numero, remover, token = {
        "restock": (db.get_restock, "recharge", lambda r: r["id"], db.delete_restock, "restock"),
        "sale": (db.get_sale, "vente", lambda r: f"V{r['id']}", db.delete_sale, "sale"),
        "expense": (db.get_expense, "depense", lambda r: r["id"], db.delete_expense, "expense"),
    }.get(kind, (None,) * 5)
    if getter is None:
        return False, "Action inconnue."
    row = await getter(ident)
    if not row:
        return False, texts.ALREADY_HANDLED
    if not await _in_week(row):
        return False, WEEK_CLOSED
    error = await _sheet(sheet_kind, numero(row), None)
    if error:
        return False, error
    await remover(row["id"])
    keys = [row.get("livreur_id"), cash_svc.key(row["livreur_id"]) if row.get("livreur_id") else None]
    if kind == "restock":
        keys += [row.get("to_livreur_id"), cash_svc.key(row["to_livreur_id"]) if row.get("to_livreur_id") else None,
                 stock.box_key(row["box"]) if row.get("box") and row["box"] != rs.SWIPE_BOX else None]
    _forget(f"{token}:{row['id']}", *keys)
    await _logged(actor, kind, row, None)
    return True, "🗑 Supprimé (base et tableau)."
