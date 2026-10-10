"""Goûts (variantes) : même produit pour la compta, seulement « en a / n'en a plus » chez le livreur."""
from bot.services import restock_text as rt
from bot.services import variants
from bot.services.catalog import Catalog

MSX = {"id": 3, "name": "MSX", "aliases": [], "variants": ["noisette", "fraise", "orange", "banane"]}
CAT = Catalog([{"id": 1, "name": "US", "aliases": [], "variants": []}, {"id": 2, "name": "DIV", "aliases": []}, MSX])


def test_parse_list_and_found_in():
    assert variants.parse_list("Noisette, fraise et banane ; fraise") == ["noisette", "fraise", "banane"]
    assert variants.found_in("12 MSX (5 bananes, 7 Fraise)", MSX["variants"]) == ["fraise", "banane"]
    assert variants.leftovers("MSX banane kiwi 3", MSX, ["banane"]) == ["kiwi"]


def test_requested_in_an_order():
    assert variants.requested("2 DIV (60 €) + 1 MSX banane", CAT) == [("MSX", "banane")]
    assert variants.requested("1 MSX noisette, 1 MSX fraise", CAT) == [("MSX", "noisette"), ("MSX", "fraise")]
    assert variants.requested("2 US", CAT) == [] and variants.requested("1 MSX", CAT) == []


def test_ravi_keeps_flavors_and_warns_on_unknown():
    lines = list(enumerate("Livreur A\nBox 1\n12 MSX banane fraise\n3 MSX (2 noisette, 1 kiwi)\n6 US".splitlines(), 1))
    out = rt.parse(lines, CAT, ["Livreur A"], [], ("Box 1",))
    assert out.errors == []
    assert out.blocks[0].load == [{"p": "MSX", "q": 15, "v": ["fraise", "banane", "noisette"]}, {"p": "US", "q": 6}]
    assert out.warnings == ["ligne 4 : goût « kiwi » inconnu pour MSX (goûts : noisette, fraise, orange, banane) — ignoré"]


def test_by_livreur():
    rows = [{"livreur_name": "Livreur A", "product": "MSX", "variant": "banane"},
            {"livreur_name": "Livreur A", "product": "MSX", "variant": "fraise"}]
    assert variants.by_livreur(rows) == {"Livreur A": {"MSX": {"banane", "fraise"}}}
