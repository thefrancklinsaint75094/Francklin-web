"""Appli des ravitailleurs « Ravitaillement » (mini-application Telegram) : simple, gros boutons, tout y est.

GET /ravi → bot/static/ravi.html. Les livreurs (en service d'abord) avec ce qu'ils ont sur eux et le cash à
récupérer ; un livreur touché → 📦 CHARGER, ↩️ REPRENDRE, 💶 RÉCUPÉRER DU CASH, ↔️ DONNER À UN AUTRE LIVREUR, avec
des compteurs − / + ; le stock des box ; le cash de chacun ; les goûts.

Tout passe par le même code que /ravi et /swipe dans le chat (récap, puis ✅ ENREGISTRER) : rechargements en
base, livreurs et dispatch prévenus, tableau Rechargement rempli. Accès : signature Telegram, ravitailleur actif.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from bot import config, db, texts
from bot.services import names

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "ravi.html"


async def _me(request: web.Request) -> dict:
    from bot import web as admin_web

    tg = admin_web.check_init_data(request.headers.get("X-Init-Data", ""), config.get().telegram_bot_token)
    if not tg or not tg.get("id"):
        raise web.HTTPUnauthorized(text="Ouvre l'appli depuis Telegram.")
    user = await db.get_user_by_tg(int(tg["id"]))
    if not user or user["status"] != "active" or user["role"] != "ravitailleur":
        raise web.HTTPForbidden(text="Réservé aux ravitailleurs.")
    return user


def _key(name: str | None) -> str:
    return " ".join(str(name or "").lower().split())


async def board(user: dict) -> dict:
    """Livreurs (en service d'abord) avec leur stock et leur cash, box, produits connus."""
    from bot.handlers import restock_text
    from bot.services import cash, stock

    stocks, money, boxes = await asyncio.gather(stock.livreurs_now(), cash.livreurs_now(), stock.boxes_now())
    accounts = {_key(u.get("display_name")): u for u in await db.list_users(role="livreur", status="active")}
    sheet_names = await restock_text._livreur_names()
    stock_by = {_key(k): v for k, v in (stocks.get("livreurs") or {}).items()} if stocks["ok"] else {}
    cash_by = {_key(r["nom"]): r for r in (money.get("livreurs") or {}).values()} if money["ok"] else {}
    livreurs = []
    for name in sheet_names:
        account = accounts.get(_key(name))
        items = {p: q for p, q in (stock_by.get(_key(name)) or {}).items() if float(q or 0)}
        row = cash_by.get(_key(name)) or {}
        livreurs.append({"name": name, "prenom": names.prenom(name), "on_duty": bool(account and account.get("on_duty")),
                         "account": bool(account), "items": items, "cash": round(float(row.get("cash") or 0), 2)})
    livreurs.sort(key=lambda lv: (not lv["on_duty"], -lv["cash"], lv["name"]))
    return {
        "me": {"name": user.get("display_name"), "prenom": names.prenom(user.get("display_name"))},
        "livreurs": livreurs,
        "boxes": [{"name": b, "items": {p: q for p, q in (v or {}).items() if float(q or 0)}}
                  for b, v in (boxes.get("boxes") or {}).items()] if boxes["ok"] else [],
        "box_names": list(config.get().boxes),
        "products": [p["name"] for p in await db.list_products()],
        "ravitailleur_cash": money.get("ravitailleur") if money["ok"] else None,
        "errors": [e for e in (stocks.get("error"), money.get("error"), boxes.get("error")) if e],
    }


async def flavors() -> dict:
    from bot.services import variants

    products = [p for p in await db.list_products() if p.get("variants")]
    by = variants.by_livreur(await db.list_livreur_variants())
    return {"ok": True, "products": [p["name"] for p in products],
            "livreurs": [{"name": name, "prenom": names.prenom(name),
                          "flavors": {p: sorted(v) for p, v in mine.items()}} for name, mine in sorted(by.items())]}


def _text(body: dict) -> str | None:
    """Formulaire de l'appli → texte /ravi ou /swipe (même lecture que dans le chat)."""
    mode, livreur = body.get("mode"), str(body.get("livreur") or "").strip()
    items = [(int(i.get("q") or 0), str(i.get("p") or "").strip()) for i in body.get("items") or []]
    items = [(q, p) for q, p in items if q > 0 and p]
    try:
        cash = round(float(str(body.get("cash") or "0").replace(",", ".")), 2)
    except ValueError:
        cash = 0.0
    if not livreur:
        return None
    if mode == "swipe":
        to = str(body.get("to") or "").strip()
        if not to or _key(to) == _key(livreur):
            return None
        lines = [f"{livreur} > {to}"] + (["tout"] if body.get("all") else [f"{q} {p}" for q, p in items])
    else:
        lines = [livreur]
        if mode in ("load", "unload") and items:
            box = str(body.get("box") or "")
            lines.append(box if box.lower().startswith("box") else f"Box {box}")
            lines += [f"{'-' if mode == 'unload' else ''}{q} {p}" for q, p in items]
    if cash > 0:
        lines.append(f"cash {cash:g}")
    return "\n".join(lines) if len(lines) > 1 else None


async def act(context, user: dict, body: dict) -> dict:
    from bot.handlers import restock_text, swipe
    from bot.services import restock_text as ravi_text

    kind = body.get("type")
    if kind not in ("prepare", "commit"):
        return {"ok": False, "message": "Je n'ai pas compris."}
    text = _text(body)
    if text is None:
        return {"ok": False, "message": "Il manque quelque chose : le livreur, un produit ou le cash."}
    if body.get("mode") == "swipe":
        errors, blocks, warnings = await swipe.prepare(text)
        if errors or not blocks:
            return {"ok": False, "html": texts.swipe_errors(errors) if errors else texts.SWIPE_HELP}
        if kind == "prepare":
            return {"ok": True, "html": texts.swipe_preview(blocks, warnings)}
        return {"ok": True, "html": await swipe.commit(context, user, blocks)}
    errors, blocks, warnings = await restock_text.prepare(text)
    if errors or not blocks:
        return {"ok": False, "html": texts.ravi_errors(errors) if errors else "Ajoute un produit ou du cash."}
    if ravi_text.missing(blocks):
        return {"ok": False, "message": "Choisis le box."}
    if kind == "prepare":
        return {"ok": True, "html": texts.ravi_preview(user["display_name"], blocks, warnings)}
    done = await restock_text.commit(context, user, {"ravi": user["display_name"], "ravi_id": user["id"],
                                                     "blocks": blocks})
    return {"ok": True, "html": done}


# ---------------------------------------------------------------- routes

async def _page(request: web.Request) -> web.Response:
    return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def _board(request: web.Request) -> web.Response:
    user = await _me(request)
    try:
        return web.json_response(await board(user))
    except Exception:  # noqa: BLE001
        log.exception("Tableau ravitailleur impossible")
        return web.json_response({"error": "Les tableaux ne répondent pas, réessaie dans un instant."}, status=503)


async def _flavors(request: web.Request) -> web.Response:
    await _me(request)
    return web.json_response(await flavors())


async def _action(request: web.Request) -> web.Response:
    from bot import web as admin_web

    user = await _me(request)
    body = await admin_web._json(request)
    try:
        result = await act(admin_web._context(request), user, body)
    except Exception:  # noqa: BLE001
        log.exception("Action ravitailleur impossible")
        result = {"ok": False, "message": texts.GENERIC_ERROR}
    return web.json_response(result)


def routes(app: web.Application) -> None:
    app.router.add_get("/ravi", _page)
    app.router.add_post("/api/ravi/board", _board)
    app.router.add_post("/api/ravi/flavors", _flavors)
    app.router.add_post("/api/ravi/action", _action)
