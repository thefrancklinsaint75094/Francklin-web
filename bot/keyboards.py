"""Construction des boutons inline. callback_data : préfixe court + identifiant (≤ 64 octets)."""
from __future__ import annotations

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

from bot.texts import ROLE_LABEL


def role_choice() -> M:
    return M([[B("Franchisé", callback_data="role:franchise"), B("Livreur", callback_data="role:livreur")],
              [B("Ravitailleur", callback_data="role:ravitailleur")]])


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
    """Message de course du franchisé : il a les pleins pouvoirs sur sa course."""
    cid = course["id"]
    if course["status"] == "pending":
        return M([
            [B("👤 Attribuer", callback_data=f"assign:{cid}"), B("✏️ Modifier", callback_data=f"order_edit:{cid}")],
            [B("🗑 Retirer", callback_data=f"course_withdraw:{cid}")],
        ])
    if course["status"] == "assigned":
        return M([
            [B("📦 Livrée", callback_data=f"force_deliver:{cid}"), B("✏️ Modifier", callback_data=f"order_edit:{cid}")],
            [B("💬 Contacter", callback_data=f"relay_start:{cid}"), B("🗑 Retirer", callback_data=f"course_withdraw:{cid}")],
        ])
    return None


def withdraw_confirm(course_id: int) -> M:
    return M([[B("Oui", callback_data=f"fw_yes:{course_id}"), B("Non", callback_data=f"fw_no:{course_id}")]])


def proposal(course_id: int) -> M:
    return M([[B("✅ Je prends", callback_data=f"course_take:{course_id}")]])


def livreur_course(course_id: int) -> M:
    return M(
        [
            [B("📦 Livré", callback_data=f"course_deliver:{course_id}"), B("💬 Contacter", callback_data=f"relay_start:{course_id}")],
            [B("✏️ Modifier la commande", callback_data=f"order_edit:{course_id}")],
            [
                B("🔜 Bientôt libre", callback_data="livreur_soon_free"),
                B("❌ Annuler", callback_data=f"course_livreur_cancel:{course_id}"),
            ],
        ]
    )


def _short_eur(value) -> str:
    v = float(value)
    return (f"{v:.2f}".replace(".", ",").replace(",00", "")) + "€"


def order_editor(lines: list[dict], sel: int | None = None) -> M:
    """Éditeur de commande du livreur. callback_data : oe_q:<ligne>:<±1>, oe_s:<ligne> (choisir
    la ligne dont on change le prix, -1 pour revenir), oe_p:<ligne>:<±€>, oe_add:<page>, oe_ok, oe_x."""
    from bot.services.order_edit import PRICE_STEPS

    rows = []
    if sel is not None and 0 <= sel < len(lines):
        steps = [B(f"{d:+d} €", callback_data=f"oe_p:{sel}:{d}") for d in PRICE_STEPS]
        rows += [steps[:3], steps[3:], [B("✅ OK", callback_data="oe_s:-1")]]
        return M(rows)
    for i, line in enumerate(lines):
        price = _short_eur(line["x"]) if float(line["x"]) > 0 else "prix ?"
        label = f"{line['q']} {line['p']} · {price}"[:40]
        rows.append([B("➖", callback_data=f"oe_q:{i}:-1"), B(label, callback_data=f"oe_s:{i}"),
                     B("➕", callback_data=f"oe_q:{i}:1")])
    rows.append([B("➕ Ajouter un produit", callback_data="oe_add:0")])
    rows.append([B("✅ Valider", callback_data="oe_ok"), B("↩️ Annuler", callback_data="oe_x")])
    return M(rows)


