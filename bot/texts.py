"""Tous les textes envoyés aux utilisateurs (catalogue §14).

Messages en HTML : tout texte venant d'un utilisateur passe par esc().
"""
from __future__ import annotations

import html
from decimal import Decimal, ROUND_HALF_UP

ROLE_LABEL = {"franchise": "Franchisé", "livreur": "Livreur", "dispatch": "Dispatch"}


def esc(value) -> str:
    return html.escape(str(value), quote=False) if value is not None else ""


def eur(value) -> str:
    """60 → « 60 € » ; 1035 → « 1 035 € » ; 12.5 → « 12,50 € »."""
    d = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole = int(d)
    cents = int((abs(d) - abs(whole)) * 100)
    grouped = f"{whole:,}".replace(",", " ")
    if cents:
        return f"{grouped},{cents:02d} €"
    return f"{grouped} €"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def ordinal(n: int) -> str:
    return "1re" if n == 1 else f"{n}e"


# ================================================================ onboarding

ASK_ROLE = "Tu es franchisé ou livreur ?"
ASK_NAME = "Ton prénom ou le nom de ton point de vente ?"
NAME_TOO_LONG = "C'est un peu long — donne-moi juste ton prénom ou le nom du point de vente."
REGISTRATION_SENT = "Merci, ton inscription est envoyée. Tu seras prévenu dès qu'elle est validée."
PENDING = "Ton inscription est en attente de validation."
REJECTED = "Ton inscription n'a pas été retenue."
START_TO_REGISTER = "Envoie /start pour t'inscrire."
BANNED_NOTICE = "Ton accès a été retiré."

WELCOME_FRANCHISE = (
    "✅ Tu es validé.\n\n"
    "Envoie-moi tes commandes comme tu le fais d'habitude — en texte ou en vocal. "
    "Je te renvoie une fiche à confirmer, puis je trouve le livreur le plus proche.\n\n"
    "Il me faut au minimum : l'adresse, les produits et le prix.\n\n"
    "/mescourses — voir mes courses de la nuit"
)

WELCOME_LIVREUR = (
    "✅ Tu es validé.\n\n"
    "/dispo — te mettre en service (tu partageras ta position en direct)\n"
    "/pause — te retirer temporairement\n"
    "/macourse — revoir ta course en cours\n\n"
    "Tu ne reçois que les courses proches de toi. Une seule à la fois — appuie sur « Bientôt libre » "
    "quand tu termines pour enchaîner.\n\n"
    "Le client prend autre chose ? « ✏️ Modifier la commande » sur ta course : ➖ / ➕ pour les quantités, "
    "➕ Ajouter un produit pour choisir dans la liste, et les boutons de prix. Rien à taper."
)

WELCOME_DISPATCH = (
    "Dispatch actif.\n\n"
    "/recap — totaux par livreur\n"
    "/journal — détail des courses livrées (envoyé automatiquement à 6h)\n"
    "/encours — courses en attente et en cours\n"
    "/users — tous les utilisateurs\n"
    "/exclure — retirer un accès\n"
    "/reactiver — rendre un accès\n"
    "/produits — catalogue des produits (ajouter, supprimer)\n"
    "/synchro — renvoyer les courses de la nuit vers Google Sheets"
)

MODEL_HELP = (
    "📝 <b>Modèle de commande</b>\n\n"
    "<code>12 rue de Rivoli 75004 Paris\n"
    "2 vodka 60\n"
    "1 coca 5\n"
    "\n"
    "Digicode 45A32, 3e étage</code>\n\n"
    "• 1re ligne : l'adresse, avec le code postal.\n"
    "• Puis une ligne par produit : <b>quantité</b>, <b>produit</b>, <b>prix total</b> de la ligne.\n"
    "• Une ligne vide, puis le commentaire : digicode, étage, consignes. Seul le livreur le verra.\n\n"
    "Je fais le total. /produits — voir les produits connus"
)

WELCOME_FRANCHISE_RULES = (
    "✅ Tu es validé.\n\n"
    "Envoie-moi tes commandes en texte. Je te renvoie une fiche à confirmer, "
    "puis je trouve le livreur le plus proche.\n\n"
    + MODEL_HELP.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    + "\n\n/modele — revoir ce modèle\n/produits — produits connus\n/mescourses — voir mes courses de la nuit"
)

