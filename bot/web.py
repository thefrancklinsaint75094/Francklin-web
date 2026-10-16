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
        return {"ok": True, "map": f"https://www.google.com/maps/search/?api=1&query={pos['lat']},{pos['lon']}",
                "message": f"Position il y a {minutes_since(parse_ts(pos['updated_at']))} min"}
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
    if kind in ORDER_ACTIONS:
        return await _order(context, admin, kind, body, course)
    if kind in ("edit_lines", "edit_apply"):
        return await _edit(context, admin, kind, body, course)
    if kind in TEXT_FORMS:
        return await _text_form(context, admin, kind, body)
    if kind == "expense":
        return await _expense(context, admin, body)
    if kind in USER_ACTIONS:
        return await _user(context, admin, kind, body)
    if kind in ("debrief", "close_livreur", "synchro", "recap"):
        cmd = {"debrief": "close", "close_livreur": "close", "synchro": "synchro", "recap": "recap"}[kind]
        out = await _capture(context, admin, cmd, str(body.get("arg") or "") if kind != "debrief" else "")
        return {"ok": bool(out), "html": "\n\n".join(out) or texts.GENERIC_ERROR}
    if kind in ("reset_check", "reset_run"):
        from bot.handlers import cloture

        if not common.is_dispatch(admin):
            return {"ok": False, "message": texts.DISPATCH_ONLY}
        ok, text = await (cloture.precheck() if kind == "reset_check" else cloture.run(admin))
        return {"ok": ok, "html": text}
    if kind == "message":
        target = await db.get_user(str(body.get("user_id")))
        text = " ".join(str(body.get("text") or "").split())[:1000]
        if not target or target["status"] != "active" or not text:
            return {"ok": False, "message": "Message vide ou destinataire inconnu."}
        await messaging.send(context.bot, target, texts.admin_message(admin, text))
        await db.log_event("admin_message", user_id=admin["id"], payload={"to": target["id"]})
        return {"ok": True, "message": f"Message envoyé à {target.get('display_name')}."}
    if kind == "notify_level":
        level = "tout" if body.get("value") == "tout" else "essentiel"
        await db.update_user(admin["id"], {"notify_level": level})
        return {"ok": True, "message": "Tous les avis en messages" if level == "tout"
                else "Seulement l'essentiel en messages ; le reste est dans le fil"}
    return {"ok": False, "message": "Action inconnue."}


ORDER_ACTIONS = ("order_analyse", "order_send", "order_drop", "order_pick")
TEXT_FORMS = ("ravi_prepare", "ravi_commit", "swipe_prepare", "swipe_commit", "sales_prepare", "sales_commit")
USER_ACTIONS = ("user_rename", "user_prenom", "user_role", "user_sheet_name", "user_ban", "user_unban",
                "user_delete", "user_approve", "user_reject")


