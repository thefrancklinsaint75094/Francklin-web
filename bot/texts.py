"""Tous les textes envoyés aux utilisateurs (catalogue §14).

Messages en HTML : tout texte venant d'un utilisateur passe par esc().
"""
from __future__ import annotations

import html
from decimal import Decimal, ROUND_HALF_UP

ROLE_LABEL = {"franchise": "Franchisé", "livreur": "Livreur", "dispatch": "Dispatch", "ravitailleur": "Ravitailleur"}


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

ASK_ROLE = "Tu es franchisé, livreur ou ravitailleur ?"
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

LIVE_GUIDE = (
    "📍 <b>Partage ta position une fois pour toutes</b> (depuis ton téléphone) :\n"
    "1. Touche 📎 en bas de cette conversation\n"
    "2. Choisis <b>Position</b>, puis <b>Partager ma position en direct</b>\n"
    "3. Choisis <b>« Jusqu'à ce que je l'arrête »</b>\n"
    "C'est tout : ensuite, un simple /dispo te met en service chaque jour.\n"
    "Si rien ne se passe : autorise la localisation pour Telegram dans les réglages du téléphone. "
    "« Envoyer ma position actuelle » ne suffit pas (elle ne bouge plus)."
)

WELCOME_LIVREUR = (
    "✅ Tu es validé.\n\n"
    "/dispo — te mettre en service (tu partageras ta position en direct et diras comment tu te déplaces : "
    "🚶 transport, 🛵 deux-roues ou 🚗 voiture)\n"
    "/pause — te retirer temporairement\n"
    "/macourse — revoir ta course en cours\n"
    "/depense — noter une dépense (essence, repas…) ou une avance sur ta paye\n"
    "/macaisse — le cash que tu dois remettre\n"
    "/gouts — coche les goûts qu'il te reste (MSX banane, fraise…)\n\n"
    "Tu ne reçois que les courses proches de toi. Une seule à la fois — appuie sur « Bientôt libre » "
    "quand tu termines pour enchaîner.\n\n"
    "Course livrée : écris <b>OK</b> (« OK CB » si payé par carte ou virement). Il faut valider ta course "
    "avant de passer à la suivante.\n"
    "Le client a pris autre chose ? Écris <b>Modif</b> : ➖ / ➕ pour les quantités, ➕ Ajouter un produit, "
    "les boutons de prix (de 10 en 10 €) ; le franchisé valide ta modification."
    "\n\n" + LIVE_GUIDE
)

WELCOME_DISPATCH = (
    "Dispatch actif.\n\n"
    "/recap — totaux par livreur\n"
    "/journal — détail des courses livrées (envoyé automatiquement à 6h)\n"
    "/encours — courses en attente et en cours (👤 Attribuer à un livreur)\n"
    "/livreurs — mettre un livreur en service ou en pause\n"
    "/users — tous les utilisateurs\n"
    "/bannir — bannir quelqu'un : plus aucun accès au bot (réactivable avec /reactiver)\n"
    "/reactiver — rendre un accès\n"
    "/supprimer — supprimer un compte (définitif, la personne peut se réinscrire de zéro)\n"
    "/prenom — le prénom affiché à côté de chaque livreur : « Livreur A (Ketur) »\n"
    "/gouts — goûts des produits (MSX banane, fraise…) et ce qu'il reste chez chaque livreur\n"
    "/produits — catalogue des produits (ajouter, supprimer)\n"
    "/recharge — charger ou reprendre un livreur, cash récupéré\n"
    "/ravi 1 — même chose en texte : livreur, box, quantités et produits\n"
    "/swipe — transférer des produits d'un livreur à un autre (Livreur A &gt; Livreur B)\n"
    "/stock — ce qui reste dans chaque box et chez les livreurs\n"
    "/caisse — cash à récupérer chez chaque livreur\n"
    "/depense — noter une dépense d'un livreur\n"
    "/synchro — renvoyer les courses de la nuit vers Google Sheets\n"
    "/ventes — ajouter un récap de ventes au tableau (par livreur)\n"
    "/close — débrief de la journée (la journée est finie)\n"
    "/reset — remise à zéro de la semaine : archives dans Drive, onglets vidés"
)

MODEL_HELP = (
    "📝 <b>Modèle de commande</b>\n\n"
    "<code>12 rue de Rivoli 75004 Paris\n"
    "2 vodka 60\n"
    "1 coca 10\n"
    "\n"
    "Digicode 45A32, 3e étage</code>\n\n"
    "• 1re ligne : l'adresse, avec le code postal.\n"
    "• Puis une ligne par produit : <b>quantité</b>, <b>produit</b>, <b>prix total</b> de la ligne "
    "(de 10 en 10 €).\n"
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

WELCOME_RAVITAILLEUR = (
    "✅ Tu es validé.\n\n"
    "/recharge — charger un livreur, reprendre du stock ou noter le cash récupéré.\n"
    "/ravi — la même chose en un message (livreur, box, « 12 DIV », « -2 MSX », « cash 300 »).\n"
    "/swipe — transférer des produits d'un livreur à un autre (Livreur A &gt; Livreur B).\n"
    "/stock — ce qui reste dans chaque box et chez les livreurs.\n"
    "/caisse — cash à récupérer chez chaque livreur (💶 Récupérer).\n\n"
    "Tout se fait par boutons : le livreur, chargement ou reprise, le box, les produits et les quantités, "
    "puis le cash. Chaque rechargement part dans le tableau Rechargement et le livreur est prévenu."
)

WELCOME = {"franchise": WELCOME_FRANCHISE, "livreur": WELCOME_LIVREUR, "dispatch": WELCOME_DISPATCH,
           "ravitailleur": WELCOME_RAVITAILLEUR}


ADMIN_HELP_FRANCHISE = (
    "\n\n👑 <b>Pleins pouvoirs</b> — sur ta course : 👤 Attribuer à un livreur, ✏️ Modifier, 📦 Livrée.\n"
    "/encours — toutes les courses · /livreurs — service / pause des livreurs\n"
    "/recap · /journal · /users · /recharge · /ravi · /swipe · /stock · /caisse · /depense · /ventes · /synchro · /close · /reset · /bannir · /reactiver · /supprimer · /prenom · /gouts"
)


def welcome(role: str) -> str:
    """Message de bienvenue du rôle, adapté au mode de lecture des commandes."""
    from bot import config

    if role == "franchise":
        base = WELCOME_FRANCHISE_RULES if not config.get().uses_ai else WELCOME_FRANCHISE
        return base + ADMIN_HELP_FRANCHISE
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
LIVREUR_TEXT_HINT = ("Course livrée ? Écris <b>OK</b> (ou « OK CB » si payé par carte / virement), "
                     "ou <b>Modif</b> si le client a pris autre chose.\n"
                     "Pour parler à un franchisé, utilise le bouton 💬 Contacter sur ta course.")
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


def price_not_round(order, problems: list[str]) -> str:
    label = order.address or order.products or "ta commande"
    return (
        f"⚠️ Les prix vont de 10 en 10 € : {esc(', '.join(problems))} — pas possible pour : {esc(label)}.\n"
        "Renvoie la commande avec le bon prix (ex. 60, 70, 80…)."
    )


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
    LIVE_GUIDE + "\n\n"
    "Tant que je reçois ta position, tu es visible pour les courses.\n\n"
    "Et tu te déplaces comment aujourd'hui ? 🚶 Transport (à pied, métro, bus) · 🛵 Deux-roues · 🚗 Voiture"
)
DISPO_REMINDER = "⏳ Je n'ai toujours pas reçu ta position, tu n'es pas encore en service.\n\n" + LIVE_GUIDE
ON_DUTY = "✅ Tu es en service. Je t'envoie les courses proches de toi."
PAUSED = "⏸ Tu es en pause. Relance /dispo pour reprendre."
STATIC_POSITION_WARNING = (
    "⚠️ Position reçue, mais elle ne se mettra pas à jour. Pour rester visible plus de 30 min, "
    "partage ta position <b>en direct</b> : 📎 → Position → Partager ma position en direct → "
    "« Jusqu'à ce que je l'arrête »."
)
POSITION_LOST = "📍 Je ne reçois plus ta position, tu n'es plus visible. Relance /dispo quand tu reprends."
POSITION_SILENT = ("📍 Je ne reçois plus ta position depuis quelques minutes. Vérifie que le partage en direct est "
                   "toujours actif (📎 → Position → Partager ma position en direct → « Jusqu'à ce que je "
                   "l'arrête »), sinon tu vas sortir du service.")