WELCOME = {"franchise": WELCOME_FRANCHISE, "livreur": WELCOME_LIVREUR, "dispatch": WELCOME_DISPATCH}


def welcome(role: str) -> str:
    """Message de bienvenue du rôle, adapté au mode de lecture des commandes."""
    from bot import config

    if role == "franchise" and not config.get().uses_ai:
        return WELCOME_FRANCHISE_RULES
    return WELCOME[role]

UNBANNED_NOTICE = "✅ Ton accès est rétabli."


def new_registration(role: str, real_name: str, username: str | None) -> str:
    lines = ["🆕 Nouvelle inscription", f"Rôle : {ROLE_LABEL[role]}", f"Nom : {esc(real_name)}"]
    lines.append(f"Pseudo : @{esc(username)}" if username else "Pseudo : —")
    return "\n".join(lines)


def registration_approved(user: dict) -> str:
    return f"✅ Validé : {esc(user['display_name'])} — {esc(user.get('real_name'))}"


def registration_rejected(user: dict) -> str:
    return f"❌ Inscription refusée : {ROLE_LABEL[user['role']]} — {esc(user.get('real_name'))}"


ALREADY_HANDLED = "Déjà traité."


# ================================================================ génériques

GENERIC_ERROR = "Petit souci technique de mon côté, ton message est bien noté. Réessaie dans une minute."
ONLY_TEXT_OR_VOICE = "Je ne traite que les commandes en texte ou en vocal."
LIVREUR_TEXT_HINT = "Pour parler à un franchisé, utilise le bouton 💬 Contacter sur ta course."
NOT_FOR_YOU = "Cette commande n'est pas disponible pour toi."
CANCELLED_OP = "OK, rien n'a changé."


# ================================================================ franchisé : commandes

EXTRACTION_PARSE_FAIL = "Je n'ai pas réussi à lire ta commande, tu peux la réécrire ?"
NO_ORDER_FOUND = "Je n'ai pas trouvé de commande dans ton message."
NO_ORDER_IN_IMAGE = "Je n'ai pas trouvé de commande sur cette image."
VOICE_UNSUPPORTED = "Les vocaux ne sont pas encore pris en charge, écris-moi la commande."
PHOTO_UNSUPPORTED = "Je ne lis pas encore les captures d'écran, écris-moi la commande."
VOICE_FAILED = "Je n'ai pas réussi à écouter ton vocal, tu peux l'écrire ?"
CORRECTION_PROMPT = "Renvoie-moi la commande corrigée."
CORRECTION_CANCELLED = "Correction annulée."
DRAFT_EXPIRED = "⏱ Fiche expirée — renvoie ta commande si besoin."
DRAFT_CANCELLED = "Fiche annulée."
DRAFT_REPLACED = "✏️ Fiche corrigée ci-dessous."

_FIELD_NAMES = {"address": "l'adresse", "products": "les produits", "price": "le prix"}


def missing_fields(order) -> str:
    names = [_FIELD_NAMES[f] for f in order.missing]
    if len(names) == 1:
        what = names[0]
    else:
        what = ", ".join(names[:-1]) + " et " + names[-1]
    label = order.address or order.products or "ta commande"
    return f"Il me manque {what} pour : {esc(label)}."


def complement_hint(course_id: int) -> str:
    return (
        f"Je n'ai pas trouvé de commande. Pour compléter la course #{course_id}, "
        "utilise 💬 Contacter sur la course."
    )


def geocode_not_found(address: str) -> str:
    return f"Je n'arrive pas à localiser cette adresse : \"{esc(address)}\". Tu peux la préciser ?"


def out_of_zone(where: str) -> str:
    return f"Cette adresse est hors zone ({esc(where)}). Je ne peux pas la prendre."


def order_lines(data: dict, price_suffix: str = "", products_list: bool = False) -> list[str]:
    """Bloc 📍 🔑 🍾 💶 🕐 commun à la fiche franchisé et à la fiche complète livreur.
    products_list : un produit par ligne (fiche du livreur)."""
    lines = [f"📍 {esc(data['address'])}"]
    if data.get("address_detail"):
        lines.append(f"🔑 {esc(_cap(data['address_detail']))}")
    parts = [p.strip() for p in str(data["products"]).split(" + ") if p.strip()]
    if products_list and len(parts) > 1:
        lines.append("🍾 Produits :")
        lines += [f"   • {esc(p)}" for p in parts]
    else:
        lines.append(f"🍾 {esc(data['products'])}")
    lines.append(f"💶 {eur(data['price'])}{price_suffix}")
    if data.get("requested_time"):
        lines.append(f"🕐 {esc(data['requested_time'])}")
    return lines