async def _order(context, admin: dict, kind: str, body: dict, course: dict | None) -> dict:
    """Nouvelle commande : analyse (fiches à confirmer), envoi, choix du livreur — comme dans le chat."""
    from bot.handlers import franchise
    from bot.services import broadcast

    if not common.can_order(admin):
        return {"ok": False, "message": texts.NOT_FOR_YOU}
    if kind == "order_analyse":
        raw = str(body.get("text") or "").strip()[:4000]
        if len(raw) < 8:
            return {"ok": False, "message": "Écris l'adresse puis les produits (voir le modèle)."}
        problem, results = await franchise.analyse(admin, raw)
        if problem == "unavailable":
            return {"ok": False, "message": texts.GENERIC_ERROR}
        if problem == "parse":
            return {"ok": False, "message": texts.EXTRACTION_PARSE_FAIL}
        if problem and problem.startswith("complement:"):
            return {"ok": False, "message": texts.complement_hint(int(problem.split(":")[1]))}
        if problem == "none":
            return {"ok": False, "message": texts.NO_ORDER_FOUND}
        cards = []
        for item_kind, item in results:
            if item_kind != "order":
                cards.append({"ok": False, "html": item})
                continue
            draft = await db.create_draft(admin["id"], raw)
            draft = await db.update_draft(draft["id"], {"extracted": item}) or draft
            cards.append({"ok": True, "draft_id": draft["id"],
                          "html": texts.draft_card(item, config.get().price_max)})
        return {"ok": True, "cards": cards}
    if kind == "order_drop":
        await db.update_draft_if_status(str(body.get("draft_id")), ["awaiting"], {"status": "expired"})
        return {"ok": True}
    if kind == "order_send":
        draft = await db.get_draft(str(body.get("draft_id")))
        if draft is None or draft["franchise_id"] != admin["id"]:
            return {"ok": False, "message": texts.DRAFT_NOT_ACTIVE}
        locked = await db.update_draft_if_status(draft["id"], ["awaiting"], {"status": "confirmed"})
        if locked is None:
            return {"ok": False, "message": texts.DRAFT_NOT_ACTIVE}
        created, opts = await franchise.create_course(context, admin, locked, None)
        return {"ok": True, "message": f"Course #{created['id']} enregistrée", "course_id": created["id"],
                "options": [{**_who(o["user"]), "m": o.get("m"), "busy": o.get("busy", 0),
                             "v": [[v, ok] for v, ok in (o.get("v") or [])]} for o in opts]}
    if kind == "order_pick":
        if course is None or course["status"] != "pending":
            return {"ok": False, "message": texts.ALREADY_CLOSED}
        if course["franchise_id"] != admin["id"] and not common.is_admin(admin):
            return {"ok": False, "message": texts.NOT_FOR_YOU}
        if body.get("auto"):
            pick.forget(context.job_queue, course["id"])
            await lifecycle.refresh_franchise_message(context, course)
            await broadcast.run_wave(context, course["id"], advance=False)
            return {"ok": True, "message": "Envoyée au plus proche"}
        livreur = await db.get_user(str(body.get("livreur_id")))
        if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
            return {"ok": False, "message": texts.ALREADY_HANDLED}
        pick.forget(context.job_queue, course["id"])
        result = await franchise.send_to_livreur(context, admin, course, livreur)
        return {"ok": result is not None, "message": result or texts.ALREADY_CLOSED}
    return {"ok": False, "message": "Action inconnue."}


async def _edit(context, admin: dict, kind: str, body: dict, course: dict | None) -> dict:
    """Produits et prix d'une course (en attente ou en cours), lignes « quantité · produit · prix »."""
    from bot.handlers import livreur as livreur_h
    from bot.services import catalog, order_edit

    if course is None or course["status"] not in ("pending", "assigned"):
        return {"ok": False, "message": texts.COURSE_FINISHED}
    if kind == "edit_lines":
        return {"ok": True, "lines": order_edit.lines_from_course(course, await catalog.load())}
    lines = []
    for line in (body.get("lines") or [])[:order_edit.MAX_LINES]:
        try:
            name, qty, price = str(line["p"]).strip(), int(line["q"]), round(float(line["x"]), 2)
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "message": "Ligne illisible."}
        if name and qty > 0:
            lines.append({"p": name, "q": min(qty, order_edit.MAX_QTY), "x": max(price, 0.0)})
    if not lines:
        return {"ok": False, "message": texts.ORDER_EDIT_EMPTY}
    if order_edit.missing_prices(lines):
        return {"ok": False, "message": texts.order_edit_missing_price(order_edit.missing_prices(lines))}
    if order_edit.off_step_prices(lines):
        return {"ok": False, "message": texts.order_edit_off_step(order_edit.off_step_prices(lines))}
    fields = {"products": order_edit.products_text(lines), "price": order_edit.total(lines)}
    updated = await db.update_course_if_status(course["id"], ["pending", "assigned"], fields)
    if updated is None:
        return {"ok": False, "message": texts.COURSE_FINISHED}
    await db.log_event("course_modified", course["id"], admin["id"], {
        "before": {"products": course["products"], "price": float(course["price"])},
        "after": {"products": updated["products"], "price": float(updated["price"])}})
    await livreur_h._notify_modified(context, admin, course, updated)
    await lifecycle.refresh_franchise_message(context, updated)
    return {"ok": True, "message": f"#{course['id']} modifiée : {updated['products']} — {texts.eur(updated['price'])}"}


