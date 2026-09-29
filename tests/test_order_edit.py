"""Modification de la commande par le livreur : fonctions pures."""
from bot.services import order_edit as oe
from bot.services import sheets
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "DIV", "aliases": []}, {"id": 2, "name": "KT", "aliases": []}])


def test_lines_from_template_and_free_text():
    course = {"products": "2 div (60 €) + 1 KT (5 €)", "price": "65"}
    assert oe.lines_from_course(course, CAT) == [{"p": "DIV", "q": 2, "x": 60.0}, {"p": "KT", "q": 1, "x": 5.0}]
    # Texte libre : le total sur la première ligne, les autres sans prix.
    assert oe.lines_from_course({"products": "2 vodka + coca", "price": 60}) == [
        {"p": "vodka", "q": 2, "x": 60.0}, {"p": "coca", "q": 1, "x": 0.0}]


def test_quantity_never_changes_price_and_zero_removes_line():
    lines = [{"p": "DIV", "q": 2, "x": 60.0}, {"p": "KT", "q": 1, "x": 5.0}]
    # Le prix est libre : ➖ / ➕ ne le recalculent jamais.
    assert oe.change_qty(lines, 0, 1)[0] == {"p": "DIV", "q": 3, "x": 60.0}
    assert oe.change_qty(lines, 0, -1)[0] == {"p": "DIV", "q": 1, "x": 60.0}
    assert oe.change_qty([{"p": "KT", "q": 1, "x": 10.0}], 0, 1)[0]["x"] == 10.0
    assert oe.change_qty(lines, 1, -1) == [{"p": "DIV", "q": 2, "x": 60.0}]
    assert lines[0]["q"] == 2  # l'original n'est pas modifié
    assert oe.change_qty(lines, 9, 1) == lines


def test_add_product_new_or_existing():
    lines = [{"p": "DIV", "q": 2, "x": 60.0}]
    added, idx = oe.add_product(lines, "KT")
    assert idx == 1 and added[1] == {"p": "KT", "q": 1, "x": 0.0}
    again, idx = oe.add_product(lines, "DIV")
    assert idx == 0 and again == [{"p": "DIV", "q": 3, "x": 60.0}]   # prix inchangé


def test_prices_by_buttons_of_ten_and_typed():
    assert oe.PRICE_STEPS == (-50, -20, -10, 10, 20, 50)
    lines = [{"p": "KT", "q": 1, "x": 0.0}]
    lines = oe.change_price(lines, 0, 50)
    lines = oe.change_price(lines, 0, 20)
    lines = oe.change_price(lines, 0, -10)
    assert lines[0]["x"] == 60.0
    assert oe.change_price(lines, 0, -50)[0]["x"] == 10.0
    assert oe.change_price(lines, 0, -100)[0]["x"] == 0.0
    assert oe.set_price(lines, 0, 30)[0]["x"] == 30.0
    assert [oe.parse_price(t) for t in ("30", "12,50 €", "70e", " 40 euros", "abc", "3 DIV")] == [
        30.0, 12.5, 70.0, 40.0, None, None]
    assert [oe.valid_price(v) for v in (10, 30.0, 120, 0, 5, 35, 12.5)] == [
        True, True, True, False, False, False, False]
    assert oe.off_step_prices([{"p": "A", "q": 1, "x": 30.0}, {"p": "B", "q": 1, "x": 5.0}]) == ["B"]


def test_products_text_round_trips_through_google_sheets():
    lines = [{"p": "DIV", "q": 3, "x": 90.0}, {"p": "KT", "q": 1, "x": 7.5}]
    text = oe.products_text(lines)
    assert text == "3 DIV (90 €) + 1 KT (7,50 €)"
    assert oe.total(lines) == 97.5
    assert sheets.product_lines(text, 97.5, CAT) == [
        {"produit": "DIV", "qte": 3, "prix": 90.0}, {"produit": "KT", "qte": 1, "prix": 7.5}]
    assert oe.products_text([{"p": "DIV", "q": 2, "x": 60.0}]) == "2 DIV"


def test_missing_prices_and_same():
    lines = [{"p": "DIV", "q": 1, "x": 30.0}, {"p": "KT", "q": 1, "x": 0.0}]
    assert oe.missing_prices(lines) == ["KT"]
    assert oe.same(lines, [dict(l) for l in lines])
    assert not oe.same(lines, lines[:1])


def test_franchise_price_problems():
    assert oe.price_problems(60, "2 vodka") == []
    assert oe.price_problems(65, "2 vodka") == ["total 65 €"]
    assert oe.price_problems(30, "1 DIV (15 €) + 1 KT (15 €)") == ["1 DIV (15 €)", "1 KT (15 €)"]
    assert oe.price_problems(90, "2 DIV (60 €) + 1 KT (30 €)") == []
    assert oe.price_problems(12.5, "1 KT") == ["total 12,50 €"]