def draft_card(ex: dict, price_max: int) -> str:
    lines: list[str] = []
    if ex.get("header"):
        lines.append(f"<b>{esc(ex['header'])}</b>")
    if ex.get("duplicate_of"):
        lines.append(
            f"⚠️ Ressemble à la course #{ex['duplicate_of']} envoyée il y a {ex.get('duplicate_minutes', 0)} min"
        )
    if not ex.get("has_housenumber", True):
        lines.append("⚠️ Pas de numéro de rue trouvé — vérifie l'adresse")
    for warning in ex.get("warnings") or []:
        lines.append(esc(warning))
    if lines:
        lines.append("")
    body = order_lines(ex)
    if float(ex["price"]) > price_max:
        idx = next(i for i, l in enumerate(body) if l.startswith("💶"))
        body.insert(idx + 1, "⚠️ Prix élevé, vérifie")
    lines.extend(body)
    lines += ["", "C'est correct ?"]
    return "\n".join(lines)


def draft_already_confirmed(course_id: int | None) -> str:
    return f"Déjà confirmée (course #{course_id})." if course_id else "Déjà confirmée."


DRAFT_NOT_ACTIVE = "Cette fiche n'est plus active."


def course_summary(course: dict) -> str:
    return f"📍 {esc(course['address'])} — {eur(course['price'])}"


def franchise_course_status(course: dict, livreur: dict | None, no_livreur: bool = False,
                            none_on_duty: bool = False) -> str:
    cid = course["id"]
    status = course["status"]
    if status == "pending":
        if none_on_duty:
            head = (
                f"✅ Course #{cid} enregistrée — ⚠️ aucun livreur en service pour l'instant, "
                "je la propose dès qu'un livreur se connecte."
            )
        elif no_livreur:
            head = f"⚠️ Course #{cid} — aucun livreur disponible pour l'instant, je continue à chercher."
        else:
            head = f"✅ Course #{cid} envoyée aux livreurs."
    elif status == "assigned":
        head = f"🚴 Course #{cid} — prise par {esc(livreur['display_name'] if livreur else 'un livreur')}"
    elif status == "delivered":
        from bot.timeutil import hhmm, parse_ts

        head = f"✅ Course #{cid} — livrée à {hhmm(parse_ts(course['delivered_at']))}"
    elif status == "cancelled":
        head = f"🗑 Course #{cid} — retirée."
    else:
        head = f"🗑 Course #{cid} — annulée sur place."
    return f"{head}\n{course_summary(course)}"


def withdraw_confirm(course_id: int) -> str:
    return f"Retirer la course #{course_id} ?"


ALREADY_CLOSED = "Cette course est déjà terminée."


def livreur_cancelled_for_franchise(livreur_name: str, course_id: int) -> str:
    return f"⚠️ {esc(livreur_name)} a annulé la course #{course_id}, je cherche un autre livreur."


def course_released_for_franchise(course_id: int) -> str:
    return f"⚠️ Le livreur a été retiré de la course #{course_id}, je cherche un autre livreur."


STATUS_LABEL = {
    "pending": "⏳ en attente",
    "delivered": "✅ livrée",
    "cancelled": "🗑 retirée",
    "cancelled_on_site": "⚠️ annulée sur place",
}


def mes_courses(courses: list[dict], livreurs: dict[str, dict]) -> str:
    if not courses:
        return "Mes courses cette nuit\n\nAucune course pour l'instant."
    lines = ["Mes courses cette nuit", ""]
    for c in courses:
        if c["status"] == "assigned":
            lv = livreurs.get(c["livreur_id"]) or {}
            state = f"🚴 en cours ({esc(lv.get('display_name', 'livreur'))})"
        else:
            state = STATUS_LABEL[c["status"]]
        lines.append(f"#{c['id']} — {esc(c['district'])} — {eur(c['price'])} — {state}")
    return "\n".join(lines)


# ================================================================ livreur

