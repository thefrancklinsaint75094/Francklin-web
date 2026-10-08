"""/ventes (admins) : un récap de ventes par livreur, vérifié puis ajouté à la feuille Dispatch.

/ventes (ou /vente) seul → exemple, puis le bot attend le récap (15 min). /ventes suivi du récap dans le
même message → directement l'aperçu. « /ventes lundi » (ou « hier ») : les ventes vont dans l'onglet de
cette nuit-là, pour un récap saisi en retard. « ✅ Ajouter au tableau » : ventes en base (table sales, avec
la nuit choisie), une ligne OK par vente dans l'onglet ; stock et caisse du livreur les comptent comme
des courses."""
from __future__ import annotations

from datetime import date, timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import cash, sales, sheets, stock
from bot.timeutil import night_label, night_start_date, now_utc

STATE_INPUT = "sales_input"
STATE_CONFIRM = "sales_confirm"
STATE_MINUTES = 15


async def _save(user: dict, state: str, payload: dict) -> None:
    await db.set_state(user["id"], state, payload, now_utc() + timedelta(minutes=STATE_MINUTES))


async def ventes(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not common.is_admin(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    first, _, body = (update.message.text or "").partition("\n")
    arg = " ".join(first.split()[1:])
    night = None
    if arg:
        night = sales.target_night(arg, _current_night())
        if night is None:
            await messaging.reply(update, texts.sales_bad_day(arg))
            return
    payload = {"night": night.isoformat()} if night else {}
    if body.strip():
        await _process(update, user, body, payload)
        return
    await _save(user, STATE_INPUT, payload)
    await messaging.reply(update, texts.sales_help(_tab_label(night)) if night else texts.SALES_HELP)


def _current_night() -> date:
    return night_start_date(now_utc(), config.get().night_end_hour)


def _tab_label(night: date | None) -> str:
    """« Lundi » (nuit en cours) ou « Lundi — nuit du 5 au 6 octobre » (nuit passée)."""
    night = night or _current_night()
    tab = sheets.JOURS[night.weekday()]
    return tab if night == _current_night() else f"{tab} — nuit du {night_label(night)}"


async def on_message(update: Update, context, user: dict, payload: dict | None = None) -> None:
    """Texte d'un admin qui a lancé /ventes : le récap (ou un récap corrigé), pour la nuit choisie."""
    await _process(update, user, update.message.text or "", {"night": (payload or {}).get("night")})


async def _process(update: Update, user: dict, text: str, payload: dict) -> None:
    keep = {"night": payload["night"]} if payload.get("night") else {}
    livreurs = await db.list_users(role="livreur", status="active")
    parsed = sales.parse(text, await sheets.load_catalog(), list(config.get().livreur_names), livreurs)
    if parsed.errors:
        await _save(user, STATE_INPUT, keep)
        await messaging.reply(update, texts.sales_errors(parsed.errors))
        return
    if not parsed.count:
        await _save(user, STATE_INPUT, keep)
        await messaging.reply(update, texts.SALES_EMPTY)
        return
    night = date.fromisoformat(keep["night"]) if keep else _current_night()
    tab = _tab_label(night)
    blocks = [{"livreur": b.livreur, "user_id": b.user["id"] if b.user else None, "lines": b.lines}
              for b in parsed.blocks]
    sent = await messaging.reply(update, texts.sales_preview(blocks, tab), keyboards.sales_confirm())
    await _save(user, STATE_CONFIRM, {"blocks": blocks, "tab": tab, "night": night.isoformat(),
                                      "msg": sent.message_id if sent else None})


@common.callback
async def action(update: Update, context):
    """sl_ok : ajouter au tableau · sl_x : annuler."""
    user = await common.actor(update)
    if not common.is_admin(user):
        return None
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    state, payload = common.active_state(user)
    if state != STATE_CONFIRM or payload.get("msg") != msg_id:
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.SALES_EXPIRED, True
    await db.clear_state(user["id"])
    if update.callback_query.data == "sl_x":
        await messaging.edit(context.bot, chat_id, msg_id, texts.SALES_CANCELLED)
        return None

    rows = await db.create_sales([
        {"livreur_name": b["livreur"], "livreur_id": b["user_id"], "by_user_id": user["id"], "night": payload.get("night"),
         "payment": line["pay"], "product": line["p"], "qty": line["q"], "price": line["x"]}
        for b in payload["blocks"] for line in b["lines"]
    ])
    await db.log_event("sales_entered", user_id=user["id"], payload={"sales": [r["id"] for r in rows]})
    # Comptées par le bot (stock, espèces) tant que la feuille ne les a pas.
    for r in rows:
        if r.get("livreur_id"):
            token = f"sale:{r['id']}"
            stock.add_inflight(r["livreur_id"], token, {r["product"]: -int(r["qty"])})
            cash.track(r["livreur_id"], token, float(r["price"]) if r["payment"] == "especes" else 0.0)
    added, error = 0, None
    if sheets.enabled():
        try:
            added = await sheets.send_rows([sheets.sale_row(r) for r in rows])
            for r in rows:
                if r.get("livreur_id"):
                    stock.remove_inflight(r["livreur_id"], f"sale:{r['id']}")
                    cash.done(r["livreur_id"], f"sale:{r['id']}")
        except RuntimeError as exc:
            error = str(exc)
    else:
        error = texts.SHEETS_DISABLED
    text = texts.sales_done(len(rows), payload["tab"], added, error)
    await messaging.edit(context.bot, chat_id, msg_id, text)
    if user["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, f"{texts.sales_preview(payload['blocks'], payload['tab'], done=True)}"
                                                     f"\n\n(par {texts.esc(user['display_name'])})")
    return "Ajouté ✅" if not error else None
