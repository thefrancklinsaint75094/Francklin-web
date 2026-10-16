"""Mini-application Telegram des admins (dispatch, franchisés) : un vrai écran à onglets au lieu d'une suite de
messages. Servie par le bot lui-même (aiohttp, port PORT de Railway), ouverte par le bouton de menu « Dispatch ».

- GET  /            → bot/static/app.html (l'appli)
- POST /api/board   → courses de la nuit (en attente, en cours, livrées), livreurs (avec l'adresse qu'ils sont en
                      train de faire et leur heure d'arrivée), totaux
- POST /api/section → stock (box, livreurs), caisse, produits, équipe
- POST /api/feed    → fil d'activité (tous les avis, même ceux qui n'arrivent plus en message)
- POST /api/action  → attribuer, changer de livreur, livrée, rediffuser, annuler, heure, adresse, service / pause,
                      position, ouvrir dans le chat, commande du menu, niveau de notifications

Chaque requête porte l'en-tête X-Init-Data (Telegram.WebApp.initData) : signature vérifiée avec le token du
bot, puis la personne doit être un admin actif. Les actions réutilisent exactement le code des boutons du chat.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl

from aiohttp import web

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import lifecycle, names, pick, transport
from bot.timeutil import hhmm, minutes_since, night_bounds, night_label, night_start_date, now_utc, parse_ts

log = logging.getLogger(__name__)

APP_HTML = Path(__file__).parent / "static" / "app.html"
INIT_DATA_MAX_AGE = 24 * 3600
_runner: web.AppRunner | None = None
PTB = web.AppKey("ptb", object)          # l'Application python-telegram-bot (bot, job_queue)


# ---------------------------------------------------------------- authentification

def check_init_data(init_data: str, token: str, max_age: int = INIT_DATA_MAX_AGE) -> dict | None:
    """Telegram.WebApp.initData signé → l'utilisateur Telegram ({"id", "first_name", …}), sinon None.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), received):
        return None
    try:
        if max_age and time.time() - int(pairs.get("auth_date") or 0) > max_age:
            return None
        return json.loads(pairs.get("user") or "null")
    except (ValueError, TypeError):
        return None


async def _admin(request: web.Request) -> dict:
    tg = check_init_data(request.headers.get("X-Init-Data", ""), config.get().telegram_bot_token)
    if not tg or not tg.get("id"):
        raise web.HTTPUnauthorized(text="Ouvre l'appli depuis Telegram.")
    user = await db.get_user_by_tg(int(tg["id"]))
    if not common.is_admin(user):
        raise web.HTTPForbidden(text="Réservé au dispatch et aux franchisés.")
    return user


def _context(request: web.Request):
    app = request.app[PTB]
    return SimpleNamespace(bot=app.bot, job_queue=app.job_queue, application=app)


# ---------------------------------------------------------------- données

def _who(user: dict | None) -> dict | None:
    if not user:
        return None
    name = user.get("display_name") or "?"
    return {"id": user["id"], "name": name, "prenom": names.prenom(name)}


def _hm(value) -> str | None:
    ts = parse_ts(value) if value else None
    return hhmm(ts) if ts else None


def _course(c: dict, users: dict, viewer: dict) -> dict:
    franchise = users.get(c.get("franchise_id"))
    livreur = users.get(c.get("livreur_id"))
    assigned = parse_ts(c.get("assigned_at")) if c.get("assigned_at") else None
    return {
        "id": c["id"], "status": c["status"], "address": c.get("address"), "detail": c.get("address_detail"),
        "district": c.get("district"), "products": c.get("products"), "price": float(c.get("price") or 0),
        "requested_time": c.get("requested_time"), "eta": _hm(c.get("eta_at")), "created": _hm(c.get("created_at")),
        "delivered": _hm(c.get("delivered_at")), "payment": c.get("payment"), "round": c.get("broadcast_round"),
        "minutes": minutes_since(assigned) if assigned else None,
        "waiting_pick": c["status"] == "pending" and pick.is_awaiting(c["id"]),
        "no_livreur": c["status"] == "pending" and lifecycle._no_livreur.get(c["id"]) is not None,
        "franchise": _who(franchise), "livreur": _who(livreur), "mine": c.get("franchise_id") == viewer["id"],
    }