SOON_FREE_ACK = "👍 Tu vas recevoir la prochaine course proche de toi."
SOON_FREE_AUTO = "📍 Tu arrives — je peux te proposer la course suivante."
SOON_FREE_NO_COURSE = "Tu n'as pas de course en cours."
SOON_FREE_ALREADY = "C'est noté, tu as déjà une course réservée."
TOO_LATE = "Trop tard, course déjà prise."
ALREADY_HAS_COURSE = "Tu as déjà une course en cours."
NO_ASSIGNED = "Tu n'as aucune course en cours."
LIVREUR_CANCEL_CONFIRM = "Tu es sûr ? Ça sera compté dans tes annulations."


def proposal(course: dict, distance_label: str | None) -> str:
    where = f"📍 {esc(course['district'])}"
    if distance_label:
        where += f" · à {distance_label} de toi"
    lines = [
        f"🆕 Course #{course['id']}",
        where,
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
    lines += ["", who, "", "✅ Livrée : écris <b>OK</b> · ✏️ autre chose vendu : écris <b>Modif</b>"]
    return "\n".join(lines)


PAYMENT_LABEL = {"especes": "💵 espèces", "virement": "💳 virement"}
PAYMENT_PROMPT = "Le client a payé comment ?"


def _pay(course: dict) -> str:
    label = PAYMENT_LABEL.get(course.get("payment") or "")
    return f" · {label}" if label else ""


def delivered_for_livreur(course: dict) -> str:
    return f"✅ Course #{course['id']} livrée — {eur(course['price'])} encaissés{_pay(course)}."


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
                   "ou tape le prix (ex. 30). Puis ✅ OK.")
    else:
        out.append("➖ / ➕ : quantité (le prix ne change pas tout seul). Touche un produit pour mettre son prix. "
                   "✅ Valider quand c'est bon.")
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
ORDER_EDIT_PRICE_HINT = "Les prix vont de 10 en 10 € : tape seulement le prix de la ligne, par exemple 30."
ORDER_EDIT_USE_BUTTONS = "Utilise les boutons de la commande en cours de modification (➖ ➕ ✅ Valider)."


def order_edit_missing_price(names: list[str]) -> str:
    return "Mets le prix de : " + ", ".join(names)[:180]


def order_edit_off_step(names: list[str]) -> str:
    return "Les prix vont de 10 en 10 € : corrige le prix de " + ", ".join(names)[:150]


def order_modified_for_franchise(course: dict, old_price, editor_name: str) -> str:
    lines = [f"✏️ Course #{course['id']} modifiée par {esc(editor_name)} sur place", "",
             f"🍾 {esc(course['products'])}",
             f"💶 {eur(course['price'])} (avant : {eur(old_price)})"]
    return "\n".join(lines)


def order_modified_for_livreur(course: dict, old_price, editor_name: str) -> str:
    return "\n".join([f"✏️ Course #{course['id']} modifiée par {esc(editor_name)}", "",
                      f"🍾 {esc(course['products'])}",
                      f"💶 {eur(course['price'])} à encaisser (avant : {eur(old_price)})"])


def d_order_modified(course: dict, livreur: dict, old_products: str, old_price) -> str:
    return (
        f"✏️ #{course['id']} — modifiée par {esc(livreur['display_name'])} — "
        f"{eur(old_price)} → {eur(course['price'])}\n"
        f"avant : {esc(old_products)}\n"
        f"après : {esc(course['products'])}"
    )


# ================================================================ rechargement (ravitailleur / dispatch)

RESTOCK_KIND_LABEL = {"load": "📦 Chargement", "unload": "↩️ Reprise", "cash": "💶 Cash seulement", "swipe": "🔁 Swipe"}
RESTOCK_CHOOSE_LIVREUR = "📦 <b>Rechargement</b> — quel livreur ?"
RESTOCK_NO_LIVREUR = "Aucun livreur actif pour l'instant."
RESTOCK_EXPIRED = "Rechargement expiré : relance /recharge."
RESTOCK_EMPTY = "Ajoute au moins un produit ou du cash."
RESTOCK_CASH_HINT = "Tape seulement le montant, par exemple 250."
RESTOCK_USE_BUTTONS = "Utilise les boutons du rechargement en cours (ou ❌ Annuler)."
RESTOCK_CANCELLED = "Rechargement annulé."


def _restock_head(livreur_name: str, kind: str | None = None, box: str | None = None) -> str:
    head = f"📦 <b>Rechargement — {esc(livreur_name)}</b>"
    if kind:
        head += f"\n{RESTOCK_KIND_LABEL[kind]}"
        if box:
            head += f" · {esc(box)}"
    return head


def restock_choose_kind(livreur_name: str) -> str:
    return f"{_restock_head(livreur_name)}\n\nChargement, reprise ou cash seulement ?"


def restock_choose_box(livreur_name: str, kind: str) -> str:
    return f"{_restock_head(livreur_name, kind)}\n\nQuel box ?"


def restock_editor(livreur_name: str, kind: str, box: str | None, items: list[dict], cash: float) -> str:
    lines = [_restock_head(livreur_name, kind, box), ""]
    if kind != "cash":
        sign = "−" if kind == "unload" else "+"
        if items:
            lines += [f"{sign}{i['q']} {esc(i['p'])}" for i in items]
        else:
            lines.append("Aucun produit : ➕ Ajouter un produit.")
    lines += ["", f"💶 Cash récupéré : {eur(cash)}", ""]
    if kind == "cash":
        lines.append("Règle le cash avec les boutons (ou tape le montant), puis ✅ Valider.")
    else:
        lines.append("−1 / +1 / +5 sur les quantités. ✅ Valider quand c'est bon.")
    return "\n".join(lines)


def restock_picker(livreur_name: str, page: int, pages: int) -> str:
    text = f"📦 Rechargement — {esc(livreur_name)} — choisis le produit"
    if pages > 1:
        text += f" (page {page + 1}/{pages})"
    return text


def restock_done(restock: dict, livreur_name: str) -> str:
    from bot.services import restock as rs

    lines = [f"✅ Rechargement #{restock['id']} enregistré — {esc(livreur_name)}",
             RESTOCK_KIND_LABEL[restock["kind"]] + (f" · {esc(restock['box'])}" if restock.get("box") else "")]
    if restock.get("items"):
        lines.append(esc(rs.items_text(restock["items"], restock["kind"])))
    if float(restock.get("cash") or 0) > 0:
        lines.append(f"💶 Cash récupéré : {eur(restock['cash'])}")
    return "\n".join(lines)


def restock_for_livreur(restock: dict) -> str:
    from bot.services import restock as rs

    head = {"load": "📦 Chargement reçu", "unload": "↩️ Stock repris", "cash": "💶 Cash remis"}[restock["kind"]]
    lines = [f"{head} (#R{restock['id']})"]
    if restock.get("items"):
        lines.append(esc(rs.items_text(restock["items"], restock["kind"])))
    if float(restock.get("cash") or 0) > 0:
        lines.append(f"💶 Cash récupéré : {eur(restock['cash'])}")
    return "\n".join(lines)


