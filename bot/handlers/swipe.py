"""/swipe (ravitailleurs et admins) : transférer des produits d'un livreur à un autre.

« /swipe » + les blocs dans le même message → aperçu ; « /swipe » seul → exemple, puis le bot attend
le texte (15 min). « tout » prend tout le stock du livreur d'après le tableau. ✅ : un rechargement
« swipe » par transfert (produits et/ou cash), écrit en une ligne dans le tableau Rechargement (colonne A celui qui donne,
box « Swipe », quantités positives, colonne T celui qui reçoit) ; les deux livreurs et le dispatch
prévenus, stock compté en attendant la feuille."""
from __future__ import annotations

from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common
from bot.handlers.restock import ROLES
from bot.services import sheets, stock
from bot.services import swipe as sw
from bot.services.restock import SWIPE_BOX
from bot.timeutil import now_utc

STATE_INPUT = "swipe_input"
STATE_CONFIRM = "swipe_confirm"
STATE_MINUTES = 15


def _allowed(user: dict | None) -> bool:
    return bool(user) and user["status"] == "active" and user["role"] in ROLES


async def _save(user: dict, state: str, payload: dict) -> None:
    await db.set_state(user["id"], state, payload, now_utc() + timedelta(minutes=STATE_MINUTES))


async def swipe(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not _allowed(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    first, _, body = (update.message.text or "").partition("\n")
    rest = " ".join(first.split()[1:])          # « /swipe Livreur A > Livreur B » sur la même ligne
    text = "\n".join(t for t in (rest, body) if t.strip())
    if text.strip():
        await _process(update, user, text)
        return
    await _save(user, STATE_INPUT, {})
    await messaging.reply(update, texts.SWIPE_HELP)


async def on_message(update: Update, context, user: dict) -> None:
    await _process(update, user, update.message.text or "")


async def _process(update: Update, user: dict, text: str) -> None:
    livreurs = await db.list_users(role="livreur", status="active")
    parsed = sw.parse(list(enumerate(text.splitlines(), 1)), await sheets.load_catalog(),
                      list(config.get().livreur_names), livreurs)
    errors = list(parsed.errors)
    warnings: list[str] = []
    if not errors and parsed.blocks:
        known = await stock.livreurs_now() if sheets.enabled() else {"ok": False, "error": texts.SHEETS_DISABLED}
        for b in parsed.blocks:
            have = sw.stock_of(b.src, known["livreurs"]) if known["ok"] else None
            if b.all:
                if have is None:
                    errors.append(f"{b.src} : stock illisible ({known.get('error') or 'livreur absent du tableau'}), "
                                  "écris les produits un par un")
                elif not sw.everything(have):
                    errors.append(f"{b.src} n'a plus rien en stock d'après le tableau")
                else:
                    b.items = sw.everything(have)
            elif have is not None:
                warnings += [texts.swipe_short(b.src, p, want, got) for p, want, got in sw.shortages(b.items, have)]
    if errors or not parsed.blocks:
        await _save(user, STATE_INPUT, {})
        await messaging.reply(update, texts.swipe_errors(errors) if errors else texts.SWIPE_HELP)
        return
    blocks = [{"src": b.src, "dst": b.dst, "src_id": b.src_user["id"] if b.src_user else None,
               "dst_id": b.dst_user["id"] if b.dst_user else None, "items": b.items, "all": b.all, "cash": b.cash}
              for b in parsed.blocks]
    sent = await messaging.reply(update, texts.swipe_preview(blocks, warnings), keyboards.swipe_confirm())
    await _save(user, STATE_CONFIRM, {"blocks": blocks, "msg": sent.message_id if sent else None})


@common.callback
async def action(update: Update, context):
    """sw_ok : enregistrer · sw_x : annuler."""
    user = await common.actor(update)
    if not _allowed(user):
        return None
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id
    state, payload = common.active_state(user)
    if state != STATE_CONFIRM or payload.get("msg") != msg_id:
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.SWIPE_EXPIRED, True
    await db.clear_state(user["id"])
    if update.callback_query.data == "sw_x":
        await messaging.edit(context.bot, chat_id, msg_id, texts.SWIPE_CANCELLED)
        return None
    for b in payload["blocks"]:
        row = await db.create_restock({"livreur_id": b["src_id"], "livreur_name": b["src"], "by_user_id": user["id"],
                                       "to_livreur_id": b["dst_id"], "to_livreur_name": b["dst"],
                                       "kind": "swipe", "box": SWIPE_BOX, "items": b["items"],
                                       "cash": float(b.get("cash") or 0)})
        stock.push_restock_later(row)
        await db.log_event("swipe", user_id=user["id"], payload={"restock_id": row["id"], "from": b["src"], "to": b["dst"]})
        for uid, text in ((b["src_id"], texts.swipe_for_giver(b)), (b["dst_id"], texts.swipe_for_receiver(b))):
            livreur = await db.get_user(uid) if uid else None
            if livreur:
                await messaging.send(context.bot, livreur, text)
    await messaging.edit(context.bot, chat_id, msg_id, texts.swipe_done(payload["blocks"]))
    if user["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, f"{texts.swipe_done(payload['blocks'], short=True)}"
                                                     f"\n\n(par {texts.esc(user['display_name'])})")
    return "Enregistré ✅"
