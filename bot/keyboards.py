"""Construction des boutons inline. callback_data : préfixe court + identifiant (≤ 64 octets)."""
from __future__ import annotations

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

from bot.texts import ROLE_LABEL


def role_choice() -> M:
    return M([[B("Franchisé", callback_data="role:franchise"), B("Livreur", callback_data="role:livreur")]])


def approve_reject(user_id: str) -> M:
    return M([[B("✅ Valider", callback_data=f"approve:{user_id}"), B("❌ Refuser", callback_data=f"reject:{user_id}")]])


def draft_card(draft_id: str) -> M:
    return M(
        [
            [B("✅ Confirmer", callback_data=f"draft_confirm:{draft_id}")],
            [
                B("✏️ Corriger", callback_data=f"draft_edit:{draft_id}"),
                B("🗑 Annuler", callback_data=f"draft_cancel:{draft_id}"),
            ],
        ]
    )


def cancel_correction(draft_id: str) -> M:
    return M([[B("Annuler la correction", callback_data=f"draft_edit_cancel:{draft_id}")]])


def franchise_course(course: dict) -> M | None:
    cid = course["id"]
    if course["status"] == "pending":
        return M([[B("🗑 Retirer", callback_data=f"course_withdraw:{cid}")]])
    if course["status"] == "assigned":
        return M(
            [[B("💬 Contacter", callback_data=f"relay_start:{cid}"), B("🗑 Retirer", callback_data=f"course_withdraw:{cid}")]]
        )
    return None


def withdraw_confirm(course_id: int) -> M:
    return M([[B("Oui", callback_data=f"fw_yes:{course_id}"), B("Non", callback_data=f"fw_no:{course_id}")]])


def proposal(course_id: int) -> M:
    return M([[B("✅ Je prends", callback_data=f"course_take:{course_id}")]])


def livreur_course(course_id: int) -> M:
    return M(
        [
            [B("📦 Livré", callback_data=f"course_deliver:{course_id}"), B("💬 Contacter", callback_data=f"relay_start:{course_id}")],
            [
                B("🔜 Bientôt libre", callback_data="livreur_soon_free"),
                B("❌ Annuler", callback_data=f"course_livreur_cancel:{course_id}"),
            ],
        ]
    )


def livreur_cancel_confirm(course_id: int) -> M:
    return M([[B("Oui, j'annule", callback_data=f"lc_yes:{course_id}"), B("Non", callback_data=f"lc_no:{course_id}")]])


def relay_cancel() -> M:
    return M([[B("Annuler", callback_data="relay_cancel")]])


def relay_reply(course_id: int) -> M:
    return M([[B("↩️ Répondre", callback_data=f"relay_start:{course_id}")]])


def recap_prev(night_date: str) -> M:
    return M([[B("📅 Nuit précédente", callback_data=f"recap_prev:{night_date}")]])


def journal_prev(night_date: str) -> M:
    return M([[B("📅 Nuit précédente", callback_data=f"journal_prev:{night_date}")]])


def encours(pending: list[dict], assigned: list[dict]) -> M | None:
    rows = []
    for c in pending:
        rows.append([B(f"#{c['id']} 🗑 Annuler", callback_data=f"force_cancel:{c['id']}")])
    for c in assigned:
        rows.append(
            [
                B(f"#{c['id']} ✅ Livrée", callback_data=f"force_deliver:{c['id']}"),
                B("🔁 Rediffuser", callback_data=f"force_release:{c['id']}"),
                B("🗑 Annuler", callback_data=f"force_cancel:{c['id']}"),
            ]
        )
    return M(rows) if rows else None


def override_confirm(action: str, course_id: int) -> M:
    return M([[B("Oui", callback_data=f"fy:{action}:{course_id}"), B("Non", callback_data="op_cancel")]])


def _user_label(user: dict) -> str:
    name = user.get("display_name") or ROLE_LABEL[user["role"]]
    if user.get("real_name"):
        name += f" ({user['real_name']})"
    return name[:60]


def pending_users(users: list[dict]) -> M | None:
    rows = [
        [
            B(f"✅ {_user_label(u)}", callback_data=f"approve:{u['id']}"),
            B("❌", callback_data=f"reject:{u['id']}"),
        ]
        for u in users
    ]
    return M(rows) if rows else None


def user_buttons(users: list[dict], prefix: str) -> M:
    return M([[B(_user_label(u), callback_data=f"{prefix}:{u['id']}")] for u in users])


def ban_confirm(user_id: str) -> M:
    return M([[B("Oui", callback_data=f"ban_yes:{user_id}"), B("Non", callback_data="op_cancel")]])




def catalog(products: list[dict]) -> M:
    rows = [[B("➕ Ajouter des produits", callback_data="prod_add")]]
    for p in products:
        rows.append([B(f"🗑 {p['name'][:50]}", callback_data=f"prod_del:{p['id']}")])
    return M(rows)


def catalog_cancel() -> M:
    return M([[B("Annuler", callback_data="prod_cancel")]])