DISPO_PROMPT = (
    "Partage-moi ta position en direct (📎 → Position → Partager ma position en direct → 8 heures). "
    "Tant que je la reçois, tu es visible pour les courses."
)
ON_DUTY = "✅ Tu es en service. Je t'envoie les courses proches de toi."
PAUSED = "⏸ Tu es en pause. Relance /dispo pour reprendre."
STATIC_POSITION_WARNING = (
    "⚠️ Position reçue, mais elle ne se mettra pas à jour. Pour rester visible plus de 30 min, "
    "partage ta position <b>en direct</b>."
)
POSITION_LOST = "📍 Je ne reçois plus ta position, tu n'es plus visible. Relance /dispo quand tu reprends."
SOON_FREE_ACK = "👍 Tu vas recevoir la prochaine course proche de toi."
SOON_FREE_AUTO = "📍 Tu arrives — je peux te proposer la course suivante."
SOON_FREE_NO_COURSE = "Tu n'as pas de course en cours."
SOON_FREE_ALREADY = "C'est noté, tu as déjà une course réservée."
TOO_LATE = "Trop tard, course déjà prise."
ALREADY_HAS_COURSE = "Tu as déjà une course en cours."
NO_ASSIGNED = "Tu n'as aucune course en cours."
LIVREUR_CANCEL_CONFIRM = "Tu es sûr ? Ça sera compté dans tes annulations."


def proposal(course: dict, distance_label: str) -> str:
    lines = [
        f"🆕 Course #{course['id']}",
        f"📍 {esc(course['district'])} · à {distance_label} de toi",
        f"🍾 {esc(course['products'])}",
        f"💶 {eur(course['price'])}",
    ]
    if course.get("requested_time"):
        lines.append(f"🕐 {esc(course['requested_time'])}")
    return "\n".join(lines)


def proposal_taken(course_id: int) -> str:
    return f"❌ Course #{course_id} — déjà attribuée"


def proposal_withdrawn(course_id: int) -> str:
    return f"❌ Course #{course_id} — retirée"


def full_fiche(course: dict, franchise: dict) -> str:
    lines = [f"🚴 Course #{course['id']} — c'est pour toi", ""]
    lines += order_lines(course, price_suffix=" à encaisser", products_list=True)
    who = esc(franchise.get("display_name") or "Franchisé")
    if franchise.get("telegram_username"):
        who += f" — @{esc(franchise['telegram_username'])}"
    lines += ["", who]
    return "\n".join(lines)


def delivered_for_livreur(course: dict) -> str:
    return f"✅ Course #{course['id']} livrée — {eur(course['price'])} encaissés."


def livreur_cancelled(course_id: int) -> str:
    return f"❌ Tu as annulé la course #{course_id}."


def cancelled_by_franchise(course_id: int) -> str:
    return f"⚠️ Course #{course_id} annulée par le franchisé. Tu es libéré."


def cancelled_by_dispatch_for_livreur(course_id: int) -> str:
    return f"⚠️ Course #{course_id} annulée par le dispatch. Tu es libéré."


def released_by_dispatch_for_livreur(course_id: int) -> str:
    return f"⚠️ Le dispatch t'a retiré la course #{course_id}. Tu es libéré."


def forced_delivered_for_livreur(course_id: int) -> str:
    return f"✅ Course #{course_id} marquée livrée par le dispatch."


def stuck_for_livreur(course_id: int) -> str:
    return f"⏰ Tu as toujours la course #{course_id} en cours — appuie sur Livré si c'est fait."


# ================================================================ modification de la commande (livreur)

def order_editor(course_id: int, lines: list[dict], sel: int | None = None) -> str:
    out = [f"✏️ <b>Course #{course_id} — modifier la commande</b>", ""]
    if not lines:
        out.append("Aucun produit. Ajoute-en avec ➕ Ajouter un produit.")
    for i, line in enumerate(lines):
        price = eur(line["x"]) if float(line["x"]) > 0 else "⚠️ prix ?"
        mark = "  ◀️" if sel == i else ""
        out.append(f"{i + 1}. {line['q']} × {esc(line['p'])} — {price}{mark}")
    out += ["", f"💶 <b>Total : {eur(sum(float(l['x']) for l in lines))}</b>", ""]
    if sel is not None and 0 <= sel < len(lines):
        out.append(f"Prix de la ligne {sel + 1} ({esc(lines[sel]['p'])}) : ajuste avec les boutons, "
                   "ou tape le prix (ex. 35). Puis ✅ OK.")
    else:
        out.append("➖ / ➕ : quantité. Touche un produit pour changer son prix. ✅ Valider quand c'est bon.")
    return "\n".join(out)