async def board(viewer: dict) -> dict:
    cfg = config.get()
    night = night_start_date(now_utc(), cfg.night_end_hour)
    start, end = night_bounds(night, cfg.night_end_hour)
    pending = await db.list_courses_by_status("pending")
    assigned = await db.list_courses_by_status("assigned")
    delivered = await db.list_delivered_between(start, end)
    cancelled = (await db.list_closed_between("cancelled", start, end)
                 + await db.list_closed_between("cancelled_on_site", start, end))
    livreurs = sorted(await db.list_users(role="livreur", status="active"),
                      key=lambda u: (not u.get("on_duty"), u.get("display_name") or ""))
    users = await db.get_users([c["franchise_id"] for c in pending + assigned + delivered]
                               + [c["livreur_id"] for c in assigned + delivered if c.get("livreur_id")])
    users.update({lv["id"]: lv for lv in livreurs})
    positions = await db.get_positions(lv["id"] for lv in livreurs)
    busy: dict[str, int] = {}
    for c in assigned:
        busy[c["livreur_id"]] = busy.get(c["livreur_id"], 0) + 1
    from bot.services import declare

    blocked = await declare.blocked_ids()
    per: dict[str, dict] = {}
    pay = {"especes": 0.0, "virement": 0.0}
    for c in delivered:
        name = (users.get(c.get("livreur_id")) or {}).get("display_name") or "?"
        row = per.setdefault(name, {"name": name, "prenom": names.prenom(name), "n": 0, "total": 0.0})
        row["n"] += 1
        row["total"] += float(c["price"])
        if c.get("payment") in pay:
            pay[c["payment"]] += float(c["price"])
    return {
        "night": night_label(night), "now": hhmm(now_utc()),
        "me": {"id": viewer["id"], "name": viewer.get("display_name"), "role": viewer["role"],
               "dispatch": common.is_dispatch(viewer), "notify_level": viewer.get("notify_level") or "essentiel"},
        "pending": [_course(c, users, viewer) for c in pending],
        "assigned": [_course(c, users, viewer) for c in sorted(assigned, key=lambda c: c.get("assigned_at") or "")],
        "delivered": [_course(c, users, viewer) for c in sorted(delivered, key=lambda c: c.get("delivered_at") or "",
                                                                 reverse=True)],
        "cancelled": len(cancelled),
        "livreurs": [{
            **_who(lv), "on_duty": bool(lv.get("on_duty")), "soon_free": bool(lv.get("soon_free")),
            "forced": bool(lv.get("duty_forced")), "courses": busy.get(lv["id"], 0),
            "transport": transport.ICON.get(lv.get("transport_mode") or "", ""),
            "position_min": (minutes_since(parse_ts(positions[lv["id"]]["updated_at"]))
                             if lv["id"] in positions else None),
            "stock_to_confirm": lv["id"] in blocked,
        } for lv in livreurs],
        "totals": {"n": len(delivered), "total": sum(float(c["price"]) for c in delivered), "pay": pay,
                   "per": sorted(per.values(), key=lambda r: (-r["n"], -r["total"], r["name"]))},
        "routes": _routes(assigned, livreurs, positions, users, viewer),
        "chapters": _chapters(viewer),
    }


