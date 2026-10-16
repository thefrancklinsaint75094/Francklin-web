"""/menu des admins (dispatch, franchisés) : six chapitres, chacun ouvre ses boutons ; un bouton lance la
commande comme si elle avait été tapée. Le ☰ de Telegram ne garde que l'essentiel (common.COMMANDS) ;
toutes les commandes restent utilisables en les tapant."""
from __future__ import annotations

from telegram import Message, Update

from bot import keyboards, messaging, texts
from bot.handlers import common

# chapitre → (titre, [(commande, bouton, explication)]) ; « @x » : bouton qui envoie directement le callback x.
CHAPTERS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "courses": ("🚚 Courses", [
        ("encours", "🚚 Ce soir", "courses en attente, en cours, livrées (👤 Attribuer, 📍 Adresse)"),
        ("mescourses", "🧾 Mes commandes", "les commandes que j'ai passées cette nuit"),
        ("modele", "📝 Modèle de commande", "le modèle à copier pour commander"),
        ("produits", "📋 Catalogue", "catalogue des produits"),
    ]),
    "livreurs": ("🛵 Livreurs", [
        ("livreurs", "🟢 Service / pause", "mettre un livreur en service ou en pause"),
        ("@loc_all", "🗺 Positions", "où sont les livreurs en service"),
        ("prenom", "🙂 Prénoms", "le prénom affiché à côté de chaque livreur"),
        ("gouts", "🍬 Goûts", "goûts des produits et ce qu'il reste chez chaque livreur"),
    ]),
    "users": ("👥 Utilisateurs", [
        ("users", "👥 Fiches", "chaque personne : nom, rôle, service, accès"),
        ("bannir", "⛔ Bannir", "plus aucun accès au bot"),
        ("reactiver", "✅ Réactiver", "rendre un accès"),
        ("supprimer", "🗑 Supprimer", "compte supprimé (la personne peut se réinscrire)"),
    ]),
    "stock": ("📦 Stock", [
        ("stock", "📦 Stock", "ce qui reste dans chaque box et chez les livreurs"),
        ("recharge", "🔄 Recharger", "charger ou reprendre un livreur, cash récupéré"),
        ("ravi", "✍️ Recharge en texte", "/ravi 1 : livreur, box, quantités et produits"),
        ("swipe", "↔️ Transfert", "d'un livreur à un autre (Livreur A &gt; Livreur B)"),
    ]),
    "argent": ("💶 Argent", [
        ("caisse", "💶 Caisse", "cash à récupérer chez chaque livreur"),
        ("depense", "🧾 Dépense", "noter une dépense d'un livreur"),
        ("ventes", "🧮 Ventes", "ajouter un récap de ventes au tableau"),
    ]),
    "bilans": ("📊 Bilans", [
        ("recap", "📊 Totaux", "totaux par livreur"),
        ("journal", "📒 Journal", "détail des courses livrées (envoyé à 6h)"),
        ("close", "🌙 Débrief", "débrief de la journée (/close Livreur A : fin de service)"),
        ("synchro", "🔁 Synchro", "renvoyer la nuit vers Google Sheets"),
        ("reset", "🗄 Remise à zéro", "fin de semaine : archives dans Drive, onglets vidés"),
    ]),
}


def _runners() -> dict:
    """Commande → handler (import tardif : ces modules importent beaucoup de choses)."""
    from bot.handlers import (cash, close_day, cloture, dispatch, franchise, prenoms, restock, restock_text,
                              sales, stock_view, swipe)
    from bot.handlers import variants as variants_h

    return {"encours": dispatch.encours, "mescourses": franchise.mes_courses, "modele": franchise.modele,
            "produits": dispatch.produits, "livreurs": dispatch.livreurs, "prenom": prenoms.prenom,
            "gouts": variants_h.gouts, "users": dispatch.users, "bannir": dispatch.exclure,
            "reactiver": dispatch.reactiver, "supprimer": dispatch.supprimer, "stock": stock_view.stock_cmd,
            "recharge": restock.recharge, "ravi": restock_text.ravi, "swipe": swipe.swipe, "caisse": cash.caisse,
            "depense": cash.depense, "ventes": sales.ventes, "recap": dispatch.recap, "journal": dispatch.journal,
            "close": close_day.close, "synchro": dispatch.synchro, "reset": cloture.cloture}


async def menu(update: Update, context) -> None:
    """/menu : les chapitres."""
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if not common.is_admin(user):
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    await messaging.reply(update, texts.MENU_HOME, keyboards.menu_chapters())


@common.callback
async def action(update: Update, context):
    """mn : chapitres · mn:<chapitre> : ses boutons · mn_run:<commande> : lance la commande."""
    user = await common.actor(update)
    if not common.is_admin(user) or user["status"] != "active":
        return None
    q = update.callback_query
    data = q.data or ""
    chat_id, msg_id = update.effective_chat.id, q.message.message_id
    if data == "mn":
        await messaging.edit(context.bot, chat_id, msg_id, texts.MENU_HOME, keyboards.menu_chapters())
        return None
    act, _, arg = data.partition(":")
    if act == "mn" and arg in CHAPTERS:
        title, items = CHAPTERS[arg]
        await messaging.edit(context.bot, chat_id, msg_id, texts.menu_chapter(title, items),
                             keyboards.menu_chapter(items))
        return None
    if act == "mn_run":
        handler = _runners().get(arg)
        if handler is None:
            return None
        # Même effet que de taper /commande : le handler reçoit un message « /commande ».
        typed = Message(message_id=msg_id, date=q.message.date, chat=q.message.chat, from_user=q.from_user,
                        text=f"/{arg}")
        typed.set_bot(context.bot)
        await handler(Update(update_id=update.update_id, message=typed), context)
    return None