async def _text_form(context, admin: dict, kind: str, body: dict) -> dict:
    """Rechargement (/ravi), swipe et récap de ventes : même texte et même lecture que dans le chat ;
    *_prepare montre le récap, *_commit relit le texte et enregistre."""
    from bot.handlers import restock_text, sales, swipe
    from bot.services import restock_text as ravi_text

    text = str(body.get("text") or "")[:6000]
    what, step = kind.split("_")
    if what in ("ravi", "swipe") and admin["role"] not in ("dispatch", "franchise", "ravitailleur"):
        return {"ok": False, "message": texts.NOT_FOR_YOU}
    if what == "ravi":
        ravi = str(body.get("ravi") or "")
        ravis = await restock_text._ravitailleurs()
        if ravi not in ravis:
            return {"ok": False, "message": "Choisis le ravitailleur."}
        errors, blocks, warnings = await restock_text.prepare(text)
        if errors or not blocks:
            return {"ok": False, "html": texts.ravi_errors(errors) if errors else "Écris au moins un produit ou du cash."}
        if ravi_text.missing(blocks):
            return {"ok": False, "message": "Choisis le livreur et le box."}
        if step == "prepare":
            return {"ok": True, "html": texts.ravi_preview(ravi, blocks, warnings)}
        users = await db.list_users(role="ravitailleur", status="active")
        _, ravi_user = ravi_text.resolve_ravitailleur(ravi, [ravi], users)
        done = await restock_text.commit(context, admin, {"ravi": ravi, "ravi_id": ravi_user["id"] if ravi_user else None,
                                                          "blocks": blocks})
        return {"ok": True, "html": done}
    if what == "swipe":
        errors, blocks, warnings = await swipe.prepare(text)
        if errors or not blocks:
            return {"ok": False, "html": texts.swipe_errors(errors) if errors else texts.SWIPE_HELP}
        if step == "prepare":
            return {"ok": True, "html": texts.swipe_preview(blocks, warnings)}
        return {"ok": True, "html": await swipe.commit(context, admin, blocks)}
    if not common.is_admin(admin):
        return {"ok": False, "message": texts.NOT_FOR_YOU}
    errors, ready = await sales.prepare(text, body.get("night") or None)
    if errors or ready is None:
        return {"ok": False, "html": texts.sales_errors(errors) if errors else texts.SALES_EMPTY}
    if step == "prepare":
        return {"ok": True, "html": texts.sales_preview(ready["blocks"], ready["tab"])}
    done, ok = await sales.commit(context, admin, ready)
    return {"ok": ok, "html": done}


async def _expense(context, admin: dict, body: dict) -> dict:
    from bot.handlers import cash as cash_h
    from bot.services.sales import resolve_livreur

    name, livreur = resolve_livreur(str(body.get("livreur") or ""), list(config.get().livreur_names),
                                    await db.list_users(role="livreur", status="active"))
    try:
        amount = round(float(str(body.get("amount") or "0").replace(",", ".")), 2)
    except ValueError:
        amount = 0
    kind = "paye" if body.get("kind") == "paye" else "charges"
    if livreur is None:
        return {"ok": False, "message": "Ce livreur n'a pas de compte sur le bot."}
    if amount <= 0:
        return {"ok": False, "message": texts.EXPENSE_ZERO}
    row = await cash_h.save_expense(context, admin, livreur, kind, amount, str(body.get("motif") or "").strip()[:80])
    return {"ok": True, "html": texts.expense_done(row, livreur["display_name"])}


async def _user(context, admin: dict, kind: str, body: dict) -> dict:
    """Fiches : nom, prénom, nom de feuille, rôle, bannir / réactiver / supprimer, valider / refuser."""
    from bot.handlers import dispatch, onboarding, user_card

    if kind == "user_prenom":                          # prénom d'un nom de feuille (compte ou non)
        if not common.is_dispatch(admin):
            return {"ok": False, "message": texts.DISPATCH_ONLY}
        sheet = str(body.get("name") or "")
        ok = sheet and await user_card.set_prenom(admin, sheet, str(body.get("value") or ""))
        return {"ok": bool(ok), "message": "Prénom enregistré" if ok else texts.USER_NAME_INVALID}
    if not common.is_dispatch(admin):
        return {"ok": False, "message": texts.DISPATCH_ONLY}
    user = await db.get_user(str(body.get("user_id")))
    if user is None or user["role"] == "dispatch" or user["status"] == "deleted":
        return {"ok": False, "message": texts.ALREADY_HANDLED}
    if user["id"] == admin["id"]:
        return {"ok": False, "message": texts.USER_SELF}
    if kind == "user_approve" and user["status"] == "pending":
        user = await onboarding.approve_user(context, user)
        return {"ok": True, "message": texts.registration_approved(user)}
    if kind == "user_reject" and user["status"] == "pending":
        await onboarding.reject_user(context, user)
        return {"ok": True, "message": texts.registration_rejected(user)}
    if kind == "user_rename":
        updated = await user_card.rename(admin, user, str(body.get("value") or ""))
        return {"ok": updated is not None, "message": "Nom changé" if updated else texts.USER_NAME_INVALID}
    if kind == "user_sheet_name":
        _, error = await dispatch.set_sheet_name(context, admin, user, str(body.get("value") or ""))
        return {"ok": not error, "message": error or "Nom de feuille changé"}
    if kind == "user_role" and body.get("value") in user_card.ROLES and body.get("value") != user["role"]:
        user = await user_card._change_role(context, admin, user, body["value"])
        return {"ok": True, "message": f"{user['display_name']} : {texts.ROLE_LABEL[user['role']]}"}
    if kind == "user_ban" and user["status"] == "active":
        user = await dispatch.ban_user(context, admin, user)
        return {"ok": True, "message": texts.banned_done(user)}
    if kind == "user_unban" and user["status"] == "banned":
        user = await dispatch.unban_user(context, admin, user)
        return {"ok": True, "message": texts.unbanned_done(user)}
    if kind == "user_delete":
        user = await dispatch.delete_user(context, admin, user)
        return {"ok": True, "message": texts.deleted_done(user)}
    return {"ok": False, "message": texts.ALREADY_HANDLED}