def order_picker(course_id: int, page: int, pages: int) -> str:
    text = f"✏️ Course #{course_id} — choisis le produit à ajouter"
    if pages > 1:
        text += f" (page {page + 1}/{pages})"
    return text


ORDER_EDIT_NO_CATALOG = "Aucun produit dans le catalogue : demande au dispatch d'en ajouter (/produits)."
ORDER_EDIT_EXPIRED = "Modification expirée : rouvre ✏️ Modifier la commande."
ORDER_EDIT_UNCHANGED = "Aucun changement."
ORDER_EDIT_EMPTY = "La commande doit garder au moins un produit."
ORDER_EDIT_TOO_MANY = "Trop de produits sur cette commande."
ORDER_EDIT_PRICE_HINT = "Tape seulement le prix de la ligne, par exemple 35."
ORDER_EDIT_USE_BUTTONS = "Utilise les boutons de la commande en cours de modification (➖ ➕ ✅ Valider)."


def order_edit_missing_price(names: list[str]) -> str:
    return "Mets le prix de : " + ", ".join(names)[:180]


def order_modified_for_franchise(course: dict, old_price, livreur_name: str) -> str:
    lines = [f"✏️ Course #{course['id']} modifiée par {esc(livreur_name)} sur place", "",
             f"🍾 {esc(course['products'])}",
             f"💶 {eur(course['price'])} (avant : {eur(old_price)})"]
    return "\n".join(lines)


def d_order_modified(course: dict, livreur: dict, old_products: str, old_price) -> str:
    return (
        f"✏️ #{course['id']} — modifiée par {esc(livreur['display_name'])} — "
        f"{eur(old_price)} → {eur(course['price'])}\n"
        f"avant : {esc(old_products)}\n"
        f"après : {esc(course['products'])}"
    )


# ================================================================ relais

COURSE_FINISHED = "Cette course est terminée."
WRITE_TEXT = "Écris ton message en texte."
RELAY_SENT = "✉️ Envoyé."
RELAY_CANCELLED = "Message annulé."


def relay_prompt(course_id: int) -> str:
    return f"Écris ton message pour la course #{course_id}."


def relayed_message(course: dict, sender_name: str, content: str) -> str:
    return f"💬 Course #{course['id']} — {esc(course['address'])}\n{esc(sender_name)} : « {esc(content)} »"


# ================================================================ dispatch : notifications

def d_created(course: dict, franchise: dict) -> str:
    text = f"🆕 #{course['id']} — {esc(franchise['display_name'])} — {esc(course['district'])} — {eur(course['price'])}"
    if course.get("possible_duplicate_of"):
        text += f" (⚠️ doublon possible de #{course['possible_duplicate_of']})"
    return text


def d_taken(course: dict, franchise: dict, livreur: dict) -> str:
    return (
        f"🚴 #{course['id']} — {esc(franchise['display_name'])} → {esc(livreur['display_name'])} — "
        f"{esc(course['district'])} — {eur(course['price'])}"
    )


def d_delivered(course: dict, livreur: dict, far_label: str | None = None) -> str:
    text = f"✅ #{course['id']} — livrée — {esc(livreur['display_name'])} — {eur(course['price'])}"
    if far_label:
        text += f"\n⚠️ livré à {far_label} de l'adresse"
    return text


def d_livreur_cancel(course_id: int, livreur: dict) -> str:
    return (
        f"⚠️ #{course_id} — annulée par {esc(livreur['display_name'])} "
        f"({ordinal(livreur['cancel_count'])} annulation) — remise en diffusion"
    )


def d_withdrawn(course_id: int, franchise: dict) -> str:
    return f"🗑 #{course_id} — retirée par {esc(franchise['display_name'])}"


def d_cancelled_on_site(course_id: int, franchise: dict, livreur: dict | None) -> str:
    who = esc(livreur["display_name"]) if livreur else "le livreur"
    return f"⚠️ #{course_id} — annulée sur place par {esc(franchise['display_name'])} ({who} déplacé pour rien)"


def d_no_livreur(course_id: int) -> str:
    return f"🔴 #{course_id} — aucun livreur disponible"