def d_restock(restock: dict, by: dict, livreur: dict) -> str:
    from bot.services import restock as rs

    text = (f"📦 R#{restock['id']} — {esc(by['display_name'])} → {esc(livreur['display_name'])} — "
            f"{RESTOCK_KIND_LABEL[restock['kind']]}")
    if restock.get("box"):
        text += f" · {esc(restock['box'])}"
    if restock.get("items"):
        text += f"\n{esc(rs.items_text(restock['items'], restock['kind']))}"
    if float(restock.get("cash") or 0) > 0:
        text += f"\n💶 {eur(restock['cash'])}"
    return text


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
    text = f"✅ #{course['id']} — livrée — {esc(livreur['display_name'])} — {eur(course['price'])}{_pay(course)}"
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


CANNOT_BAN_SELF = "Tu ne peux pas te bannir toi-même."

STOCK_MENU = "📦 <b>Stock</b> — que veux-tu voir ?"


def _qty_list(values: dict) -> tuple[list[str], list[str]]:
    have = [f"{esc(p)} <b>{_n(q)}</b>" for p, q in values.items() if float(q or 0) > 0]
    empty = [esc(p) for p, q in values.items() if float(q or 0) <= 0]
    return have, empty


def box_stock(name: str, values: dict) -> str:
    have, _ = _qty_list(values)                  # les produits à 0 ne sont pas cités
    lines = [f"📦 <b>{esc(name)}</b> — stock actuel", ""]
    lines.append(" · ".join(have) if have else "Vide.")
    return "\n".join(lines)


def _low_stock(totals: list[dict]) -> list[str]:
    """Produits sous le seuil qui restent en stock (un produit à 0 n'est pas cité)."""
    out = []
    for t in totals:
        total = float(t.get("total") or 0)
        if str(t.get("statut") or "").upper() in ("ALERTE", "RUPTURE") and str(t.get("produit") or "").strip() \
                and total > 0:
            out.append(f"🟠 {esc(t['produit'])} : {_n(total)} (seuil {_n(t.get('seuil') or 0)})")
    return out


def boxes_overview(boxes: dict, totals: list[dict]) -> str:
    lines = ["📦 <b>Tous les box</b> — stock actuel", ""]
    for name, values in boxes.items():
        have, _ = _qty_list(values)
        lines.append(f"<b>{esc(name)}</b> : {' · '.join(have) if have else 'vide'}")
    low = _low_stock(totals)
    if low:
        lines += ["", "⚠️ <b>Sous le seuil</b> (box + livreurs)"] + low
    return "\n".join(lines)


def livreurs_stock(livreurs: dict, flavors: dict | None = None) -> str:
    lines = ["🚴 <b>Stock chez les livreurs</b>", ""]
    for name, values in livreurs.items():
        have, _ = _qty_list(values)
        if have:                                 # un livreur sans rien n'est pas cité
            lines.append(f"<b>{esc(name)}</b> : {' · '.join(have)}")
            mine = (flavors or {}).get(name, {})
            for product in sorted(mine):         # goûts, seulement pour un produit qu'il a encore
                if any(p == product and float(q or 0) > 0 for p, q in values.items()):
                    lines.append(f"   🍬 {esc(product)} : {esc(', '.join(sorted(mine[product])))}")
    if len(lines) == 2:
        lines.append("Aucun stock chez les livreurs.")
    return "\n".join(lines)


def stock_unavailable(error: str) -> str:
    return f"⚠️ Stock illisible pour l'instant : {esc(error[:200])}"


def name_picker(user: dict) -> str:
    return (f"🏷 Nom dans les feuilles pour {esc(user.get('real_name') or '?')} "
            f"(actuellement « {esc(user.get('display_name') or '?')} ») :\n"
            "C'est ce nom qui est écrit dans Dispatch et Rechargement.")


def name_taken(name: str, holder: dict) -> str:
    return f"« {name} » est déjà pris par {holder.get('real_name') or holder.get('display_name')}."


def name_set_done(user: dict, old: str | None) -> str:
    return f"🏷 {esc(old or '?')} → <b>{esc(user['display_name'])}</b> ({esc(user.get('real_name') or '?')})"


def name_set_for_user(user: dict) -> str:
    return f"🏷 Ton nom est maintenant « {esc(user['display_name'])} »."


def _stock_who(livreur: dict, sheet_name: str) -> str:
    name = livreur.get("display_name") or "Le livreur"
    if sheet_name and sheet_name != name:
        name += f" ({sheet_name})"
    return esc(name)


def _n(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}".replace(".", ",")


def stock_assignment_alert(course: dict, livreur: dict, sheet_name: str,
                           warnings: list[tuple[str, int, float]]) -> str:
    who = _stock_who(livreur, sheet_name)
    lines = [f"⚠️ Stock — course #{course['id']} ({who})"]
    for product, need, have in warnings:
        p = esc(product)
        if have <= 0:
            lines.append(f"• {who} n'a plus de {p} sur lui ({need} commandé{'s' if need > 1 else ''})")
        elif need == have == 1:
            lines.append(f"• C'est le dernier {p} de {who}")
        elif need == have:
            lines.append(f"• Ce sont les {_n(have)} derniers {p} de {who}")
        else:
            lines.append(f"• {who} n'a que {_n(have)} {p} sur lui pour {need} commandé{'s' if need > 1 else ''}")
    return "\n".join(lines)


def stock_empty_alert(course: dict, livreur: dict, sheet_name: str, products: list[str]) -> str:
    who = _stock_who(livreur, sheet_name)
    what = ", ".join(esc(p) for p in products)
    return f"📭 {who} n'a plus de {what} sur lui (course #{course['id']} livrée). Pense à le recharger (/recharge)."


def stock_dispatch_alert(course: dict, short: list[tuple[dict, list[tuple[str, int, float]]]]) -> str:
    """Aucun livreur éligible n'a tout ce que demande la course."""
    lines = [f"⚠️ Course #{course['id']} ({esc(course['district'])}) : aucun livreur en service n'a tout en stock."]
    for livreur, missing in short[:6]:
        what = ", ".join(f"{_n(have)}/{need} {esc(p)}" for p, need, have in missing)
        lines.append(f"• {esc(livreur.get('display_name') or '?')} : {what}")
    lines.append("La course part quand même au plus proche. Pense à recharger (/recharge).")
    return "\n".join(lines)


def arrival_for_franchise(course: dict, livreur: dict, distance_label: str, minutes: int) -> str:
    return (f"📍 {esc(livreur.get('display_name') or 'Le livreur')} arrive — course #{course['id']} : "
            f"à {distance_label} (≈ {minutes} min)\n{course_summary(course)}")


def d_arrival(course: dict, livreur: dict, distance_label: str, minutes: int) -> str:
    return (f"📍 #{course['id']} — {esc(livreur.get('display_name') or '?')} arrive : "
            f"à {distance_label} (≈ {minutes} min) — {esc(course['district'])}")


def ago(minutes: float) -> str:
    """0 → « à l'instant » ; 7 → « il y a 7 min » ; 95 → « il y a 1 h 35 »."""
    m = int(minutes)
    if m < 1:
        return "à l'instant"
    if m < 60:
        return f"il y a {m} min"
    return f"il y a {m // 60} h {m % 60:02d}" if m % 60 else f"il y a {m // 60} h"


def position_state(pos: dict | None, minutes: float | None, fresh: bool) -> str:
    """« 📍 il y a 2 min · en direct », « ⚠️ position fixe (il y a 5 min) »…"""
    if pos is None or minutes is None:
        return "pas de position"
    if not fresh:
        return f"position perdue (dernière {ago(minutes)})"
    if pos.get("live") is False:
        return f"⚠️ position fixe ({ago(minutes)})"
    return f"📍 {ago(minutes)} · en direct"


