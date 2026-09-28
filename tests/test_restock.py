"""Rechargement des livreurs : fonctions pures et ligne envoyée au tableau Rechargement."""
from bot.services import restock as rs
from bot.services import sheets


def test_quantities_and_cash():
    items = rs.add_product([], "DIV")
    items = rs.add_product(items, "KT")
    items = rs.add_product(items, "DIV")
    assert items == [{"p": "DIV", "q": 2}, {"p": "KT", "q": 1}]
    items = rs.change_qty(items, 0, 5)
    assert items[0]["q"] == 7
    assert rs.change_qty(items, 1, -1) == [{"p": "DIV", "q": 7}]
    assert rs.change_cash(0, 100) == 100.0 and rs.change_cash(50, -100) == 0.0


def test_texts_and_signs():
    items = [{"p": "DIV", "q": 12}, {"p": "KT", "q": 6}]
    assert rs.items_text(items, "load") == "+12 DIV, +6 KT"
    assert rs.items_text(items, "unload") == "−12 DIV, −6 KT"
    assert rs.signed_quantities(items, "unload") == {"DIV": -12, "KT": -6}
    assert rs.is_empty({"items": [], "cash": 0}) and not rs.is_empty({"items": [], "cash": 10})


def test_restock_row_for_the_sheet():
    # Lundi 28 septembre 2026 à 23h10, Paris → onglet Lundi.
    restock = {"id": 7, "created_at": "2026-09-28T21:10:00+00:00", "livreur_id": "l", "by_user_id": "r",
               "kind": "load", "box": "Box 2", "items": [{"p": "DIV", "q": 12}], "cash": "250"}
    users = {"l": {"display_name": "Livreur 1", "real_name": "Karim"}, "r": {"display_name": "Ravitailleur 1"}}
    assert sheets.restock_row(restock, users) == {
        "type": "recharge", "numero": 7, "onglet": "Lundi", "heure": "23:10",
        "livreur": "Livreur 1", "livreur_nom": "Karim", "box": "Box 2", "cash": 250.0,
        "produits": {"DIV": 12}, "ravitailleur": "Ravitailleur 1", "ravitailleur_nom": "",
    }