def d_stuck(course_id: int, minutes: int, livreur: dict) -> str:
    return f"⏰ #{course_id} — en cours depuis {minutes} min ({esc(livreur['display_name'])})"


def d_blocked(user: dict) -> str:
    return f"🚫 {esc(user.get('display_name') or user.get('real_name') or 'Un utilisateur')} a bloqué le bot"


D_TWO_INSTANCES = (
    "⚠️ Deux copies du bot tournent en même temps depuis plus de 2 minutes. "
    "Vérifie qu'il n'y a qu'une seule réplique sur Railway et aucun bot lancé ailleurs."
)


def d_error(kind: str) -> str:
    return f"⚠️ Erreur technique : {esc(kind)}"


def d_override(action: str, course_id: int) -> str:
    return {
        "cancel": f"🗑 #{course_id} — annulée par le dispatch",
        "deliver": f"✅ #{course_id} — marquée livrée par le dispatch",
        "release": f"🔁 #{course_id} — remise en diffusion par le dispatch",
    }[action]


BOT_STARTED = "🟢 Bot démarré"


# ================================================================ dispatch : commandes

def recap(label: str, rows: list[tuple[str, int, float]], on_site: list[tuple[str, int]],
          pending: int | None, assigned: int | None) -> str:
    if not rows:
        text = f"📊 Récap nuit du {label} — aucune course livrée."
    else:
        lines = [f"📊 Récap nuit du {label}", ""]
        for name, count, total in rows:
            lines.append(f"{esc(name)} — {count} course{'s' if count > 1 else ''} — {eur(total)}")
        n = sum(r[1] for r in rows)
        lines += ["", f"Total : {n} course{'s' if n > 1 else ''} — {eur(sum(r[2] for r in rows))}"]
        text = "\n".join(lines)
    extra = []
    if on_site:
        total = sum(c for _, c in on_site)
        detail = ", ".join(f"{esc(name)} ×{c}" for name, c in on_site)
        extra.append(f"Annulées sur place : {total} ({detail})")
    if pending is not None:
        extra.append(f"En attente : {pending} · En cours : {assigned}")
    if extra:
        text += "\n\n" + "\n".join(extra)
    return text


def journal_header(label: str, count: int) -> str:
    if count == 0:
        return f"📋 Journal nuit du {label} — aucune course livrée."
    return f"📋 Journal nuit du {label} — {count} course{'s' if count > 1 else ''} livrée{'s' if count > 1 else ''}"


def journal_line(time_label: str, course: dict, franchise_name: str, livreur_name: str) -> str:
    return (
        f"{time_label} · #{course['id']} · {esc(franchise_name)} → {esc(livreur_name)} · "
        f"{esc(course['address'])} · {eur(course['price'])}"
    )


def journal_total(total: float) -> str:
    return f"Total : {eur(total)}"


def encours(pending_lines: list[str], assigned_lines: list[str], on_duty: int, total_livreurs: int) -> str:
    lines = ["🚦 En ce moment", "", f"⏳ En attente ({len(pending_lines)})"]
    lines += pending_lines or ["—"]
    lines += ["", f"🚴 En cours ({len(assigned_lines)})"]
    lines += assigned_lines or ["—"]
    lines += ["", f"📍 Livreurs en service : {on_duty} / {total_livreurs}"]
    return "\n".join(lines)


def encours_pending_line(course: dict, franchise: dict) -> str:
    return (
        f"#{course['id']} — {esc(franchise['display_name'])} — {esc(course['district'])} — "
        f"{eur(course['price'])} — vague {course['broadcast_round']}"
    )


def encours_assigned_line(course: dict, franchise: dict, livreur: dict, minutes: int, two: bool) -> str:
    text = (
        f"#{course['id']} — {esc(franchise['display_name'])} → {esc(livreur['display_name'])} — "
        f"{esc(course['district'])} — {eur(course['price'])} — depuis {minutes} min"
    )
    return text + (" · 2 courses" if two else "")


def override_confirm(action: str, course_id: int) -> str:
    return {
        "cancel": f"Annuler la course #{course_id} ?",
        "deliver": f"Marquer la course #{course_id} comme livrée ?",
        "release": f"Retirer le livreur et remettre la course #{course_id} en diffusion ?",
    }[action]