def _fake_update(context, admin: dict, text: str, chat):
    from telegram import Message, Update, User

    msg = Message(message_id=0, date=datetime.now(), chat=chat,
                  from_user=User(id=admin["telegram_id"], first_name=admin.get("display_name") or "Admin", is_bot=False,
                                 username=admin.get("telegram_username")),
                  text=text)
    msg.set_bot(context.bot)
    chat.set_bot(context.bot)
    return Update(update_id=0, message=msg)


async def with_capture(context, user: dict, text: str, call) -> list[str]:
    """Appelle call(update) avec un faux message « text » de cette personne : les réponses du bot qui lui
    étaient destinées sont renvoyées (l'appli les affiche) au lieu de partir dans le chat. Les avis aux
    autres (livreurs, franchisés, dispatch) partent normalement."""
    from telegram import Chat

    sink: list[str] = []

    class CaptureChat(Chat):
        async def send_message(self, text, *args, **kwargs):  # noqa: ANN001 — même signature que Chat
            sink.append(text)
            return None

    await call(_fake_update(context, user, text, CaptureChat(id=user["telegram_id"], type="private")))
    return sink


async def _capture(context, admin: dict, cmd: str, arg: str = "") -> list[str]:
    """Commande du chat dont la réponse s'affiche dans l'appli (débrief, fin de service, synchro, récap)."""
    from bot.handlers import menu

    handler = menu._runners().get(cmd)
    if handler is None:
        return []
    return await with_capture(context, admin, f"/{cmd} {arg}".strip(), lambda update: handler(update, context))


async def _run_command(context, admin: dict, cmd: str) -> dict:
    """Bouton d'une commande du chat (/stock, /ravi…) : le bot répond dans le chat, comme si elle était tapée."""
    from telegram import Chat

    from bot.handlers import menu

    handler = menu._runners().get(cmd)
    if handler is None:
        return {"ok": False, "message": "Commande inconnue."}
    if cmd in menu.DISPATCH_ONLY and not common.is_dispatch(admin):
        return {"ok": False, "message": texts.DISPATCH_ONLY}
    await handler(_fake_update(context, admin, f"/{cmd}", Chat(id=admin["telegram_id"], type="private")), context)
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
        taken = {u.get("display_name") for u in users}
        sheet_only = [n for n in config.get().livreur_names if n not in taken]
        return {"ok": True, "manage": common.is_dispatch(viewer), "users": [{
            **_who(u), "role": u["role"], "role_label": texts.ROLE_LABEL.get(u["role"], u["role"]),
            "status": u["status"], "on_duty": bool(u.get("on_duty")), "username": u.get("telegram_username"),
            "real_name": u.get("real_name"), "me": u["id"] == viewer["id"],
            "sheet_names": list(names.names_for(u["role"]))} for u in sorted(
                users, key=lambda u: (u["status"] != "pending", order.get(u["role"], 9), u.get("display_name") or ""))],
            "sheet_only": [{"name": n, "prenom": names.prenom(n)} for n in sheet_only]}
    if name == "forms":
        from bot.handlers import restock_text

        return {"ok": True, "livreurs": await restock_text._livreur_names(),
                "ravitailleurs": await restock_text._ravitailleurs(), "boxes": list(config.get().boxes),
                "products": [p["name"] for p in await db.list_products()],
                "can_order": common.can_order(viewer), "dispatch": common.is_dispatch(viewer)}
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
    from bot import web_livreur

    app = web.Application(client_max_size=64 * 1024)
    app[PTB] = ptb_app
    app.router.add_get("/", _index)
    web_livreur.routes(app)
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
