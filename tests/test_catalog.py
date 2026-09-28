"""Catalogue des produits et modèle de commande (sans IA)."""
from bot.services.catalog import Catalog, normalize, parse_input
from bot.services.rules_extraction import MODEL_EXAMPLE, parse_template

PRODUCTS = [
    {"id": 1, "name": "Vodka Absolut", "aliases": ["absolut", "abso", "vodka"]},
    {"id": 2, "name": "Vodka Grey Goose", "aliases": ["grey goose", "vodka"]},
    {"id": 3, "name": "Jack Daniel's", "aliases": ["jack", "jd"]},
    {"id": 4, "name": "Coca-Cola", "aliases": ["coca"]},
]
CAT = Catalog(PRODUCTS)


def test_normalize():
    assert normalize("Jack Daniel's 70cl") == "jack daniel"
    assert normalize("Rosés") == "rose"
    assert normalize("Coca-Cola 1,5L") == "coca cola"
    assert normalize("  VODKA  ") == "vodka"


def test_parse_input_one_product_per_line():
    assert parse_input("Vodka Absolut : absolut, abso\n- Coca-Cola 1,5L = coca, coca cola\nRed Bull\n\n") == [
        ("Vodka Absolut", ["absolut", "abso"]),
        ("Coca-Cola 1,5L", ["coca", "coca cola"]),
        ("Red Bull", []),
    ]


def test_match_exact_alias_plural_and_volume():
    assert CAT.match("absolut").product["id"] == 1
    assert CAT.match("ABSOLUT 70cl").product["id"] == 1
    assert CAT.match("cocas").product["id"] == 4
    assert CAT.match("JD").product["id"] == 3


def test_match_contained_prefers_longest():
    assert CAT.match("bouteille de jack daniels").product["name"] == "Jack Daniel's"


def test_match_typo():
    m = CAT.match("absolu")
    assert m.product["id"] == 1 and m.fuzzy


def test_match_ambiguous_and_unknown():
    assert CAT.match("vodka").ambiguous == ["Vodka Absolut", "Vodka Grey Goose"]
    m = CAT.match("ricard")
    assert m.product is None and not m.ambiguous


# --- Modèle de commande

def test_model_example():
    [o] = parse_template(MODEL_EXAMPLE, CAT)
    assert o["address"] == "12 rue de Rivoli 75004 Paris"
    # « vodka » désigne deux produits : le mot est gardé tel quel, avec un avertissement.
    assert o["products"] == "2 vodka (60 €) + 1 Coca-Cola (5 €)"
    assert o["warnings"] == ["⚠️ « vodka » peut être : Vodka Absolut, Vodka Grey Goose"]
    assert o["price"] == "65.00"
    assert o["address_detail"] == "Digicode 45A32, 3e étage"


def test_model_ambiguous_word_warns():
    [o] = parse_template("1 rue A 75001\n2 vodka 60", Catalog(PRODUCTS[:2] + PRODUCTS[3:]))
    assert any("peut être" in w for w in o["warnings"])


def test_model_unknown_product_warns_but_keeps_order():
    [o] = parse_template("12 rue de Rivoli 75004\n1 ricard 25", CAT)
    assert o["products"] == "1 ricard" and o["price"] == "25.00"
    assert o["warnings"] == ["⚠️ Produit pas dans le catalogue : « ricard »"]


def test_model_without_catalog_has_no_warnings():
    [o] = parse_template("12 rue de Rivoli 75004\n1 ricard 25", Catalog([]))
    assert o["warnings"] == []


def test_model_missing_line_price_means_missing_total():
    [o] = parse_template("12 rue de Rivoli 75004\n2 vodka 60\n1 coca", CAT)
    assert o["price"] is None


def test_model_decimal_and_euro_sign():
    [o] = parse_template("12 rue de Rivoli 75004\n1 coca 4,50€\n2 jd 70 euros", CAT)
    assert o["price"] == "74.50"


def test_model_time_and_detail_on_address_line():
    [o] = parse_template("12 rue de Rivoli 75004, vers 23h\n2 abso 60\n\nil attend en bas", CAT)
    assert o["requested_time"] == "vers 23h" and o["address_detail"] == "il attend en bas"


def test_model_floor_line_is_not_a_product():
    [o] = parse_template("12 rue X 75011\n3e étage\n2 abso 60", CAT)
    assert o["address_detail"] == "3e étage" and o["products"] == "2 Vodka Absolut"


def test_free_text_is_not_the_model():
    assert parse_template("12 rue de rivoli paris 4, digicode 45A32, 2 vodka + coca, 60€", CAT) is None
    assert parse_template("54 rue du point du jour Boulogne 50 mousseux", CAT) is None
    assert parse_template("- 8 rue oberkampf 75011, 1 jack, 45\n- 12 rue de paris, 2 rosé, 38€", CAT) is None