def livreurs_list(rows: list[tuple]) -> str:
    """rows : (livreur, position fraîche ?, nombre de courses en cours, position, minutes depuis la position)."""
    if not rows:
        return "🚴 Aucun livreur actif."
    lines = ["🚴 <b>Livreurs</b>", ""]
    for lv, located, busy, *where in rows:
        pos, minutes, waiting = (where + [None, None, None])[:3]
        if lv.get("on_duty"):
            if lv.get("duty_forced") and not located:
                state = "🟢 en service · sans position"
            else:
                state = "🟢 en service · " + position_state(pos, minutes, located)
        elif waiting is not None:
            state = f"⏸ pause · /dispo {ago(waiting)}, position pas encore reçue"
        elif pos is None:
            state = "⏸ pause · jamais de position"
        else:
            state = "⏸ pause"
        extra = f" · {busy} course{'s' if busy > 1 else ''} en cours" if busy else ""
        from bot.services.transport import ICON

        extra += f" · {ICON[lv['transport_mode']]}" if lv.get("transport_mode") in ICON else " · ❔"
        lines.append(f"{esc(lv.get('display_name') or '?')} — {state}{extra}")
    lines += ["", "Un livreur mis en service ici reçoit les courses même sans position partagée."]
    return "\n".join(lines)


def duty_on_by_admin(admin: dict) -> str:
    return (f"🟢 {esc(admin.get('display_name') or 'Le dispatch')} t'a mis en service : tu reçois les courses. "
            "Partage ta position en direct pour recevoir d'abord les plus proches. /pause pour arrêter.")


def duty_off_by_admin(admin: dict) -> str:
    return f"⏸ {esc(admin.get('display_name') or 'Le dispatch')} t'a mis en pause. /dispo pour reprendre."


def assign_prompt(course: dict) -> str:
    return f"👤 À quel livreur attribuer la course #{course['id']} ?\n{course_summary(course)}"


def assigned_done(course: dict, livreur: dict) -> str:
    return f"✅ Course #{course['id']} attribuée à {esc(livreur.get('display_name') or '?')}."


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
        f"{esc(course['address'])} · {eur(course['price'])}{_pay(course)}"
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


def users_list(franchises: list[str], livreurs: list[str], pending: list[str],
               ravitailleurs: list[str] | None = None) -> str:
    lines = ["👥 Utilisateurs", "", "<b>Franchisés</b>"]
    lines += franchises or ["—"]
    lines += ["", "<b>Livreurs</b>"]
    lines += livreurs or ["—"]
    if ravitailleurs:
        lines += ["", "<b>Ravitailleurs</b>"]
        lines += ravitailleurs
    lines += ["", "<b>En attente de validation</b>"]
    lines += pending or ["—"]
    return "\n".join(lines)


EXCLURE_HEADER = "⛔ Qui veux-tu bannir ?"
EXCLURE_EMPTY = "Aucun utilisateur actif à bannir."
SUPPRIMER_HEADER = ("🗑 Quel compte supprimer ? (⏳ en attente de validation, ⛔ banni)\n"
                    "Pour empêcher quelqu'un de revenir, utilise plutôt /bannir : supprimé, il peut se réinscrire.")
SUPPRIMER_EMPTY = "Aucun compte à supprimer."
DELETED_NOTICE = "Ton compte a été supprimé. Pour revenir un jour, envoie /start."
REACTIVER_HEADER = "Qui veux-tu réactiver ?"
REACTIVER_EMPTY = "Aucun utilisateur banni."


def ban_confirm(user: dict) -> str:
    return f"Bannir {esc(user.get('display_name') or ROLE_LABEL[user['role']])} ({esc(user.get('real_name'))}) ?"


def banned_done(user: dict) -> str:
    return (f"⛔ {esc(user.get('display_name'))} ({esc(user.get('real_name'))}) est banni."
            + _wiped(user))


def _wiped(user: dict) -> str:
    n = user.get("wiped") or 0
    return f"\n🧹 {plural(n, 'message')} du bot effacé{'s' if n > 1 else ''} chez lui." if n else ""


def delete_confirm(user: dict) -> str:
    name = esc(user.get("display_name") or ROLE_LABEL[user["role"]])
    return (f"🗑 Supprimer définitivement {name} ({esc(user.get('real_name'))}) ?\n\n"
            "• il n'a plus accès au bot, ses courses en cours sont rendues ou annulées ;\n"
            "• les messages du bot des dernières 48 h sont effacés chez lui ;\n"
            "• son nom dans les feuilles est libéré ;\n"
            "• l'historique (récap, journal, feuilles) garde ses anciennes courses ;\n"
            "• il pourra se réinscrire de zéro avec /start, et tu devras le valider.")


def deleted_done(user: dict) -> str:
    return f"🗑 {esc(user.get('display_name'))} est supprimé." + _wiped(user)


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


def sheets_synced(total: int, added: int, restocks: int = 0, expenses: int = 0, sold: int = 0) -> str:
    if total == 0 and restocks == 0 and expenses == 0 and sold == 0:
        return "📗 Aucune course livrée ni rechargement cette nuit : rien à envoyer."
    what = [plural(total, "course")] if total else []
    if restocks:
        what.append(plural(restocks, "rechargement"))
    if expenses:
        what.append(plural(expenses, "dépense"))
    if sold:
        what.append(plural(sold, "vente saisie") if sold == 1 else f"{sold} ventes saisies")
    return (f"📗 Google Sheets à jour : {added} ajout{'s' if added > 1 else ''} sur "
            f"{', '.join(what[:-1]) + ' et ' + what[-1] if len(what) > 1 else what[0]} de la nuit.")


def sheets_failed(error: str) -> str:
    return f"⚠️ Envoi à Google Sheets impossible : {esc(error[:200])}"


# ================================================================ caisse : dépenses et cash des livreurs

EXPENSE_KIND_LABEL = {"charges": "🧾 Charges (à ses frais)", "paye": "💸 Avance sur paye"}
EXPENSE_CHOOSE_LIVREUR = "🧾 <b>Dépense</b> — pour quel livreur ?"
EXPENSE_EXPIRED = "Dépense expirée : relance /depense."
EXPENSE_CANCELLED = "Dépense annulée."
EXPENSE_ZERO = "Indique d'abord un montant."
EXPENSE_AMOUNT_HINT = "Tape seulement le montant, par exemple 35 ou 12,50."
EXPENSE_USE_BUTTONS = "Utilise les boutons de la dépense en cours (ou ❌ Annuler)."
CASH_NOT_LINKED = "La caisse se lit dans Google Sheets, qui n'est pas relié."


def _expense_head(payload: dict) -> str:
    head = f"🧾 <b>Dépense — {esc(payload.get('livreur_name') or '?')}</b>"
    if payload.get("kind"):
        head += f"\n{EXPENSE_KIND_LABEL[payload['kind']]}"
    return head


def expense_step(payload: dict) -> str:
    step = payload.get("step")
    head = _expense_head(payload)
    if step == "kind":
        return (f"{head}\n\nQuel type ?\n• Charges : essence, repas, parking… à ses frais\n"
                "• Paye : avance sur sa paye")
    amount = f"💶 Montant : <b>{eur(payload.get('amount') or 0)}</b>"
    if step == "amount":
        return f"{head}\n\n{amount}\n\nRègle le montant avec les boutons ou tape-le (ex : 35), puis ➡️ Suivant."
    motif = payload.get("motif") or "—"
    if step == "motif":
        return f"{head}\n\n{amount}\n\nMotif ? Choisis ou tape-le (facultatif)."
    return f"{head}\n\n{amount}\n📝 Motif : {esc(motif)}\n\nTout est bon ?"


def _expense_line(expense: dict) -> str:
    text = f"{EXPENSE_KIND_LABEL[expense['kind']]} — {eur(expense['amount'])}"
    if expense.get("motif"):
        text += f" — {esc(expense['motif'])}"
    return text


def expense_done(expense: dict, livreur_name: str) -> str:
    return f"✅ Dépense #D{expense['id']} enregistrée — {esc(livreur_name)}\n{_expense_line(expense)}"


