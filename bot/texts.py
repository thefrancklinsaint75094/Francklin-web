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
    "quand tu termines pour enchaîner."
)

WELCOME_DISPATCH = (
    "Dispatch actif.\n\n"
    "/recap — totaux par livreur\n"
    "/journal — détail des courses livrées (envoyé automatiquement à 6h)\n"
    "/encours — courses en attente et en cours\n"
    "/users — tous les utilisateurs\n"
    "/exclure — retirer un accès\n"
    "/reactiver — rendre un accès"
)

WELCOME_FRANCHISE_RULES = (
    "✅ Tu es validé.\n\n"
    "Envoie-moi tes commandes en texte. Je te renvoie une fiche à confirmer, "
    "puis je trouve le livreur le plus proche.\n\n"
    "Il me faut au minimum : l'adresse, les produits et le prix. "
    "Le plus sûr : sépare-les par des virgules, avec le code postal et le signe €.\n"
    "Exemple : 12 rue de Rivoli 75004, 2 vodka + coca, 60€, digicode 45A32\n\n"
    "/mescourses — voir mes courses de la nuit"
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


def order_lines(data: dict, price_suffix: str = "") -> list[str]:
    """Bloc 📍 🔑 🍾 💶 🕐 commun à la fiche franchisé et à la fiche complète livreur."""
    lines = [f"📍 {esc(data['address'])}"]
    if data.get("address_detail"):
        lines.append(f"🔑 {esc(_cap(data['address_detail']))}")
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
    lines += order_lines(course, price_suffix=" à encaisser")
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