def order_picker(products: list[dict], page: int, page_size: int) -> M:
    start = page * page_size
    chunk = products[start:start + page_size]
    rows = [[B(p["name"][:30], callback_data=f"oe_pick:{p['id']}") for p in chunk[i:i + 3]]
            for i in range(0, len(chunk), 3)]
    nav = []
    if page > 0:
        nav.append(B("◀️", callback_data=f"oe_add:{page - 1}"))
    if start + page_size < len(products):
        nav.append(B("▶️", callback_data=f"oe_add:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("↩️ Retour", callback_data="oe_s:-1")])
    return M(rows)


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
        rows.append([B(f"#{c['id']} 👤 Attribuer", callback_data=f"assign:{c['id']}"),
                     B("🗑 Annuler", callback_data=f"force_cancel:{c['id']}")])
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
    if action == "deliver":
        # Livrée par un admin : il indique aussi comment le client a payé.
        return M([[B("Oui — 💵 Espèces", callback_data=f"fy:deliver:{course_id}:e"),
                   B("Oui — 💳 Virement", callback_data=f"fy:deliver:{course_id}:v")],
                  [B("Non", callback_data="op_cancel")]])
    return M([[B("Oui", callback_data=f"fy:{action}:{course_id}"), B("Non", callback_data="op_cancel")]])


def payment_choice(course_id: int) -> M:
    return M([[B("💵 Espèces", callback_data=f"pay:{course_id}:e"), B("💳 Virement", callback_data=f"pay:{course_id}:v")],
              [B("↩️ Retour", callback_data=f"pay_back:{course_id}")]])


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


STATUS_MARK = {"pending": "⏳ ", "banned": "⛔ "}


def user_buttons(users: list[dict], prefix: str, with_status: bool = False) -> M:
    def label(u: dict) -> str:
        return (STATUS_MARK.get(u["status"], "") if with_status else "") + _user_label(u)

    return M([[B(label(u)[:60], callback_data=f"{prefix}:{u['id']}")] for u in users])


def ban_confirm(user_id: str) -> M:
    return M([[B("Oui", callback_data=f"ban_yes:{user_id}"), B("Non", callback_data="op_cancel")]])


def delete_confirm(user_id: str) -> M:
    return M([[B("🗑 Supprimer définitivement", callback_data=f"del_yes:{user_id}")],
              [B("Annuler", callback_data="op_cancel")]])




def catalog(products: list[dict]) -> M:
    rows = [[B("➕ Ajouter des produits", callback_data="prod_add")]]
    for p in products:
        rows.append([B(f"🗑 {p['name'][:50]}", callback_data=f"prod_del:{p['id']}")])
    return M(rows)


def catalog_cancel() -> M:
    return M([[B("Annuler", callback_data="prod_cancel")]])


# ---------------------------------------------------------------- rechargement

_RS_CANCEL = B("❌ Annuler", callback_data="rs_x")


def restock_livreurs(livreurs: list[dict]) -> M:
    rows = [[B(_user_label(u)[:40], callback_data=f"rs_l:{u['id']}")] for u in livreurs]
    rows.append([_RS_CANCEL])
    return M(rows)


def restock_kind() -> M:
    return M([[B("📦 Chargement", callback_data="rs_k:load"), B("↩️ Reprise", callback_data="rs_k:unload")],
              [B("💶 Cash seulement", callback_data="rs_k:cash")], [_RS_CANCEL]])


def restock_boxes(boxes: tuple[str, ...]) -> M:
    buttons = [B(b[:30], callback_data=f"rs_b:{i}") for i, b in enumerate(boxes)]
    rows = [buttons[j:j + 3] for j in range(0, len(buttons), 3)]
    rows.append([_RS_CANCEL])
    return M(rows)


def restock_editor(kind: str, items: list[dict]) -> M:
    from bot.services.restock import CASH_STEPS

    rows = []
    if kind == "cash":
        steps = [B(f"{d:+d} €", callback_data=f"rs_cp:{d}") for d in CASH_STEPS]
        rows += [steps[:3], steps[3:]]
    else:
        for i, item in enumerate(items):
            rows.append([B("−1", callback_data=f"rs_q:{i}:-1"), B(f"{item['q']} {item['p']}"[:30], callback_data="rs_noop"),
                         B("+1", callback_data=f"rs_q:{i}:1"), B("+5", callback_data=f"rs_q:{i}:5")])
        rows.append([B("➕ Ajouter un produit", callback_data="rs_add:0"), B("💶 Cash", callback_data="rs_c")])
    rows.append([B("✅ Valider", callback_data="rs_ok"), _RS_CANCEL])
    return M(rows)


def restock_cash() -> M:
    from bot.services.restock import CASH_STEPS

    steps = [B(f"{d:+d} €", callback_data=f"rs_cp:{d}") for d in CASH_STEPS]
    return M([steps[:3], steps[3:], [B("✅ OK", callback_data="rs_back")]])


def restock_picker(products: list[dict], page: int, page_size: int) -> M:
    start = page * page_size
    chunk = products[start:start + page_size]
    rows = [[B(p["name"][:30], callback_data=f"rs_pick:{p['id']}") for p in chunk[i:i + 3]]
            for i in range(0, len(chunk), 3)]
    nav = []
    if page > 0:
        nav.append(B("◀️", callback_data=f"rs_add:{page - 1}"))
    if start + page_size < len(products):
        nav.append(B("▶️", callback_data=f"rs_add:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("↩️ Retour", callback_data="rs_back")])
    return M(rows)


# ---------------------------------------------------------------- admin : livreurs et attribution

def livreurs_duty(livreurs: list[dict]) -> M | None:
    rows = []
    for lv in livreurs:
        name = (lv.get("display_name") or "?")[:20]
        if lv.get("on_duty"):
            duty = B(f"⏸ Pause — {name}", callback_data=f"duty_off:{lv['id']}")
        else:
            duty = B(f"🟢 En service — {name}", callback_data=f"duty_on:{lv['id']}")
        rows.append([duty, B("🏷 Nom", callback_data=f"sname:{lv['id']}"), B("📍", callback_data=f"loc:{lv['id']}")])
    if rows:
        rows.append([B("🗺 Tous les livreurs", callback_data="loc_all")])
    return M(rows) if rows else None


def name_picker(user: dict, choices: tuple[str, ...], holders: dict[str, dict]) -> M:
    """Noms de la feuille ; un nom pris par quelqu'un d'autre est marqué 🔒."""
    buttons = []
    for i, name in enumerate(choices):
        holder = holders.get(" ".join(name.lower().split()))
        if holder and holder["id"] == user["id"]:
            label = f"✅ {name}"
        elif holder:
            label = f"🔒 {name}"
        else:
            label = name
        buttons.append(B(label[:30], callback_data=f"sname_set:{user['id']}:{i}"))
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([B("OK", callback_data=f"sname_done:{user['id']}")])
    return M(rows)


def assign_livreurs(course_id: int, livreurs: list[dict], counts: dict[str, int]) -> M:
    rows = []
    for lv in livreurs:
        state = "🟢" if lv.get("on_duty") else "⏸"
        busy = f" · {counts.get(lv['id'], 0)} en cours" if counts.get(lv["id"]) else ""
        label = f"{state} {lv.get('display_name') or '?'}{busy}"[:40]
        rows.append([B(label, callback_data=f"assign_to:{course_id}:{lv['id']}")])
    rows.append([B("Annuler", callback_data="op_cancel")])
    return M(rows)



def stock_menu(boxes: tuple[str, ...]) -> M:
    buttons = [B(f"📦 {b}"[:30], callback_data=f"sv:box:{i}") for i, b in enumerate(boxes)]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([B("📦 Tous les box", callback_data="sv:all"), B("🚴 Livreurs", callback_data="sv:liv")])
    return M(rows)


# ---------------------------------------------------------------- caisse : dépenses et cash

_DP_CANCEL = B("❌ Annuler", callback_data="dp_x")


def expense_livreurs(livreurs: list[dict]) -> M:
    rows = [[B(_user_label(u)[:40], callback_data=f"dp_l:{u['id']}")] for u in livreurs]
    rows.append([_DP_CANCEL])
    return M(rows)


def expense_step(step: str) -> M:
    from bot.services.cash import AMOUNT_STEPS, MOTIFS

    if step == "kind":
        return M([[B("🧾 Charges", callback_data="dp_k:c"), B("💸 Avance sur paye", callback_data="dp_k:p")],
                  [_DP_CANCEL]])
    if step == "amount":
        steps = [B(f"{d:+d} €", callback_data=f"dp_a:{d}") for d in AMOUNT_STEPS]
        half = len(steps) // 2
        return M([steps[:half], steps[half:], [B("➡️ Suivant", callback_data="dp_n"), _DP_CANCEL]])
    if step == "motif":
        buttons = [B(m, callback_data=f"dp_m:{i}") for i, m in enumerate(MOTIFS)]
        rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
        rows.append([B("Sans motif", callback_data="dp_m:-1"), _DP_CANCEL])
        return M(rows)
    return M([[B("✅ Enregistrer", callback_data="dp_ok")],
              [B("✏️ Montant", callback_data="dp_e"), _DP_CANCEL]])


def cash_overview(rows: list[dict]) -> M:
    buttons = []
    for row in rows:
        user = row.get("user")
        if user and user["status"] == "active" and row["cash"] > 0:
            label = f"💶 Récupérer — {row['nom']}"[:40]
            buttons.append([B(label, callback_data=f"cs_r:{user['id']}")])
    buttons.append([B("🔄 Actualiser", callback_data="cs_ref")])
    return M(buttons)


def cloture_confirm() -> M:
    return M([[B("✅ Reset de la semaine", callback_data="cl_go")], [B("❌ Annuler", callback_data="cl_x")]])


def transport_mode(current: str | None) -> M:
    from bot.services.transport import ICON, LABEL

    buttons = []
    for code, mode in (("t", "transport"), ("d", "deux_roues"), ("v", "voiture")):
        mark = "✅ " if current == mode else ""
        buttons.append(B(f"{mark}{ICON[mode]} {LABEL[mode].split(' /')[0]}", callback_data=f"tmode:{code}"))
    return M([buttons])


def sales_confirm() -> M:
    return M([[B("✅ Ajouter au tableau", callback_data="sl_ok")], [B("❌ Annuler", callback_data="sl_x")]])


def ravi_confirm() -> M:
    return M([[B("✅ Enregistrer", callback_data="rv_ok")], [B("❌ Annuler", callback_data="rv_x")]])