def expense_for_livreur(expense: dict, by: dict) -> str:
    return (f"🧾 Dépense notée pour toi par {esc(by.get('display_name') or '?')} (#D{expense['id']})\n"
            f"{_expense_line(expense)}")


def d_expense(expense: dict, by: dict, livreur: dict) -> str:
    who = esc(livreur["display_name"])
    if by["id"] != livreur["id"]:
        who = f"{esc(by['display_name'])} → {who}"
    return f"🧾 D#{expense['id']} — {who} — {_expense_line(expense)}"


def _cash_detail(row: dict) -> str:
    parts = [f"espèces {eur(row['especes'])}"] if row.get("especes") else []
    if row.get("depenses"):
        parts.append(f"− dépenses {eur(row['depenses'])}")
    if row.get("recupere"):
        parts.append(f"− récupéré {eur(row['recupere'])}")
    if row.get("en_vol"):
        sign = "+" if row["en_vol"] > 0 else "−"
        parts.append(f"{sign} {eur(abs(row['en_vol']))} pas encore dans la feuille")
    return " ".join(parts)


def cash_overview(rows: list[dict]) -> str:
    lines = ["💶 <b>Caisse des livreurs</b> — cash à récupérer", ""]
    total = 0.0
    shown = [r for r in rows if any(abs(float(r.get(k) or 0)) >= 0.01
                                    for k in ("cash", "especes", "depenses", "recupere", "en_vol", "virement"))]
    for row in shown:                            # un livreur sans aucun mouvement n'est pas cité
        total += max(0.0, row["cash"])
        lines.append(f"<b>{esc(row['nom'])}</b> : {eur(row['cash'])}")
        detail = _cash_detail(row)
        if detail:
            lines.append(f"   {detail}")
        if row.get("virement"):
            lines.append(f"   💳 virements : {eur(row['virement'])}")
    if not shown:
        lines.append("Rien à récupérer : aucun mouvement de cash cette semaine.")
    lines += ["", f"Total à récupérer : <b>{eur(total)}</b>"]
    return "\n".join(lines)


def my_cash(row: dict) -> str:
    lines = [f"💶 <b>Ta caisse — {esc(row['nom'])}</b>", ""]
    if row.get("especes"):
        lines.append(f"Espèces encaissées : {eur(row['especes'])}")
    if row.get("depenses"):
        lines.append(f"Dépenses et avances : − {eur(row['depenses'])}")
    if row.get("recupere"):
        lines.append(f"Déjà remis : − {eur(row['recupere'])}")
    if row.get("en_vol"):
        sign = "+" if row["en_vol"] > 0 else "−"
        lines.append(f"Pas encore dans la feuille : {sign} {eur(abs(row['en_vol']))}")
    lines += ["", f"<b>À remettre : {eur(row['cash'])}</b>"]
    if row.get("virement"):
        lines.append(f"💳 Virements (pour info) : {eur(row['virement'])}")
    return "\n".join(lines)


def cash_unavailable(error: str) -> str:
    return f"⚠️ Caisse illisible pour l'instant : {esc(error[:200])}"


# ================================================================ reset de la semaine (/reset)

CLOTURE_CANCELLED = "Reset annulé : rien n'a changé."
CLOTURE_RUNNING = "Reset déjà en cours…"
CLOTURE_ALREADY = "La semaine vient déjà d'être remise à zéro."
CLOTURE_IN_PROGRESS = "⏳ Reset en cours : copie des 3 fichiers dans Drive, puis remise à zéro… (1 à 3 min)"
CLOTURE_REMINDER = ("🗓 Nouvelle semaine : pense à /reset — archive des 3 fichiers dans Drive, stock des box "
                    "reporté, onglets Lundi → Dimanche remis à zéro.")


def _qty_text(values: dict) -> str:
    return " · ".join(f"{_n(q)} {esc(p)}" for p, q in values.items() if q)


def cloture_precheck(open_courses: int, livreurs_stock: dict, cash_rows: list[dict], ravi: float | None,
                     tab: str) -> str:
    lines = ["🗓 <b>Reset de la semaine</b>", "",
             "1. Copie des 3 fichiers dans Google Drive (dossier « Archives bot »).",
             "2. Le stock actuel des box devient le stock initial (COMPTA, onglet STOCK).",
             "3. Les onglets Lundi → Dimanche sont vidés (commandes, dépenses, rechargements), "
             "ainsi que MOUVEMENTS et RAVI.",
             f"4. Le stock encore chez les livreurs est reporté (onglet {esc(tab)} du Rechargement).", ""]
    carried = [(name, values) for name, values in livreurs_stock.items() if any(q for q in values.values())]
    if carried:
        lines.append("📦 Reporté :")
        lines += [f"• {esc(name)} : {_qty_text(values)}" for name, values in carried]
    else:
        lines.append("📦 Aucun stock chez les livreurs.")
    unpaid = [r for r in cash_rows if abs(r["cash"]) >= 0.01]
    if unpaid:
        lines += ["", "⚠️ Cash pas encore récupéré (remis à zéro — récupère-le avant avec /caisse) :"]
        lines += [f"• {esc(r['nom'])} : {eur(r['cash'])}" for r in unpaid]
    if ravi is not None and abs(ravi) >= 0.01:
        lines += ["", f"⚠️ Reste chez le ravitailleur : {eur(ravi)} (remis à zéro, garde-en trace)"]
    if open_courses:
        lines += ["", f"⚠️ {plural(open_courses, 'course')} en attente ou en cours : elles seront écrites "
                      "dans la nouvelle semaine une fois livrées."]
    lines += ["", "L'archive garde tout. Remettre à zéro maintenant ?"]
    return "\n".join(lines)


def cloture_done(result: dict, tab: str) -> str:
    lines = ["✅ <b>Semaine remise à zéro</b>", "",
             f"🗄 Archive : <a href=\"{esc(result.get('archive') or '')}\">{esc(result.get('dossier') or 'Drive')}</a>"]
    initial = {box: values for box, values in (result.get("initial") or {}).items()}
    if initial:
        lines.append("📦 Stock initial des box :")
        lines += [f"• {esc(box)} : {_qty_text(values) or 'vide'}" for box, values in initial.items()]
    reports = result.get("reports") or {}
    if reports:
        lines.append(f"🚴 Reporté chez les livreurs (onglet {esc(tab)}) :")
        lines += [f"• {esc(name)} : {_qty_text(values)}" for name, values in reports.items()]
    return "\n".join(lines)


def cloture_failed(error: str) -> str:
    return (f"⚠️ Reset impossible : {esc(error[:250])}\n\n"
            "Si la copie dans Drive a échoué, rien n'a été effacé. Sinon, l'archive est dans le dossier "
            "« Archives bot » de Drive.")


def cloture_unavailable(error: str) -> str:
    return f"⚠️ Reset impossible pour l'instant, feuilles illisibles : {esc(error[:200])}"


# ================================================================ débrief de la journée (/close)

