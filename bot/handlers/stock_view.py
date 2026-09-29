"""/stock : ce qui reste dans chaque box et chez les livreurs (admins et ravitailleurs).

Lu dans le tableau Rechargement (ORGA ④ et ⑤ pour les box, calcul direct pour les livreurs),
plus les mouvements du bot pas encore écrits dans les feuilles."""
from __future__ import annotations

from telegram import Update

from bot import config, keyboards, messaging, texts
from bot.handlers import common
from bot.services import stock

ROLES = ("dispatch", "franchise", "ravitailleur")


def _allowed(user: dict | None) -> bool:
    return bool(user) and user["status"] == "active" and user["role"] in ROLES


def _box_from_text(text: str, boxes: tuple[str, ...]) -> str | None:
    """« /stock box 1 », « /stock 1 » → « Box 1 »."""
    arg = " ".join(text.split()[1:]).lower()
    if not arg:
        return None
    for b in boxes:
        if arg == b.lower() or arg == b.lower().replace("box", "").strip():
            return b
    return None


async def _box_text(name: str) -> str:
    data = await stock.boxes_now()
    if not data["ok"]:
        return texts.stock_unavailable(data["error"])
    values = next((v for k, v in data["boxes"].items() if k.lower() == name.lower()), None)
    return texts.box_stock(name, values or {})


async def stock_cmd(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not _allowed(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    boxes = config.get().boxes
    wanted = _box_from_text(update.message.text or "", boxes)
    if wanted:
        await messaging.reply(update, await _box_text(wanted), keyboards.stock_menu(boxes))
        return
    await messaging.reply(update, texts.STOCK_MENU, keyboards.stock_menu(boxes))


@common.callback
async def show(update: Update, context):
    """sv:box:<n> · sv:all · sv:liv"""
    user = await common.actor(update)
    if not _allowed(user):
        return None
    parts = (update.callback_query.data or "").split(":")
    boxes = config.get().boxes
    if parts[1] == "box" and len(parts) == 3 and 0 <= int(parts[2]) < len(boxes):
        text = await _box_text(boxes[int(parts[2])])
    elif parts[1] == "all":
        data = await stock.boxes_now()
        text = texts.boxes_overview(data["boxes"], data["totals"]) if data["ok"] else texts.stock_unavailable(data["error"])
    elif parts[1] == "liv":
        data = await stock.livreurs_now()
        text = texts.livreurs_stock(data["livreurs"]) if data["ok"] else texts.stock_unavailable(data["error"])
    else:
        return None
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         text, keyboards.stock_menu(boxes))
    return None