def _routes(assigned: list[dict], livreurs: list[dict], positions: dict, users: dict, viewer: dict) -> list[dict]:
    """Un livreur ne fait qu'une adresse à la fois : la plus ancienne qu'il a prise (comme pour « OK ») ; les
    autres sont « à suivre ». Arrivée : l'heure qu'il a écrite, sinon une estimation depuis sa position."""
    from bot.services.distance import haversine_m

    now = now_utc()
    mine: dict[str, list[dict]] = {}
    for c in assigned:
        mine.setdefault(c["livreur_id"], []).append(c)
    out = []
    for lv_id, courses in mine.items():
        courses.sort(key=lambda c: (c.get("assigned_at") or "", c["id"]))
        lv = users.get(lv_id) or {}
        current = courses[0]
        eta = parse_ts(current.get("eta_at")) if current.get("eta_at") else None
        arrival = {"at": hhmm(eta), "in": max(0, int((eta - now).total_seconds() // 60)), "source": "livreur"} if eta else None
        pos = positions.get(lv_id)
        dist = None
        if pos and current.get("lat") is not None:
            dist = haversine_m(pos["lat"], pos["lon"], current["lat"], current["lon"])
            if arrival is None:
                minutes = int(round(transport.travel_minutes(dist, lv.get("transport_mode"))))
                arrival = {"at": hhmm(now + timedelta(minutes=minutes)), "in": minutes, "source": "estimation"}
        out.append({"livreur": _who(lv), "transport": transport.ICON.get(lv.get("transport_mode") or "", ""),
                    "current": _course(current, users, viewer), "arrival": arrival,
                    "distance_km": round(dist / 1000, 1) if dist is not None else None,
                    "queue": [_course(c, users, viewer) for c in courses[1:]]})
    out.sort(key=lambda r: (r["arrival"] or {}).get("in", 999))
    return out


def _chapters(viewer: dict) -> list[dict]:
    from bot.handlers import menu

    out = []
    for key, (title, items) in menu.CHAPTERS.items():
        cmds = [{"cmd": c, "label": label, "about": about} for c, label, about in items
                if not c.startswith("@") and (common.is_dispatch(viewer) or c not in menu.DISPATCH_ONLY)]
        if cmds:
            out.append({"key": key, "title": title, "items": cmds})
    return out


# ---------------------------------------------------------------- actions

async def _act(request: web.Request, admin: dict, body: dict) -> dict:
    from bot.handlers import address, course_time, dispatch

    context = _context(request)
    kind = body.get("type")
    course = await db.get_course(int(body["course_id"])) if body.get("course_id") else None

    if kind in ("cancel", "deliver", "release"):
        if course is None:
            return {"ok": False, "message": texts.ALREADY_CLOSED}
        done = await dispatch.apply_override(context, admin, kind, course["id"], body.get("payment"))
        return {"ok": done is not None,
                "message": texts.d_override(kind, course["id"]) if done else "La course a changé entre-temps."}
    if kind == "assign":
        ok, text = await dispatch.assign_course(context, admin, int(body["course_id"]), str(body.get("livreur_id")))
        return {"ok": ok, "message": text or texts.ALREADY_HANDLED}
    if kind == "time":
        if not address._can_edit(admin, course):
            return {"ok": False, "message": texts.COURSE_FINISHED}
        value = " ".join(str(body.get("value") or "").split())[:course_time.MAX_LEN] or None
        if value and value.lower() in course_time.CLEAR:
            value = None
        course = await course_time.apply(context, admin, course, value)
        return {"ok": True, "message": texts.course_time_done(course)}
    if kind in ("address_preview", "address_apply"):
        if not address._can_edit(admin, course):
            return {"ok": False, "message": texts.COURSE_FINISHED}
        new, error = await address.lookup(str(body.get("text") or ""))
        if new is None:
            return {"ok": False, "message": error or "Écris l'adresse complète, avec le code postal."}
        if kind == "address_preview":
            return {"ok": True, "preview": new["address"], "detail": new.get("address_detail")}
        course = await address.apply(context, admin, course, new)
        return {"ok": True, "message": f"📍 Nouvelle adresse : {course['address']}"}
    if kind == "duty":
        livreur = await db.get_user(str(body.get("livreur_id")))
        if not livreur or livreur["role"] != "livreur" or livreur["status"] != "active":
            return {"ok": False, "message": texts.ALREADY_HANDLED}
        return {"ok": True, "message": await dispatch.set_duty(context, admin, livreur, bool(body.get("on")))}
    if kind == "position":
        livreur = await db.get_user(str(body.get("livreur_id")))
        pos = (await db.get_positions([livreur["id"]])).get(livreur["id"]) if livreur else None
        if not pos:
            return {"ok": False, "message": "Pas de position connue pour ce livreur."}
        await dispatch._send_position(context, admin["telegram_id"], livreur, pos)
        return {"ok": True, "message": "Position envoyée dans le chat.", "close": True}
    if kind == "open_chat":
        if course is None or course["status"] not in ("pending", "assigned"):
            return {"ok": False, "message": texts.COURSE_FINISHED}
        franchise = await db.get_user(course["franchise_id"]) or {}
        livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
        await messaging.send(context.bot, admin, texts.course_card(course, franchise, livreur),
                             keyboards.course_card(course, owner=course["franchise_id"] == admin["id"]))
        return {"ok": True, "message": "Fiche envoyée dans le chat.", "close": True}
    if kind == "run":
        return await _run_command(context, admin, str(body.get("cmd") or ""))
    if kind == "notify_level":
        level = "tout" if body.get("value") == "tout" else "essentiel"
        await db.update_user(admin["id"], {"notify_level": level})
        return {"ok": True, "message": "Tous les avis en messages" if level == "tout"
                else "Seulement l'essentiel en messages ; le reste est dans le fil"}
    return {"ok": False, "message": "Action inconnue."}


async def _run_command(context, admin: dict, cmd: str) -> dict:
    """Bouton d'une commande du chat (/stock, /ravi…) : le bot répond dans le chat, comme si elle était tapée."""
    from telegram import Chat, Message, Update, User

    from bot.handlers import menu

    handler = menu._runners().get(cmd)
    if handler is None:
        return {"ok": False, "message": "Commande inconnue."}
    if cmd in menu.DISPATCH_ONLY and not common.is_dispatch(admin):
        return {"ok": False, "message": texts.DISPATCH_ONLY}
    tg_id = admin["telegram_id"]
    msg = Message(message_id=0, date=datetime.now(), chat=Chat(id=tg_id, type="private"),
                  from_user=User(id=tg_id, first_name=admin.get("display_name") or "Admin", is_bot=False,
                                 username=admin.get("telegram_username")),
                  text=f"/{cmd}")
    msg.set_bot(context.bot)
    msg.chat.set_bot(context.bot)
    await handler(Update(update_id=0, message=msg), context)
    return {"ok": True, "message": "Réponse envoyée dans le chat.", "close": True}


# ---------------------------------------------------------------- serveur

async def _index(request: web.Request) -> web.Response:
    return web.Response(text=APP_HTML.read_text(encoding="utf-8"), content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    return body if isinstance(body, dict) else {}


async def _board(request: web.Request) -> web.Response:
    admin = await _admin(request)
    return web.json_response(await board(admin))


async def _feed(request: web.Request) -> web.Response:
    admin = await _admin(request)
    body = await _json(request)
    rows = await db.list_feed(80, team_only=not common.is_dispatch(admin), before_id=body.get("before"))
    return web.json_response({"items": [{"id": r["id"], "at": _hm(r["created_at"]), "text": names.decorate(r["text"]),
                                         "important": r["important"]} for r in rows]})


async def section(name: str, viewer: dict) -> dict:
    """Écrans du menu qui lisent les tableaux ou la base : stock, caisse, produits, équipe."""
    from bot.services import cash, stock

    if name == "stock":
        boxes, livreurs = await stock.boxes_now(), await stock.livreurs_now()
        if not boxes["ok"] and not livreurs["ok"]:
            return {"ok": False, "error": boxes.get("error")}
        clean = lambda values: {p: q for p, q in (values or {}).items() if float(q or 0)}  # noqa: E731
        return {"ok": True,
                "boxes": [{"name": b, "items": clean(v)} for b, v in (boxes.get("boxes") or {}).items()],
                "livreurs": [{"name": n, "prenom": names.prenom(n), "items": clean(v)}
                             for n, v in sorted((livreurs.get("livreurs") or {}).items())],
                "error": None if boxes["ok"] and livreurs["ok"] else (boxes.get("error") or livreurs.get("error"))}
    if name == "cash":
        data = await cash.livreurs_now()
        if not data["ok"]:
            return {"ok": False, "error": data["error"]}
        rows = [{"name": r["nom"], "prenom": names.prenom(r["nom"]),
                 **{k: round(float(r[k]), 2) for k in ("cash", "especes", "virement", "depenses", "recupere")}}
                for r in data["livreurs"].values()]
        return {"ok": True, "livreurs": rows, "total": round(sum(r["cash"] for r in rows), 2),
                "ravitailleur": data.get("ravitailleur")}
    if name == "products":
        return {"ok": True, "products": [{"name": p["name"], "variants": p.get("variants") or []}
                                         for p in await db.list_products()]}
    if name == "team":
        users = [u for u in await db.list_users() if u["status"] in ("active", "pending", "banned")]
        order = {"dispatch": 0, "franchise": 1, "livreur": 2, "ravitailleur": 3}
        return {"ok": True, "users": [{
            **_who(u), "role": texts.ROLE_LABEL.get(u["role"], u["role"]), "status": u["status"],
            "on_duty": bool(u.get("on_duty")), "username": u.get("telegram_username"),
            "real_name": u.get("real_name")} for u in sorted(users, key=lambda u: (order.get(u["role"], 9),
                                                                                  u.get("display_name") or ""))]}
    return {"ok": False, "error": "Écran inconnu."}


async def _section(request: web.Request) -> web.Response:
    admin = await _admin(request)
    body = await _json(request)
    try:
        return web.json_response(await section(str(body.get("name")), admin))
    except Exception:  # noqa: BLE001
        log.exception("Écran %s impossible", body.get("name"))
        return web.json_response({"ok": False, "error": "Lecture impossible pour l'instant, réessaie."})


async def _action(request: web.Request) -> web.Response:
    admin = await _admin(request)
    body = await _json(request)
    try:
        result = await _act(request, admin, body)
    except Exception:  # noqa: BLE001
        log.exception("Action de l'appli impossible : %s", body.get("type"))
        result = {"ok": False, "message": texts.GENERIC_ERROR}
    return web.json_response(result)


def build(ptb_app) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app[PTB] = ptb_app
    app.router.add_get("/", _index)
    app.router.add_get("/health", _health)
    app.router.add_post("/api/board", _board)
    app.router.add_post("/api/feed", _feed)
    app.router.add_post("/api/section", _section)
    app.router.add_post("/api/action", _action)
    return app


async def start(ptb_app) -> None:
    """Démarre le serveur web à côté du polling (rien si PORT n'est pas défini)."""
    global _runner
    port = config.get().web_port
    if not port:
        return
    _runner = web.AppRunner(build(ptb_app))
    await _runner.setup()
    await web.TCPSite(_runner, "0.0.0.0", port).start()
    log.info("Mini-application servie sur le port %s (%s)", port, config.get().webapp_url or "sans adresse publique")


async def stop() -> None:
    global _runner
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
