"""Guide « appli Courses » envoyé aux livreurs : icône scooter, sonnerie des courses, raccourci sur l'écran
d'accueil (Raccourcis sur iPhone, bouton de Telegram sur Android) et réglages pour ne rater aucune course.

Le lien direct ouvre l'appli du bot (« Main Mini App » réglée dans BotFather, adresse /go) ; APP_LINK le remplace.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from telegram.error import TelegramError

from bot import db, texts

log = logging.getLogger(__name__)

MEDIA = Path(__file__).resolve().parent.parent / "static" / "media"
ICON = MEDIA / "icone-courses.png"
SOUND = MEDIA / "sonnerie-course.mp3"


def app_link(bot) -> str:
    return os.environ.get("APP_LINK") or f"https://t.me/{bot.username}?startapp"


async def send(bot, user: dict) -> bool:
    """Icône, sonnerie, puis le guide (qui parle de « ci-dessus »). False si le livreur ne reçoit rien."""
    chat = user["telegram_id"]
    try:
        await bot.send_photo(chat, photo=ICON.read_bytes(), caption=texts.GUIDE_ICON, disable_notification=True)
        await bot.send_audio(chat, audio=SOUND.read_bytes(), filename="Nouvelle course.mp3", title="Nouvelle course",
                             performer="Courses", caption=texts.GUIDE_SOUND, disable_notification=True)
        await bot.send_message(chat, texts.install_guide(app_link(bot)), disable_web_page_preview=True)
    except TelegramError as exc:
        log.warning("Guide non envoyé à %s : %s", chat, exc)
        return False
    await db.log_event("install_guide", user_id=user["id"])
    return True


async def send_all(bot) -> int:
    sent = 0
    for user in await db.list_users(role="livreur", status="active"):
        sent += await send(bot, user)
    return sent
