"""Appli des livreurs (mini-application Telegram) : le plus simple possible, mais tout y est.

GET /livreur → bot/static/livreur.html. Un seul écran utile à la fois : commencer sa soirée, confirmer son
stock, les nouvelles courses (« JE LA PRENDS »), SA course (y aller, dans combien de minutes, à donner,
à encaisser, C'EST LIVRÉ → espèces / carte), ce qui vient ensuite ; puis son argent, son stock, ses goûts,
une dépense, la pause et la fin de soirée.

Chaque action passe par exactement le même code que les boutons et les messages du chat (mêmes règles,
mêmes avis au franchisé et au dispatch). Accès : la signature initData de Telegram, livreur actif.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from bot import config, db, texts
from bot.services import catalog, lifecycle, names, order_edit, transport
from bot.timeutil import hhmm, minutes_since, night_bounds, night_start_date, now_utc, parse_ts

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "livreur.html"


async def _me(request: web.Request) -> dict:
    from bot import web as admin_web

    tg = admin_web.check_init_data(request.headers.get("X-Init-Data", ""), config.get().telegram_bot_token)
    if not tg or not tg.get("id"):
        raise web.HTTPUnauthorized(text="Ouvre l'appli depuis Telegram.")
    user = await db.get_user_by_tg(int(tg["id"]))
    if not user or user["status"] != "active" or user["role"] != "livreur":
        raise web.HTTPForbidden(text="Réservé aux livreurs.")
    return user


def _hm(value) -> str | None:
    ts = parse_ts(value) if value else None
    return hhmm(ts) if ts else None


def _nav(c: dict) -> dict:
    lat, lon = c.get("lat"), c.get("lon")
    if lat is None or lon is None:
        return {}
    return {"maps": f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}",
            "waze": f"https://waze.com/ul?ll={lat},{lon}&navigate=yes"}


def _traffic(course_id: int, now) -> dict | None:
    from bot.services import traffic

    live = traffic.live(course_id)
    if not live:
        return None
    return {"at": hhmm(live["at"]), "in": max(0, int((live["at"] - now).total_seconds() // 60)), "delay": live["delay"]}


async def board(user: dict) -> dict:
    from bot.handlers import declare as declare_h
    from bot.services import declare
    from bot.services.distance import haversine_m

    cfg = config.get()
    now = now_utc()
    courses = sorted(await db.list_assigned_for_livreur(user["id"]), key=lambda c: (c.get("assigned_at") or "", c["id"]))
    pos = await db.get_position(user["id"])
    pos_min = minutes_since(parse_ts(pos["updated_at"])) if pos else None
    blocked = user["id"] in await declare.blocked_ids()

    current = None
    if courses:
        c = courses[0]
        franchise = await db.get_user(c["franchise_id"]) or {}
        eta = parse_ts(c.get("eta_at")) if c.get("eta_at") else None
        msgs = await db.list_course_messages(c["id"], 6)
        current = {
            "id": c["id"], "address": c.get("address"), "detail": c.get("address_detail"), "district": c.get("district"),
            "lines": order_edit.lines_from_course(c, await catalog.load()), "products": c.get("products"),
            "price": float(c.get("price") or 0), "requested_time": c.get("requested_time"),
            "eta_at": hhmm(eta) if eta else None, "eta_in": max(0, int((eta - now).total_seconds() // 60)) if eta else None,
            "franchise": franchise.get("display_name") or "Franchisé",
            "pending_edit": bool(c.get("pending_edit")), "nav": _nav(c), "traffic": _traffic(c["id"], now),
            "messages": [{"mine": m["sender_id"] == user["id"], "text": m["content"], "at": _hm(m["created_at"])}
                         for m in msgs],
        }
    proposals = []
    if user.get("on_duty") and not blocked:
        for c in await db.list_open_proposals(user["id"]):
            dist = haversine_m(pos["lat"], pos["lon"], c["lat"], c["lon"]) if pos and c.get("lat") is not None else None
            proposals.append({"id": c["id"], "district": c.get("district"), "products": c.get("products"),
                              "price": float(c.get("price") or 0), "requested_time": c.get("requested_time"),
                              "km": round(dist / 1000, 1) if dist is not None else None})

    night = night_start_date(now, cfg.night_end_hour)
    start, end = night_bounds(night, cfg.night_end_hour)
    mine = [c for c in await db.list_delivered_between(start, end) if c.get("livreur_id") == user["id"]]

    declaration = None
    if cfg.stock_declaration:
        for kind in ("start", "end"):
            d = await db.find_declaration(user["id"], kind, declare_h.current_night())
            if d and d["status"] == "asked":
                declaration = {"id": d["id"], "kind": kind, "expected": d.get("expected")}
                break

    return {
        "me": {"name": user.get("display_name"), "prenom": names.prenom(user.get("display_name")),
               "on_duty": bool(user.get("on_duty")), "transport": user.get("transport_mode"),
               "soon_free": bool(user.get("soon_free")),
               "position_min": pos_min, "position_ok": pos_min is not None and pos_min <= cfg.position_stale_minutes},
        "now": hhmm(now), "blocked": blocked, "declaration": declaration,
        "current": current,
        "next": [{"id": c["id"], "district": c.get("district"), "price": float(c.get("price") or 0)} for c in courses[1:]],
        "proposals": proposals,
        "night": {"n": len(mine), "total": sum(float(c["price"]) for c in mine),
                  "especes": sum(float(c["price"]) for c in mine if c.get("payment") == "especes"),
                  "list": [{"id": c["id"], "district": c.get("district"), "price": float(c["price"]),
                            "payment": c.get("payment"), "at": _hm(c.get("delivered_at"))}
                           for c in sorted(mine, key=lambda c: c.get("delivered_at") or "", reverse=True)]},
    }


async def section(user: dict, name: str) -> dict:
    from bot.services import cash, stock, variants

    if name == "money":
        data = await cash.livreur_now(user)
        if not data["ok"]:
            return {"ok": False, "error": data["error"]}
        row = data["row"]
        return {"ok": True, **{k: round(float(row.get(k) or 0), 2) for k in ("cash", "especes", "virement",
                                                                              "depenses", "recupere")}}
    if name == "stock":
        have, _ = await stock.current_stock(user)
        products = [p for p in await db.list_products() if p.get("variants")]
        flavors = variants.by_livreur(await db.list_livreur_variants(user["display_name"])).get(user["display_name"], {})
        return {"ok": have is not None, "error": None if have is not None else "Stock illisible pour l'instant.",
                "items": {p: q for p, q in (have or {}).items() if float(q or 0)},
                "flavors": [{"product": p["name"], "variants": [{"name": v, "on": v in flavors.get(p["name"], set())}
                                                                for v in p["variants"]]} for p in products]}
    return {"ok": False, "error": "Écran inconnu."}


async def act(context, user: dict, body: dict) -> dict:
    from bot import web as admin_web
    from bot.handlers import cash as cash_h
    from bot.handlers import close_day, declare as declare_h, livreur as livreur_h, relay
    from bot.services import declare

    kind = body.get("type")
    course = await db.get_course(int(body["course_id"])) if str(body.get("course_id") or "").isdigit() else None
    mine = course is not None and course.get("livreur_id") == user["id"] and course["status"] == "assigned"

    if kind == "start":
        mode = body.get("transport")
        if mode in transport.MODES.values():
            await db.update_user(user["id"], {"transport_mode": mode})
        await admin_web.with_capture(context, user, "/dispo", lambda u: livreur_h.dispo(u, context))
        user = await db.get_user(user["id"]) or user
        return {"ok": True, "message": "C'est parti ! 🟢" if user.get("on_duty") else "Partage ta position 📍"}
    if kind == "transport" and body.get("value") in transport.MODES.values():
        await db.update_user(user["id"], {"transport_mode": body["value"]})
        return {"ok": True, "message": "C'est noté ✅"}
    if kind == "pause":
        await admin_web.with_capture(context, user, "/pause", lambda u: livreur_h.pause(u, context))
        return {"ok": True, "message": "Tu es en pause ⏸"}
    if kind == "take":
        ok, text = await livreur_h.take_course(context, user, int(body.get("course_id") or 0))
        return {"ok": bool(ok), "message": text or texts.TOO_LATE}
    if kind == "eta":
        minutes = livreur_h.eta_minutes(str(body.get("minutes") or ""))
        if not mine or minutes is None:
            return {"ok": False, "message": "Écris un nombre de minutes (par exemple 25)."}
        await admin_web.with_capture(context, user, str(minutes),
                                     lambda u: livreur_h.set_eta(u, context, user, minutes, course["id"]))
        return {"ok": True, "message": f"Le franchisé sait que tu arrives dans {minutes} min ✅"}
    if kind == "deliver":
        if not mine:
            return {"ok": False, "message": texts.COURSE_FINISHED}
        current = await livreur_h.current_course(user)
        if current and current["id"] != course["id"]:
            return {"ok": False, "message": texts.finish_first(current["id"])}
        payment = lifecycle.PAYMENTS.get(str(body.get("payment") or ""))
        if payment is None:
            return {"ok": False, "message": texts.PAYMENT_PROMPT}
        done = await lifecycle.deliver(context, course, payment=payment)
        return {"ok": done is not None, "message": "Bravo, c'est livré ✅" if done else "Déjà livrée."}
    if kind == "edit":
        if not mine:
            return {"ok": False, "message": texts.COURSE_FINISHED}
        lines = []
        for line in (body.get("lines") or [])[:order_edit.MAX_LINES]:
            try:
                name, qty, price = str(line["p"]).strip(), int(line["q"]), round(float(line["x"]), 2)
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "message": "Une ligne est mal remplie."}
            if name and qty > 0:
                lines.append({"p": name, "q": min(qty, order_edit.MAX_QTY), "x": max(price, 0.0)})
        if not lines:
            return {"ok": False, "message": texts.ORDER_EDIT_EMPTY}
        if order_edit.missing_prices(lines):
            return {"ok": False, "message": texts.order_edit_missing_price(order_edit.missing_prices(lines))}
        if order_edit.off_step_prices(lines):
            return {"ok": False, "message": texts.order_edit_off_step(order_edit.off_step_prices(lines))}
        fields = {"products": order_edit.products_text(lines), "price": order_edit.total(lines)}
        franchise = await livreur_h.request_edit(context, user, course, fields)
        if franchise is None:
            return {"ok": False, "message": texts.COURSE_FINISHED}
        return {"ok": True, "message": f"Envoyé à {franchise.get('display_name') or 'au franchisé'} : il doit valider ✅"}
    if kind == "cancel":
        if not mine:
            return {"ok": False, "message": texts.COURSE_FINISHED}
        done = await lifecycle.release(context, course, reason="livreur_cancel")
        return {"ok": done is not None, "message": "La course est rendue, un autre livreur va la prendre."}
    if kind == "message":
        text = " ".join(str(body.get("text") or "").split())[:1000]
        if not mine or not text:
            return {"ok": False, "message": "Écris ton message."}
        await admin_web.with_capture(context, user, text,
                                     lambda u: relay.relay_text(u, context, user, course["id"], text))
        return {"ok": True, "message": "Message envoyé ✅"}
    if kind == "soon_free":
        courses = await db.list_assigned_for_livreur(user["id"])
        if not courses:
            return {"ok": False, "message": texts.SOON_FREE_NO_COURSE}
        if not user.get("soon_free"):
            await db.update_user(user["id"], {"soon_free": True})
            await db.log_event("soon_free", courses[0]["id"], user["id"], {"auto": False, "app": True})
            from bot.services import broadcast

            await broadcast.kick_pending(context)
        return {"ok": True, "message": "OK, je te propose la suite ✅"}
    if kind == "expense":
        try:
            amount = round(float(str(body.get("amount") or "0").replace(",", ".")), 2)
        except ValueError:
            amount = 0
        if amount <= 0:
            return {"ok": False, "message": texts.EXPENSE_ZERO}
        row = await cash_h.save_expense(context, user, user, "paye" if body.get("kind") == "paye" else "charges",
                                        amount, str(body.get("motif") or "").strip()[:80])
        return {"ok": True, "message": f"Dépense de {texts.eur(row['amount'])} notée ✅"}
    if kind in ("declare_ok", "declare_fix"):
        decl = await db.get_declaration(int(body.get("id") or 0))
        if decl is None or decl["livreur_id"] != user["id"] or decl["status"] == "confirmed":
            return {"ok": True, "message": texts.DECLARE_DONE_ALREADY}
        if kind == "declare_ok":
            if decl.get("expected") is None:
                return {"ok": False, "message": texts.DECLARE_WRITE_IT}
            items = dict(decl["expected"])
        else:
            text = ", ".join(f"{int(i['q'])} {str(i['p']).strip()}" for i in body.get("items") or []
                             if str(i.get("p") or "").strip() and int(i.get("q") or 0) > 0) or "rien"
            parsed = declare.parse(text, await catalog.load())
            if parsed.errors:
                return {"ok": False, "message": " · ".join(parsed.errors)}
            items = parsed.items
        await declare_h.confirm(context, user, decl, items)
        return {"ok": True, "message": "Merci, c'est noté ✅"}
    if kind == "flavor":
        product, variant = str(body.get("product") or ""), str(body.get("variant") or "")
        known = next((p for p in await db.list_products() if p["name"] == product), None)
        if not known or variant not in (known.get("variants") or []):
            return {"ok": False, "message": texts.ALREADY_HANDLED}
        if body.get("on"):
            await db.add_livreur_variants(user["display_name"], product, [variant])
        else:
            await db.remove_livreur_variant(user["display_name"], product, variant)
        return {"ok": True}
    if kind == "close":
        out = await admin_web.with_capture(context, user, "/close", lambda u: close_day.close(u, context))
        return {"ok": True, "html": "\n\n".join(out)}
    return {"ok": False, "message": "Je n'ai pas compris."}


# ---------------------------------------------------------------- routes

async def _page(request: web.Request) -> web.Response:
    return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def _board(request: web.Request) -> web.Response:
    return web.json_response(await board(await _me(request)))


async def _section(request: web.Request) -> web.Response:
    from bot import web as admin_web

    user = await _me(request)
    body = await admin_web._json(request)
    try:
        return web.json_response(await section(user, str(body.get("name"))))
    except Exception:  # noqa: BLE001
        log.exception("Écran livreur %s impossible", body.get("name"))
        return web.json_response({"ok": False, "error": "Pas disponible pour l'instant, réessaie."})


async def _action(request: web.Request) -> web.Response:
    from bot import web as admin_web

    user = await _me(request)
    body = await admin_web._json(request)
    try:
        result = await act(admin_web._context(request), user, body)
    except Exception:  # noqa: BLE001
        log.exception("Action livreur impossible : %s", body.get("type"))
        result = {"ok": False, "message": texts.GENERIC_ERROR}
    return web.json_response(result)


def routes(app: web.Application) -> None:
    app.router.add_get("/livreur", _page)
    app.router.add_post("/api/me/board", _board)
    app.router.add_post("/api/me/section", _section)
    app.router.add_post("/api/me/action", _action)