def day_debrief(label: str, *, delivered: int, total: float, pay: dict, cancelled: int, on_site: int,
                still_open: int, livreurs: list, franchises: list, charges: float, payes: float, restocks: int,
                recovered: float, cash_rows: list[dict] | None, alerts: list[dict] | None,
                sheets_on: bool, moves: list | None = None, swipes: int = 0) -> str:
    from bot.services.transport import DETECTED_ICON, ICON

    lines = [f"🔒 <b>Journée close — nuit du {esc(label)}</b>", ""]
    if delivered:
        lines.append(f"📦 <b>{plural(delivered, 'course')} livrée{'s' if delivered > 1 else ''} — {eur(total)}</b>")
        modes = [f"{icon} {label} {eur(pay[k])}" for k, icon, label in
                 (("especes", "💵", "espèces"), ("virement", "💳", "virement"), ("", "❔", "sans mode")) if pay.get(k)]
        if modes:
            lines.append("   " + " · ".join(modes))
    else:
        lines.append("📦 Aucune course livrée.")
    if cancelled or on_site:
        lines.append(f"   ❌ {cancelled} annulée{'s' if cancelled > 1 else ''} · "
                     f"🚪 {on_site} annulée{'s' if on_site > 1 else ''} sur place")
    if still_open:
        lines.append(f"   ⚠️ {plural(still_open, 'course')} encore ouverte{'s' if still_open > 1 else ''} (/encours)")

    if livreurs:
        lines += ["", "🚴 <b>Par livreur</b>"]
        for name, s in livreurs:
            icon = f" {ICON[s['mode']]}" if s.get("mode") in ICON else ""
            text = f"• {esc(name)}{icon}"
            if s["n"]:
                split = [f"{i} {eur(s[k])}" for k, i in (("especes", "💵"), ("virement", "💳")) if s[k]]
                text += f" — {plural(s['n'], 'course')} — {eur(s['total'])}" + (f" ({' · '.join(split)})" if split else "")
            if s["depenses"]:
                text += f" — 🧾 {eur(s['depenses'])}"
            lines.append(text)
    if franchises:
        lines += ["", "🏪 <b>Par franchisé</b>"]
        lines += [f"• {esc(name)} — {plural(s['n'], 'course')} — {eur(s['total'])}" for name, s in franchises]

    if moves:
        lines += ["", "🚦 <b>Déplacements détectés</b>"]
        for course_id, name, detected, declared in moves:
            wrong = (detected == "metro" and declared in ("deux_roues", "voiture")) or \
                    (detected == "vehicule" and declared == "transport")
            said = f" · déclaré {ICON[declared]}" if declared in ICON else ""
            lines.append(f"{'⚠️' if wrong else '•'} #{course_id} {esc(name)} : {DETECTED_ICON[detected]}"
                         f" {({'metro': 'métro', 'vehicule': 'véhicule', 'pied': 'à pied'})[detected]}{said}")

    extra = []
    if charges or payes:
        split = [f"{label} {eur(v)}" for label, v in (("charges", charges), ("payes", payes)) if v]
        extra.append(f"🧾 Dépenses : {eur(charges + payes)} ({' · '.join(split)})")
    if restocks or recovered:
        extra.append(f"📦 Rechargements : {restocks}" + (f" — cash récupéré {eur(recovered)}" if recovered else ""))
    if extra:
        lines += [""] + extra
    if swipes:
        lines.append(f"🔁 Swipes entre livreurs : {swipes}")

    if sheets_on:
        lines += ["", "💶 <b>Cash à récupérer</b> (semaine)"]
        if cash_rows is None:
            lines.append("   illisible pour l'instant (/caisse)")
        elif cash_rows:
            lines += [f"• {esc(r['nom'])} : {eur(r['cash'])}" for r in cash_rows]
        else:
            lines.append("   rien, tout est récupéré ✅")
        low = _low_stock(alerts or [])
        if low:
            lines += ["", "⚠️ <b>Stock sous le seuil</b>"] + low
    lines += ["", "✅ Journée terminée. Bon repos !"]
    return "\n".join(lines)


# ================================================================ moyen de déplacement

def transport_set(mode: str) -> str:
    from bot.services.transport import ICON, LABEL

    return f"{ICON[mode]} {LABEL[mode]} noté."


def transport_mismatch(course: dict, livreur: dict, mode: str, detail: dict) -> str:
    from bot.services.transport import ICON, LABEL

    declared = livreur.get("transport_mode")
    said = f"{ICON[declared]} {LABEL[declared].lower()}" if declared else "?"
    who = esc(livreur.get("display_name") or "?")
    if mode == "metro":
        where = ""
        if detail.get("from") and detail.get("to"):
            where = f" ({esc(detail['from'])} → {esc(detail['to'])}"
            where += f", {_n(detail['km'])} km en {detail['minutes']} min)"
        return (f"🚇 #{course['id']} — {who} semble avoir pris le métro{where} · déclaré {said}")
    return f"🛵 #{course['id']} — {who} semble rouler (≈ {detail.get('kmh', '?')} km/h) · déclaré {said}"


# ================================================================ positions des livreurs (dispatch)

NO_POSITION = "Aucune position connue pour ce livreur."
NO_POSITIONS = "Aucun livreur en service n'a de position."


def venue_title(livreur: dict, minutes: float) -> str:
    from bot.services.transport import ICON

    icon = ICON.get(livreur.get("transport_mode") or "", "🚴")
    return f"{icon} {livreur.get('display_name') or '?'} — {ago(minutes)}"[:100]


def venue_address(livreur: dict, pos: dict, address: str | None, fresh: bool) -> str:
    where = f"près de {address}" if address else f"{pos['lat']:.5f}, {pos['lon']:.5f}"
    if not fresh:
        share = "position perdue"
    elif pos.get("live") is False:
        share = "⚠️ position fixe"
    else:
        share = "✅ en direct"
        until = pos.get("live_until")
        if until:
            from bot.timeutil import hhmm, parse_ts

            share += f" jusqu'à {hhmm(parse_ts(until))}"
    duty = "🟢 en service" if livreur.get("on_duty") else "⏸ pause"
    return f"{where} · {share} · {duty}"[:200]


def d_position_silent(livreur: dict, minutes: float, address: str | None, off_after: int) -> str:
    where = f" (dernière : près de {esc(address)})" if address else ""
    return (f"⚠️ {esc(livreur.get('display_name') or '?')} : plus de position depuis {int(minutes)} min{where}. "
            f"Il reste en service ; sans nouvelle, il en sortira à {off_after} min.")


def d_position_lost(livreur: dict, minutes: int) -> str:
    return f"⏸ {esc(livreur.get('display_name') or '?')} retiré du service : plus de position depuis {minutes} min."


def d_static_position(livreur: dict) -> str:
    return (f"⚠️ {esc(livreur.get('display_name') or '?')} a envoyé une position fixe : elle ne se mettra pas à "
            "jour. Demande-lui de partager sa position en direct.")


def d_dispo_without_position(livreur: dict, minutes: int) -> str:
    return (f"⚠️ {esc(livreur.get('display_name') or '?')} a fait /dispo il y a {minutes} min mais n'a pas partagé "
            "sa position : il n'est pas en service. Je lui ai renvoyé la marche à suivre.")


# ================================================================ récap de ventes (/ventes)

SALES_HELP = (
    "🧾 <b>Récap des ventes</b> — envoie-le ici, un bloc par livreur :\n\n"
    "<code>Livreur A\n"
    "2 US 60\n"
    "CB 1 DIV 30\n\n"
    "Livreur B\n"
    "3 MSX 90</code>\n\n"
    "Chaque ligne : quantité, produit, prix total de la ligne (de 10 en 10 €, ou 0 si c'est offert). "
    "<b>Espèces par défaut</b> ; sinon écris « CB » ou « virement » au début de la ligne. "
    "Je te montre le récap avant de remplir le tableau.\n\n"
    "En retard d'un jour ? <b>/ventes lundi</b> (ou /ventes hier) : les ventes vont dans l'onglet de ce jour-là."
)
SALES_EMPTY = "Je n'ai trouvé aucune vente dans ton message.\n\n" + SALES_HELP
SALES_EXPIRED = "Récap expiré : renvoie /ventes."
SALES_CANCELLED = "Récap annulé : rien n'a été ajouté."


def sales_help(tab: str) -> str:
    """/ventes lundi : l'exemple, avec l'onglet choisi."""
    return SALES_HELP.replace("🧾 <b>Récap des ventes</b>", f"🧾 <b>Récap des ventes — onglet {esc(tab)}</b>", 1)


def sales_bad_day(arg: str) -> str:
    return (f"Je ne connais pas le jour « {esc(arg)} ». Écris par exemple /ventes lundi, /ventes dimanche "
            "ou /ventes hier (puis le récap).")