def user_who(user: dict) -> str:
    """« Livreur 1 — Karim (@karim_75) » — réservé au dispatch."""
    text = esc(user.get("display_name") or ROLE_LABEL[user["role"]])
    text += f" — {esc(user.get('real_name') or '?')}"
    if user.get("telegram_username"):
        text += f" (@{esc(user['telegram_username'])})"
    return text


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def users_list(franchises: list[str], livreurs: list[str], pending: list[str]) -> str:
    lines = ["👥 Utilisateurs", "", "<b>Franchisés</b>"]
    lines += franchises or ["—"]
    lines += ["", "<b>Livreurs</b>"]
    lines += livreurs or ["—"]
    lines += ["", "<b>En attente de validation</b>"]
    lines += pending or ["—"]
    return "\n".join(lines)


EXCLURE_HEADER = "Qui veux-tu exclure ?"
EXCLURE_EMPTY = "Aucun utilisateur actif à exclure."
REACTIVER_HEADER = "Qui veux-tu réactiver ?"
REACTIVER_EMPTY = "Aucun utilisateur exclu."


def ban_confirm(user: dict) -> str:
    return f"Exclure {esc(user.get('display_name') or ROLE_LABEL[user['role']])} ({esc(user.get('real_name'))}) ?"


def banned_done(user: dict) -> str:
    return f"⛔ {esc(user.get('display_name'))} ({esc(user.get('real_name'))}) est exclu."


def unbanned_done(user: dict) -> str:
    return f"✅ {esc(user.get('display_name'))} ({esc(user.get('real_name'))}) est réactivé."


# ================================================================ catalogue

CATALOG_ADD_PROMPT = (
    "Envoie-moi les produits, <b>un par ligne</b>. Après « : », ajoute les autres façons "
    "dont les franchisés l'écrivent.\n\n"
    "<code>Vodka Absolut : absolut, abso\n"
    "Coca-Cola : coca\n"
    "Jack Daniel's : jack, jd\n"
    "Red Bull</code>\n\n"
    "Majuscules, accents, pluriels et contenances (70cl, 1L) sont ignorés automatiquement. "
    "Un produit déjà connu est complété, pas dupliqué."
)
CATALOG_NOTHING = "Je n'ai trouvé aucun produit dans ton message."
CATALOG_DELETED = "Produit supprimé."


def catalog_list(products: list[dict], for_dispatch: bool = False) -> str:
    if not products:
        text = "📦 Catalogue vide pour l'instant."
        return text + ("\n\nAjoute des produits avec le bouton ci-dessous." if for_dispatch else "")
    lines = [f"📦 <b>Produits connus</b> ({len(products)})", ""]
    for p in products:
        line = f"• {esc(p['name'])}"
        if p.get("aliases"):
            line += f" — <i>{esc(', '.join(p['aliases']))}</i>"
        lines.append(line)
    if for_dispatch:
        lines += ["", "🗑 sous un produit pour le supprimer."]
    return "\n".join(lines)


def catalog_summary(summary: dict) -> str:
    lines = []
    if summary["added"]:
        lines.append(f"✅ Ajouté{'s' if len(summary['added']) > 1 else ''} : {esc(', '.join(summary['added']))}")
    if summary["updated"]:
        lines.append(f"✏️ Complété{'s' if len(summary['updated']) > 1 else ''} : {esc(', '.join(summary['updated']))}")
    if summary["unchanged"]:
        lines.append(f"= Déjà à jour : {esc(', '.join(summary['unchanged']))}")
    for alias, others in summary["conflicts"]:
        lines.append(f"⚠️ « {esc(alias)} » désigne aussi : {esc(', '.join(others))} — "
                     "le bot demandera de préciser.")
    return "\n".join(lines) or CATALOG_NOTHING


# ================================================================ Google Sheets

SHEETS_DISABLED = "Google Sheets n'est pas encore relié au bot."


def sheets_synced(total: int, added: int) -> str:
    if total == 0:
        return "📗 Aucune course livrée cette nuit : rien à envoyer."
    return f"📗 Google Sheets à jour : {added} ligne{'s' if added > 1 else ''} ajoutée{'s' if added > 1 else ''} sur {total} course{'s' if total > 1 else ''} de la nuit."


def sheets_failed(error: str) -> str:
    return f"⚠️ Envoi à Google Sheets impossible : {esc(error[:200])}"
