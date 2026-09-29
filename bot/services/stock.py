"""Alertes de stock du livreur, lues dans le tableau Rechargement (onglet SOLDES :
« stock actuel chez le livreur = chargé − repris − vendu OK »).

- À l'attribution d'une course : si elle prend les derniers exemplaires d'un produit
  (« ce sont les 2 derniers US ») ou plus qu'il n'en a, alerte.
- À la livraison : stock lu AVANT d'envoyer la vente à la feuille, moins les quantités
  livrées ; s'il ne reste rien d'un produit (« n'a plus de US sur lui »), alerte.

Prévenus : le franchisé de la course, le dispatch, le livreur et les ravitailleurs actifs.

Le stock est calculé « en parallèle » des feuilles, pour ne jamais dépendre d'un retard :
- le script le calcule en direct (chargé net − ventes OK lues dans la feuille Dispatch) ;
- le bot y ajoute ses propres mouvements pas encore écrits dans les feuilles (livraisons et
  rechargements en cours d'envoi, ou dont l'envoi a échoué : « en vol ») ;
- à l'attribution, il retire aussi les autres courses déjà attribuées au livreur, pas encore livrées.
Sans réponse du script, pas d'alerte.
"""
from __future__ import annotations

import asyncio
import logging
import time

from bot import config, db, messaging, texts
from bot.services import sheets

log = logging.getLogger(__name__)

INFLIGHT_TTL = 2 * 3600   # au-delà, un mouvement jamais confirmé est oublié (/synchro l'aura renvoyé)
_inflight: dict[str, dict[str, tuple[float, dict[str, int]]]] = {}   # livreur -> jeton -> (expire, deltas)


def add_inflight(livreur_id: str, token: str, deltas: dict[str, int]) -> None:
    """Mouvement connu du bot mais pas encore dans les feuilles (+ chargé, − vendu / repris)."""
    if livreur_id and deltas:
        _inflight.setdefault(livreur_id, {})[token] = (time.monotonic() + INFLIGHT_TTL, dict(deltas))


def remove_inflight(livreur_id: str, token: str) -> None:
    global _sheet_cache
    _inflight.get(livreur_id, {}).pop(token, None)
    _sheet_cache = None   # le mouvement est maintenant dans la feuille : relire la feuille


def inflight_deltas(livreur_id: str) -> dict[str, int]:
    now = time.monotonic()
    entries = _inflight.get(livreur_id, {})
    for token in [t for t, (exp, _) in entries.items() if exp < now]:
        entries.pop(token, None)
    out: dict[str, int] = {}
    for _, deltas in entries.values():
        for p, d in deltas.items():
            out[p] = out.get(p, 0) + d
    return out


def apply_deltas(stock: dict[str, float], deltas: dict[str, int]) -> dict[str, float]:
    """Stock de la feuille + mouvements (noms comparés sans casse)."""
    out = dict(stock)
    for product, delta in deltas.items():
        key = next((k for k in out if _key(k) == _key(product)), product)
        out[key] = float(out.get(key, 0) or 0) + delta
    return out


async def _reserved(livreur_id: str, except_course: int, catalog) -> dict[str, int]:
    """Quantités des autres courses du livreur, attribuées mais pas encore livrées."""
    out: dict[str, int] = {}
    for c in await db.list_assigned_for_livreur(livreur_id):
        if c["id"] == except_course:
            continue
        for p, q in course_quantities(c, catalog).items():
            out[p] = out.get(p, 0) - q
    return out


async def current_stock(livreur: dict) -> tuple[dict | None, str]:
    """Stock réel du livreur maintenant : feuille (calcul direct) + mouvements en vol."""
    stock, sheet_name = await sheets.fetch_stock(livreur)
    if stock is None:
        return None, sheet_name
    return apply_deltas(stock, inflight_deltas(livreur["id"])), sheet_name