def sales_errors(errors: list[str]) -> str:
    lines = ["⚠️ Je n'ai pas tout compris, rien n'est ajouté :"]
    lines += [f"• {esc(e)}" for e in errors[:10]]
    lines += ["", "Corrige et renvoie le récap entier (ou /ventes pour revoir l'exemple)."]
    return "\n".join(lines)


def sales_preview(blocks: list[dict], tab: str, done: bool = False) -> str:
    head = "🧾 <b>Ventes ajoutées" if done else "🧾 <b>Ventes à ajouter"
    lines = [f"{head} — onglet {esc(tab)}</b>"]
    totals = {"especes": 0.0, "virement": 0.0}
    n = 0
    for b in blocks:
        lines += ["", f"<b>{esc(b['livreur'])}</b>"]
        for line in b["lines"]:
            icon = "💵" if line["pay"] == "especes" else "💳"
            price = "🎁 offert" if float(line["x"]) == 0 else eur(line["x"])
            lines.append(f"{icon} {line['q']} {esc(line['p'])} — {price}")
            totals[line["pay"]] += float(line["x"])
            n += 1
    parts = [f"{icon} {label} {eur(totals[k])}" for k, icon, label in
             (("especes", "💵", "espèces"), ("virement", "💳", "virement")) if totals[k]]
    lines += ["", f"Total : {' · '.join(parts) or '🎁 tout offert'} — {plural(n, 'ligne')}"]
    if not done:
        lines += ["", "Tout est bon ?"]
    return "\n".join(lines)


def sales_done(count: int, tab: str, added: int, error: str | None) -> str:
    if error:
        return (f"⚠️ {plural(count, 'vente')} enregistrée{'s' if count > 1 else ''}, mais le tableau n'a pas pu "
                f"être rempli : {esc(error[:200])}\n/synchro les renverra.")
    return f"✅ {plural(count, 'vente')} ajoutée{'s' if count > 1 else ''} au tableau (onglet {esc(tab)})."


# ================================================================ rechargement en texte (/ravi)

RAVI_WHO = "Précise le ravitailleur : /ravi 1 ou /ravi 2 (puis le livreur, le box et les produits)."
RAVI_EXPIRED = "Rechargement expiré : renvoie /ravi."
RAVI_CANCELLED = "Rechargement annulé : rien n'a été enregistré."


def ravi_unknown(arg: str) -> str:
    return f"Ravitailleur inconnu « {esc(arg)} ». Écris /ravi 1 ou /ravi 2."


def ravi_help(ravi: str) -> str:
    return (f"📦 <b>Rechargement — {esc(ravi)}</b> — envoie-le ici, un bloc par livreur :\n\n"
            "<code>Livreur A\n"
            "Box 1\n"
            "12 DIV\n"
            "6 US\n"
            "-2 MSX\n"
            "cash 300</code>\n\n"
            "• une ligne par produit : quantité puis produit (chargé au livreur) ;\n"
            "• avec un « - » devant : repris au livreur (retourne dans le box) ;\n"
            "• « cash 300 » : cash récupéré (facultatif) ;\n"
            "• goûts : « 12 MSX banane fraise » (ou « 12 MSX (5 banane, 7 fraise) ») — cochés chez le livreur, "
            "la compta ne voit que 12 MSX ;\n"
            "• le box reste le même pour les livreurs suivants, sauf si tu en écris un autre.\n"
            "Je te montre le récap avant d'enregistrer.")


def ravi_errors(errors: list[str]) -> str:
    lines = ["⚠️ Je n'ai pas tout compris, rien n'est enregistré :"]
    lines += [f"• {esc(e)}" for e in errors[:10]]
    lines += ["", "Corrige et renvoie le rechargement entier."]
    return "\n".join(lines)


def _ravi_blocks(blocks: list[dict]) -> list[str]:
    lines = []
    for b in blocks:
        head = f"<b>{esc(b['livreur'])}</b>" + (f" · {esc(b['box'])}" if b.get("box") else "")
        lines += ["", head]
        if b["load"]:
            lines.append("📦 " + " · ".join(f"+{i['q']} {esc(i['p'])}" + (f" ({esc(', '.join(i['v']))})" if i.get("v") else "")
                                            for i in b["load"]))
        if b["unload"]:
            lines.append("↩️ " + " · ".join(f"−{i['q']} {esc(i['p'])}" for i in b["unload"]))
        if b.get("cash"):
            lines.append(f"💶 cash récupéré {eur(b['cash'])}")
    return lines


def ravi_preview(ravi: str, blocks: list[dict], warnings: list[str] | None = None) -> str:
    lines = [f"📦 <b>Rechargement — {esc(ravi)}</b>"] + _ravi_blocks(blocks)
    if warnings:
        lines += [""] + [f"⚠️ {esc(w)}" for w in warnings[:10]]
    return "\n".join(lines + ["", "Tout est bon ?"])


def ravi_done(ravi: str, blocks: list[dict], count: int) -> str:
    return "\n".join([f"✅ <b>Rechargement enregistré — {esc(ravi)}</b>"] + _ravi_blocks(blocks)
                     + ["", f"{plural(count, 'ligne')} envoyée{'s' if count > 1 else ''} au tableau Rechargement ; "
                            "les livreurs sont prévenus."])


# ================================================================ transfert entre livreurs (/swipe)

SWIPE_HELP = (
    "🔁 <b>Swipe — transfert entre livreurs</b> — envoie-le ici, un bloc par transfert :\n\n"
    "<code>Livreur A &gt; Livreur B\n"
    "3 DIV\n"
    "2 MSX</code>\n\n"
    "• 1re ligne : celui qui donne &gt; celui qui reçoit ;\n"
    "• puis une ligne par produit : quantité puis produit ;\n"
    "• ou « tout » : tout ce que le livreur a encore d'après le tableau ;\n"
    "• « cash 350 » : cash remis à l'autre livreur (avec ou sans produits).\n"
    "Les box ne bougent pas. Je te montre le récap avant d'enregistrer."
)
SWIPE_EXPIRED = "Swipe expiré : renvoie /swipe."
SWIPE_CANCELLED = "Swipe annulé : rien n'a été enregistré."


def swipe_short(livreur: str, product: str, want: int, have: float) -> str:
    return f"⚠️ {esc(livreur)} n'a que {have:g} {esc(product)} d'après le tableau (tu en transfères {want})"


def swipe_errors(errors: list[str]) -> str:
    lines = ["⚠️ Je n'ai pas tout compris, rien n'est enregistré :"]
    lines += [f"• {esc(e)}" for e in errors[:10]]
    lines += ["", "Corrige et renvoie le swipe entier."]
    return "\n".join(lines)


def _swipe_blocks(blocks: list[dict]) -> list[str]:
    lines = []
    for b in blocks:
        lines += ["", f"<b>{esc(b['src'])}</b> ➜ <b>{esc(b['dst'])}</b>" + (" (tout)" if b.get("all") else "")]
        if b["items"]:
            lines.append("🔁 " + " · ".join(f"{i['q']} {esc(i['p'])}" for i in b["items"]))
        if b.get("cash"):
            lines.append(f"💶 cash {eur(b['cash'])}")
    return lines


def swipe_preview(blocks: list[dict], warnings: list[str]) -> str:
    lines = ["🔁 <b>Swipe — transfert entre livreurs</b>"] + _swipe_blocks(blocks)
    if warnings:
        lines += [""] + warnings[:10]
    return "\n".join(lines + ["", "Tout est bon ?"])


def swipe_done(blocks: list[dict], short: bool = False) -> str:
    lines = ["✅ <b>Swipe enregistré</b>"] + _swipe_blocks(blocks)
    if not short:
        lines += ["", "Ajouté au tableau Rechargement (une ligne par transfert, box « Swipe ») ; les livreurs sont prévenus."]
    return "\n".join(lines)


