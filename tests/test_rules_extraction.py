"""Lecture des commandes sans IA : règles fixes."""
import pytest

from bot.services.rules_extraction import RuleExtractor, parse_message


def one(text):
    orders = parse_message(text)
    assert len(orders) == 1, orders
    return orders[0]


# --- Les 4 exemples réels de la spécification (§7.1)

def test_spec_example_1_paris_arrondissement():
    o = one("12 rue de rivoli paris 4, digicode 45A32, 2 vodka + coca, 60€")
    assert o == {"address": "12 rue de rivoli 75004 Paris", "address_detail": "digicode 45A32",
                 "products": "2 vodka + coca", "price": "60", "requested_time": None}


def test_spec_example_2_multiline_floor_is_not_a_price():
    o = one("Une bouteille de champagne 80 euros\n34 avenue de la republique 75011\n3eme étage gauche, il attend")
    assert o["address"] == "34 avenue de la republique 75011"
    assert o["products"] == "Une bouteille de champagne"
    assert o["price"] == "80"
    assert o["address_detail"] == "3eme étage gauche, il attend"


def test_spec_example_3_trailing_house_number_and_bare_price():
    o = one("75012 rue de charenton 45 / whisky + 2 red bull / 55")
    assert o["address"] == "45 rue de charenton 75012"
    assert o["products"] == "whisky + 2 red bull"
    assert o["price"] == "55"


def test_spec_example_4_two_orders():
    a, b = parse_message("2 commandes :\n- 8 rue oberkampf 75011, 1 jack daniels, 45\n"
                         "- montreuil 12 rue de paris, 2 rosé + glace, 38€, code 1234B")
    assert (a["address"], a["products"], a["price"]) == ("8 rue oberkampf 75011", "1 jack daniels", "45")
    assert (b["address"], b["products"], b["price"], b["address_detail"]) == (
        "12 rue de paris montreuil", "2 rosé + glace", "38", "code 1234B")


# --- Messages réels envoyés par un franchisé le premier soir

@pytest.mark.parametrize("text", ["54 rue du point du jour Boulogne ~ 50 mousseux",
                                  "54 rue du point du jour Boulogne 50 mousseux"])
def test_real_messages_price_not_invented(text):
    o = one(text)
    assert o["address"] == "54 rue du point du jour Boulogne"
    assert o["products"] == "50 mousseux"
    assert o["price"] is None  # « 50 » suivi d'un mot : une quantité, pas un prix


# --- Autres formes

def test_no_separator_trailing_price():
    o = one("12 rue de la paix 75002 2 vodka 60")
    assert (o["address"], o["products"], o["price"]) == ("12 rue de la paix 75002", "2 vodka", "60")


def test_city_after_postcode_stays_in_address():
    o = one("3 rue de la gare 94300 Vincennes, 2e gauche, 1 champagne, prix 90")
    assert o["address"] == "3 rue de la gare 94300 Vincennes"
    assert o["address_detail"] == "2e gauche"
    assert (o["products"], o["price"]) == ("1 champagne", "90")


def test_multiword_city():
    o = one("10 avenue jean jaures 93200 saint denis 2 whisky 70€")
    assert o["address"] == "10 avenue jean jaures 93200 saint denis"
    assert (o["products"], o["price"]) == ("2 whisky", "70")


def test_arrondissement_segment_and_time():
    o = one("5 bd voltaire, paris 11e, 1 gin, 1 tonic, 35 euros, vers 23h")
    assert o["address"] == "5 bd voltaire 75011 Paris"
    assert (o["products"], o["price"], o["requested_time"]) == ("1 gin, 1 tonic", "35", "vers 23h")


def test_city_segment_after_street():
    o = one("12 rue de paris, montreuil, 2 rosé, 38€")
    assert o["address"] == "12 rue de paris montreuil"
    assert o["products"] == "2 rosé"


def test_one_order_per_line_without_bullets():
    a, b = parse_message("8 rue oberkampf 75011, 1 jack daniels, 45\n"
                         "12 rue de paris 93100 montreuil, 2 rosé + glace, 38€")
    assert a["price"] == "45" and b["address"] == "12 rue de paris 93100 montreuil"


def test_decimal_price_with_comma():
    assert one("1 rue a 75001, gin, 12,50 €")["price"] == "12.50"


def test_detail_sentence_is_kept():
    o = one("12 rue x 75011 il attend en bas, 1 gin, 30")
    assert o["address_detail"] == "il attend en bas" and o["price"] == "30"


def test_greeting_is_no_order():
    assert parse_message("bonjour") == []
    assert parse_message("Salut !") == []


def test_complement_has_no_address_nor_price():
    o = one("le digicode c'est 45B en fait")
    assert o["address"] is None and o["price"] is None


async def test_rule_extractor_returns_validated_orders():
    orders = await RuleExtractor().extract_text("54 rue du point du jour Boulogne 50 mousseux")
    assert orders[0].missing == ["price"]
    orders = await RuleExtractor().extract_text("12 rue de rivoli paris 4, 2 vodka, 60€")
    assert orders[0].valid and orders[0].price == 60.0