def _key(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def course_quantities(course: dict, catalog=None) -> dict[str, int]:
    """{produit: quantité} d'une course (noms du catalogue quand ils sont reconnus)."""
    out: dict[str, int] = {}
    for line in sheets.product_lines(course.get("products") or "", float(course.get("price") or 0), catalog):
        if line["produit"] and isinstance(line["qte"], int):
            out[line["produit"]] = out.get(line["produit"], 0) + line["qte"]
    return out


def _have(stock: dict[str, float], product: str) -> float:
    wanted = _key(product)
    return next((float(v or 0) for k, v in stock.items() if _key(k) == wanted), 0.0)


def assignment_warnings(quantities: dict[str, int], stock: dict[str, float]) -> list[tuple[str, int, float]]:
    """(produit, commandé, en stock) quand la course prend tout ce qui reste, ou plus."""
    out = []
    for product, need in quantities.items():
        have = _have(stock, product)
        if need >= have:
            out.append((product, need, have))
    return out


def emptied_after_delivery(quantities: dict[str, int], stock_before: dict[str, float]) -> list[str]:
    """Produits dont il ne reste plus rien une fois la course livrée."""
    return [p for p, q in quantities.items() if _have(stock_before, p) - q <= 0]


def enabled() -> bool:
    return config.get().stock_alerts and sheets.enabled()


async def _recipients(course: dict, livreur: dict) -> list[dict]:
    users = [u for u in [await db.get_user(course["franchise_id"]), livreur] if u]
    users += await db.list_users(role="ravitailleur", status="active")
    seen, out = set(), []
    for u in users:
        if u["id"] not in seen and u["role"] != "dispatch":
            seen.add(u["id"])
            out.append(u)
    return out


async def _broadcast(context, course: dict, livreur: dict, text: str) -> None:
    for user in await _recipients(course, livreur):
        await messaging.send(context.bot, user, text)
    await messaging.notify_dispatch(context.bot, text)


async def check_assignment(context, course: dict, livreur: dict) -> None:
    """Ne lève jamais d'exception."""
    try:
        stock, sheet_name = await current_stock(livreur)
        if stock is None:
            return
        catalog = await sheets.load_catalog()
        quantities = course_quantities(course, catalog)
        # Disponible pour cette course = stock − ce que ses autres courses en cours vont prendre.
        stock = apply_deltas(stock, await _reserved(livreur["id"], course["id"], catalog))
        warnings = assignment_warnings(quantities, stock)
        if warnings:
            await db.log_event("stock_warning", course["id"], livreur["id"],
                               {"stock": {p: h for p, _, h in warnings}})
            await _broadcast(context, course, livreur,
                             texts.stock_assignment_alert(course, livreur, sheet_name, warnings))
    except Exception:  # noqa: BLE001
        log.exception("Alerte de stock (attribution) impossible")


async def deliver_then_push(context, course: dict, livreur: dict | None, alerts: bool = True) -> None:
    """Stock lu avant l'envoi de la vente, alerte « plus de … sur lui », puis envoi à la feuille.
    Tant que la vente n'est pas confirmée dans la feuille, le bot la compte lui-même (en vol) :
    produits sortis du stock du livreur, espèces encaissées."""
    from bot.services import cash

    quantities: dict[str, int] = {}
    token = f"course:{course['id']}"
    livreur_id = livreur["id"] if livreur is not None else None
    cash.track(livreur_id, token, cash.delivery_amount(course))
    try:
        quantities = course_quantities(course, await sheets.load_catalog())
        if livreur is not None and alerts:
            stock, sheet_name = await current_stock(livreur)
            if stock is not None:
                empty = emptied_after_delivery(quantities, stock)
                if empty:
                    await db.log_event("stock_empty", course["id"], livreur["id"], {"products": empty})
                    await _broadcast(context, course, livreur,
                                     texts.stock_empty_alert(course, livreur, sheet_name, empty))
    except Exception:  # noqa: BLE001
        log.exception("Alerte de stock (livraison) impossible")
    if livreur is not None:
        add_inflight(livreur["id"], token, {p: -q for p, q in quantities.items()})
    if await sheets.push_delivered(course) and livreur is not None:
        remove_inflight(livreur["id"], token)
        cash.done(livreur_id, token)


def _later(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    sheets._tasks.add(task)
    task.add_done_callback(sheets._tasks.discard)


def after_assignment_later(context, course: dict, livreur: dict) -> None:
    if enabled():
        _later(check_assignment(context, dict(course), livreur))


async def push_restock_tracked(restock: dict) -> None:
    """Envoi d'un rechargement, compté par le bot tant qu'il n'est pas dans la feuille."""
    from bot.services import cash
    from bot.services import restock as rs

    token = f"restock:{restock['id']}"
    deltas = rs.signed_quantities(restock.get("items") or [], restock["kind"])
    add_inflight(restock["livreur_id"], token, deltas)
    cash.track(restock["livreur_id"], token, -float(restock.get("cash") or 0))
    if restock.get("box"):
        # Le box perd ce qui est chargé au livreur, regagne ce qui est repris.
        add_inflight(box_key(restock["box"]), token, {p: -d for p, d in deltas.items()})
    if await sheets.push_restock(restock):
        remove_inflight(restock["livreur_id"], token)
        cash.done(restock["livreur_id"], token)
        if restock.get("box"):
            remove_inflight(box_key(restock["box"]), token)


def box_key(box: str) -> str:
    return f"box:{_key(box)}"


async def boxes_now() -> dict:
    """Stock des box (ORGA ④) + rechargements pas encore écrits ; totaux par produit (ORGA ⑤).
    {"ok": bool, "boxes": {box: {produit: qté}}, "totals": [...], "error": str}"""
    data = await sheets.fetch_action("stock_box")
    if not data or not data.get("ok"):
        return {"ok": False, "error": (data or {}).get("error") or "Google Sheets non relié"}
    boxes = {name: apply_deltas(values, inflight_deltas(box_key(name)))
             for name, values in (data.get("boxes") or {}).items()}
    return {"ok": True, "boxes": boxes, "totals": data.get("totals") or []}


async def livreurs_now() -> dict:
    """Stock de chaque livreur (calcul direct du script) + mouvements du bot pas encore écrits."""
    data = await sheets.fetch_action("stock_livreurs")
    if not data or not data.get("ok"):
        return {"ok": False, "error": (data or {}).get("error") or "Google Sheets non relié"}
    by_name = {_key(u["display_name"]): u for u in await db.list_users(role="livreur") if u.get("display_name")}
    out = {}
    for name, values in (data.get("livreurs") or {}).items():
        user = by_name.get(_key(name))
        out[name] = apply_deltas(values, inflight_deltas(user["id"])) if user else values
    return {"ok": True, "livreurs": out}


# ---------------------------------------------------------------- dispatch selon le stock

SHEET_CACHE_SECONDS = 60
RANK_TIMEOUT = 8.0   # la diffusion d'une course n'attend jamais la feuille plus longtemps
_sheet_cache: tuple[float, dict] | None = None   # (expire, {nom feuille: {produit: qté}})
_dispatch_alerted: set[int] = set()


async def _sheet_livreurs() -> dict | None:
    """Stock de chaque livreur d'après le script, gardé une minute (une vague par course et par
    minute ne relit pas la feuille à chaque fois). None si illisible."""
    global _sheet_cache
    now = time.monotonic()
    if _sheet_cache and _sheet_cache[0] > now:
        return _sheet_cache[1]
    data = await sheets.fetch_action("stock_livreurs", timeout=RANK_TIMEOUT)
    if not data or not data.get("ok"):
        return None
    _sheet_cache = (now + SHEET_CACHE_SECONDS, data.get("livreurs") or {})
    return _sheet_cache[1]


async def availability(course: dict, livreurs: list[dict]) -> dict[str, list[tuple[str, int, float]] | None]:
    """Pour chaque livreur : [] s'il a tout ce que demande la course, [(produit, commandé, dispo)]
    s'il lui manque quelque chose, None si son stock est inconnu. {} si rien n'est lisible.
    Dispo = feuille + mouvements en vol − ses autres courses attribuées pas encore livrées.
    Les produits absents des colonnes de la feuille (coca…) sont ignorés."""
    sheet = await _sheet_livreurs()
    if not sheet:
        return {}
    catalog = await sheets.load_catalog()
    known = {_key(p) for values in sheet.values() for p in (values or {})}
    need = {p: q for p, q in course_quantities(course, catalog).items() if _key(p) in known}
    if not need:
        return {}
    by_name = {_key(name): values or {} for name, values in sheet.items()}
    reserved: dict[str, dict[str, int]] = {}
    for c in await db.list_assigned_for_livreurs([lv["id"] for lv in livreurs]):
        if c["id"] == course["id"]:
            continue
        mine = reserved.setdefault(c["livreur_id"], {})
        for p, q in course_quantities(c, catalog).items():
            mine[p] = mine.get(p, 0) - q
    out: dict[str, list[tuple[str, int, float]] | None] = {}
    for lv in livreurs:
        values = by_name.get(_key(lv.get("display_name") or ""))
        if values is None:
            out[lv["id"]] = None
            continue
        have = apply_deltas(apply_deltas(values, inflight_deltas(lv["id"])), reserved.get(lv["id"], {}))
        out[lv["id"]] = [(p, q, _have(have, p)) for p, q in need.items() if _have(have, p) < q]
    return out


async def rank_by_stock(context, course: dict, eligible: list[tuple[dict, float | None]]):
    """Livreurs éligibles réordonnés : d'abord ceux qui ont tout en stock, puis stock inconnu,
    puis ceux à qui il manque quelque chose (l'ordre par distance est gardé dans chaque groupe).
    Si aucun n'a tout, ravitailleurs et dispatch sont prévenus une fois pour la course.
    Ne lève jamais d'exception : en cas de problème, l'ordre reste celui des distances."""
    if not enabled() or not eligible:
        return eligible
    try:
        avail = await availability(course, [lv for lv, _ in eligible])
        if not avail:
            return eligible

        def group(item):
            missing = avail.get(item[0]["id"])
            return 1 if missing is None else (0 if not missing else 2)

        ranked = sorted(eligible, key=group)   # tri stable : la distance départage
        known = [m for m in avail.values() if m is not None]
        if known and all(known) and course["id"] not in _dispatch_alerted:
            _dispatch_alerted.add(course["id"])
            await _alert_no_stock(context, course, eligible, avail)
        return ranked
    except Exception:  # noqa: BLE001
        log.exception("Dispatch selon le stock impossible")
        return eligible


async def _alert_no_stock(context, course: dict, eligible, avail) -> None:
    short = [(lv, avail[lv["id"]]) for lv, _ in eligible if avail.get(lv["id"])]
    text = texts.stock_dispatch_alert(course, short)
    await db.log_event("stock_dispatch_alert", course["id"],
                       payload={"livreurs": [lv["id"] for lv, _ in short]})
    for user in await db.list_users(role="ravitailleur", status="active"):
        await messaging.send(context.bot, user, text)
    await messaging.notify_dispatch(context.bot, text)


def push_restock_later(restock: dict) -> None:
    if sheets.enabled():
        _later(push_restock_tracked(dict(restock)))


def after_delivery_later(context, course: dict, livreur: dict | None) -> None:
    """Remplace sheets.push_delivered_later : même envoi, précédé de l'alerte de stock (si activée)
    et compté par le bot (stock, espèces) tant qu'il n'est pas confirmé dans la feuille."""
    if sheets.enabled():
        _later(deliver_then_push(context, dict(course), livreur, alerts=enabled()))
