"""/close : débrief de la journée (admins) — « la journée est finie ».

Ne change rien : courses livrées (espèces / virement), annulations, courses encore ouvertes,
chiffres par livreur et par franchisé, dépenses et rechargements de la nuit ; si Google Sheets
est relié, cash à récupérer et produits sous le seuil. Le dispatch est prévenu quand un
franchisé clôt la journée.

Quelle journée : la nuit en cours ; en journée (entre la fin et le début de nuit), la nuit qui
vient de finir, sauf si des courses ont déjà été livrées depuis la fin de nuit."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, timedelta

from telegram import Update

from bot import config, db, messaging, texts
from bot.handlers import common
from bot.services import cash, sheets, stock
from bot.timeutil import night_bounds, night_label, night_start_date, now_utc, to_paris


async def closing_night() -> date:
    cfg = config.get()
    now = now_utc()
    night = night_start_date(now, cfg.night_end_hour)
    if cfg.night_end_hour <= to_paris(now).hour < cfg.night_start_hour:
        start, end = night_bounds(night, cfg.night_end_hour)
        if not await db.list_delivered_between(start, end):
            night -= timedelta(days=1)
    return night


async def _sheet_extras() -> tuple[list[dict] | None, list[dict] | None]:
    """(cash à récupérer par livreur, produits sous le seuil) ; None si illisible ou non relié."""
    if not sheets.enabled():
        return None, None
    cash_data, boxes = await asyncio.gather(cash.livreurs_now(), stock.boxes_now())
    rows = [r for r in cash_data["livreurs"].values() if abs(r["cash"]) >= 0.01] if cash_data["ok"] else None
    alerts = None
    if boxes["ok"]:
        alerts = [t for t in boxes["totals"] if str(t.get("statut") or "").upper() in ("ALERTE", "RUPTURE")
                  and str(t.get("produit") or "").strip()]
    return rows, alerts


async def build_debrief(night: date) -> str:
    cfg = config.get()
    start, end = night_bounds(night, cfg.night_end_hour)
    delivered = await db.list_delivered_between(start, end)
    cancelled = await db.list_closed_between("cancelled", start, end)
    on_site = await db.list_closed_between("cancelled_on_site", start, end)
    expenses = await db.list_expenses_between(start, end)
    restocks = await db.list_restocks_between(start, end)
    swipes = [r for r in restocks if r["kind"] == "swipe"]
    restocks = [r for r in restocks if r["kind"] != "swipe"]
    still_open = len(await db.list_courses_by_status("pending")) + len(await db.list_courses_by_status("assigned"))
    users = await db.get_users([c["livreur_id"] for c in delivered] + [c["franchise_id"] for c in delivered]
                               + [e["livreur_id"] for e in expenses])

    def name(uid: str | None) -> str:
        return users.get(uid, {}).get("display_name") or "?"

    per_livreur: dict[str, dict] = defaultdict(lambda: {"n": 0, "total": 0.0, "especes": 0.0, "virement": 0.0,
                                                        "depenses": 0.0, "mode": None})
    per_franchise: dict[str, dict] = defaultdict(lambda: {"n": 0, "total": 0.0})
    pay = {"especes": 0.0, "virement": 0.0, "": 0.0}
    for c in delivered:
        price = float(c["price"])
        mode = c.get("payment") or ""
        pay[mode] = pay.get(mode, 0.0) + price
        lv = per_livreur[name(c.get("livreur_id"))]
        lv["mode"] = users.get(c.get("livreur_id"), {}).get("transport_mode")
        lv["n"] += 1
        lv["total"] += price
        if mode in ("especes", "virement"):
            lv[mode] += price
        fr = per_franchise[name(c["franchise_id"])]
        fr["n"] += 1
        fr["total"] += price
    for e in expenses:
        per_livreur[name(e["livreur_id"])]["depenses"] += float(e["amount"])

    # Déplacements détectés pendant les courses, avec le mode déclaré par le livreur.
    moves = [(c["id"], name(c.get("livreur_id")), c["detected_mode"],
              users.get(c.get("livreur_id"), {}).get("transport_mode"))
             for c in sorted(delivered, key=lambda c: c["id"]) if c.get("detected_mode")]
    cash_rows, alerts = await _sheet_extras()
    return texts.day_debrief(
        night_label(night),
        delivered=len(delivered), total=sum(float(c["price"]) for c in delivered), pay=pay,
        cancelled=len(cancelled), on_site=len(on_site), still_open=still_open,
        livreurs=sorted(per_livreur.items(), key=lambda kv: (-kv[1]["total"], kv[0])),
        franchises=sorted(per_franchise.items(), key=lambda kv: (-kv[1]["total"], kv[0])),
        charges=sum(float(e["amount"]) for e in expenses if e["kind"] == "charges"),
        payes=sum(float(e["amount"]) for e in expenses if e["kind"] == "paye"),
        restocks=len(restocks), recovered=sum(float(r.get("cash") or 0) for r in restocks),
        cash_rows=cash_rows, alerts=alerts, sheets_on=sheets.enabled(), moves=moves, swipes=len(swipes),
    )


async def close(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not common.is_admin(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    night = await closing_night()
    text = await build_debrief(night)
    await messaging.reply(update, text)
    await db.log_event("day_closed", user_id=user["id"], payload={"night": night.isoformat()})
    if user["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, f"{text}\n\n(par {texts.esc(user['display_name'])})")