def _swipe_what(block: dict, sign: str) -> str:
    lines = []
    if block["items"]:
        lines.append(esc(", ".join(f"{sign}{i['q']} {i['p']}" for i in block["items"])))
    if block.get("cash"):
        lines.append(f"💶 {sign}{eur(block['cash'])} de cash")
    return "\n".join(lines)


def swipe_for_giver(block: dict) -> str:
    return f"🔁 Swipe : tu donnes à {esc(block['dst'])}\n{_swipe_what(block, '−')}"


def swipe_for_receiver(block: dict) -> str:
    return f"🔁 Swipe : tu reçois de {esc(block['src'])}\n{_swipe_what(block, '+')}"


# ================================================================ prénoms des livreurs (/prenom)

PRENOM_HELP = (
    "🏷 <b>Prénoms des livreurs</b> — le bot écrit partout « Livreur A (Ketur) ».\n\n"
    "<code>/prenom C Layla</code> — donne (ou corrige) le prénom de Livreur C\n"
    "<code>/prenom C -</code> — revient au prénom donné à l'inscription\n"
    "/prenom — la liste\n\n"
    "Par défaut, c'est le prénom donné à l'inscription. Tu peux aussi écrire le prénom à la place du nom "
    "dans /ventes, /ravi et /swipe."
)


def prenoms_list(rows: list[tuple[str, str | None]]) -> str:
    lines = ["🏷 <b>Prénoms des livreurs</b>", ""]
    lines += [f"• {esc(name)}" + ("" if p else " — <i>pas de prénom</i>") for name, p in rows]
    lines += ["", "<code>/prenom C Layla</code> pour en donner ou en corriger un."]
    return "\n".join(lines)


def prenom_done(name: str, prenom: str | None) -> str:
    if prenom:
        return f"✅ C'est noté : <b>{esc(name)}</b>"
    return f"✅ {esc(name)} n'a plus de prénom affiché."


# ================================================================ le franchisé choisit le livreur

def franchise_pick(course: dict, timeout_minutes: int) -> str:
    lines = [f"✅ Course #{course['id']} enregistrée — <b>à quel livreur l'envoyer ?</b>", course_summary(course), "",
             "🟢 libre · 🛵 déjà en livraison (il la reçoit tout de suite et la fera juste après)"]
    if timeout_minutes > 0:
        lines.append(f"Sans choix dans {timeout_minutes} min, je l'envoie au plus proche.")
    return "\n".join(lines)


def livreur_busy_for_franchise(course: dict, livreur: dict, current: dict) -> str:
    return (f"⚠️ {esc(livreur.get('display_name') or '?')} est déjà en livraison (course #{current['id']}) : "
            f"ta course #{course['id']} lui est quand même envoyée, il la fera juste après.")


def queued_for_livreur(course: dict, current: dict) -> str:
    return (f"⏳ La course #{course['id']} passe après ta livraison en cours (#{current['id']}) : "
            "je te la rappelle dès que tu la valides.")


def next_course_for_livreur(course: dict, franchise: dict) -> str:
    return "🔔 <b>Course suivante, à faire maintenant</b>\n\n" + full_fiche(course, franchise)


def pick_timeout(course_id: int) -> str:
    return f"⏱ Pas de livreur choisi pour la course #{course_id} : je l'envoie au plus proche."


# ================================================================ validation « OK » / « Modif » du livreur

def finish_first(course_id: int) -> str:
    return f"Valide d'abord ta course en cours (#{course_id}) : écris OK (ou Modif) quand elle est livrée."


def course_validated(course: dict) -> str:
    return f"✅ Course #{course['id']} validée — {eur(course['price'])}{_pay(course)}."


def edit_sent_ask_payment(course: dict, franchise_name: str) -> str:
    return (f"✏️ Modification de la course #{course['id']} envoyée à {esc(franchise_name)} : c'est lui qui valide.\n\n"
            f"{PAYMENT_PROMPT} (le choix valide la livraison)")


def edit_sent(course_id: int, franchise_name: str) -> str:
    return f"✏️ Modification de la course #{course_id} envoyée à {esc(franchise_name)} : il doit la valider."


def edit_request(course: dict, livreur: dict, pending: dict) -> str:
    return "\n".join([
        f"✏️ <b>{esc(livreur.get('display_name') or 'Le livreur')} a modifié la course #{course['id']}</b>",
        f"📍 {esc(course['address'])}", "",
        f"Avant : {esc(course.get('products') or '?')} — {eur(course['price'])}",
        f"Après : <b>{esc(pending['products'])} — {eur(pending['price'])}</b>", "",
        "Tu valides ? (c'est toi qui as le dernier mot)",
    ])


def edit_decided(course_id: int, accepted: bool, pending: dict, by: str) -> str:
    if accepted:
        return f"✅ Modification de la course #{course_id} validée par {esc(by)} : {esc(pending['products'])} — {eur(pending['price'])}."
    return f"❌ Modification de la course #{course_id} refusée par {esc(by)} : la commande reste comme avant."


EDIT_ALREADY_DECIDED = "Cette modification a déjà été traitée."


def _ago(minutes: int) -> str:
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


def edit_reminder(course: dict, livreur: dict, pending: dict, minutes: int) -> str:
    return "\n".join([
        f"⏰ <b>Rappel : modification de la course #{course['id']} en attente depuis {_ago(minutes)}</b>",
        f"Par {esc(livreur.get('display_name') or 'le livreur')} — 📍 {esc(course['address'])}", "",
        f"Avant : {esc(course.get('products') or '?')} — {eur(course['price'])}",
        f"Après : <b>{esc(pending['products'])} — {eur(pending['price'])}</b>", "",
        "Tant que tu n'as pas décidé, la course n'est pas dans le tableau. Tu valides ?",
    ])


def d_edit_waiting(course: dict, franchise: dict, minutes: int) -> str:
    return (f"⏰ #{course['id']} — modification du livreur sans réponse de "
            f"{esc(franchise.get('display_name') or 'son franchisé')} depuis {_ago(minutes)} : "
            "la course n'est pas encore dans le tableau.")


# ================================================================ goûts des produits (/gouts)

VARIANTS_HELP = (
    "🍬 <b>Goûts des produits</b> — même coût, même produit pour la compta.\n\n"
    "<code>/gouts MSX noisette, fraise, orange, banane</code> — la liste des goûts de MSX\n"
    "<code>/gouts MSX -</code> — plus de goûts pour MSX\n"
    "/gouts — les goûts et ce qu'il reste chez chaque livreur\n\n"
    "Les livreurs cochent ce qu'ils ont avec /gouts ; /ravi « 12 MSX banane fraise » les coche aussi."
)


def variants_set(product: str, variants: list[str]) -> str:
    if not variants:
        return f"🍬 {esc(product)} n'a plus de goûts."
    return f"🍬 Goûts de <b>{esc(product)}</b> : {esc(', '.join(variants))}"


def variants_overview(products: list[dict], flavors: dict, admin: bool) -> str:
    if not products:
        return VARIANTS_HELP if admin else "🍬 Aucun goût défini pour l'instant."
    lines = ["🍬 <b>Goûts — ce qu'il reste chez les livreurs</b>"]
    for p in products:
        lines += ["", f"<b>{esc(p['name'])}</b> : {esc(', '.join(p['variants']))}"]
        for name in sorted(flavors):
            have = [v for v in p["variants"] if v in flavors[name].get(p["name"], set())]
            if have:
                lines.append(f"• {esc(name)} : {esc(', '.join(have))}")
    if admin:
        lines += ["", "<code>/gouts MSX noisette, fraise</code> pour changer une liste."]
    return "\n".join(lines)


def my_variants(products: list[dict]) -> str:
    if not products:
        return "🍬 Aucun goût défini pour l'instant."
    return ("🍬 <b>Tes goûts</b> — touche un goût pour le cocher (✅ tu en as encore) "
            "ou le décocher (tu n'en as plus).")

