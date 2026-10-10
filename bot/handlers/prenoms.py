"""/prenom (admins) : le prénom affiché à côté de chaque livreur, « Livreur A (Ketur) ».

/prenom seul → la liste des livreurs et de leur prénom. « /prenom C Layla » (ou « /prenom Livreur C
Layla ») → fixe le prénom, utile pour un livreur des feuilles sans compte bot ou pour corriger celui
donné à l'inscription ; « /prenom C - » → revient au prénom de l'inscription (ou à aucun)."""
from __future__ import annotations

from telegram import Update

from bot import config, db, messaging, texts
from bot.handlers import common
from bot.services import names
from bot.services.sales import resolve_livreur

CLEAR = ("-", "aucun", "rien", "supprimer")
MAX_PRENOM = 30


def _split(args: list[str], known: list[str], livreurs: list[dict]) -> tuple[str | None, str]:
    """« Livreur C Layla » ou « C Layla » → (« Livreur C », « Layla »). Le nom le plus long d'abord."""
    for n in (3, 2, 1):
        if len(args) > n:
            name, _ = resolve_livreur(" ".join(args[:n]), known, livreurs)
            if name:
                return name, " ".join(args[n:])
    return None, ""


async def _all_livreurs() -> list[str]:
    """Noms des feuilles (LIVREUR_NAMES) puis comptes livreurs hors de cette liste."""
    known = list(config.get().livreur_names)
    for u in await db.list_users(role="livreur"):
        if u["status"] not in ("banned", "deleted") and u.get("display_name") and u["display_name"] not in known:
            known.append(u["display_name"])
    return known


async def prenom(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not common.is_admin(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    args = (update.message.text or "").split()[1:]
    known = await _all_livreurs()
    if not args:
        await names.refresh()
        await messaging.reply(update, texts.prenoms_list([(n, names.prenom(n)) for n in known]))
        return
    livreurs = await db.list_users(role="livreur", status="active")
    name, value = _split(args, known, livreurs)
    if name is None or not value.strip():
        await messaging.reply(update, texts.PRENOM_HELP)
        return
    if value.strip().lower() in CLEAR:
        await db.delete_prenom(name)
    else:
        await db.set_prenom(name, " ".join(value.split())[:MAX_PRENOM])
    await names.refresh()
    await db.log_event("prenom", user_id=user["id"], payload={"name": name, "prenom": value.strip()})
    await messaging.reply(update, texts.prenom_done(name, names.prenom(name)))
