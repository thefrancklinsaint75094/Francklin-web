"""Lecture d'un rechargement en texte (/ravi)."""
from bot.services import restock_text as rt
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "US", "aliases": []}, {"id": 2, "name": "DIV", "aliases": []},
               {"id": 3, "name": "MSX", "aliases": []}, {"id": 4, "name": "3F", "aliases": []}])
BOXES = ("Box 1", "Box 2", "Box 3")
NAMES = ["Livreur A", "Livreur B"]
USERS = [{"id": "u-a", "display_name": "Livreur A"}]


def lines(text):
    return list(enumerate(text.splitlines(), 1))


def test_ravitailleur_resolution():
    users = [{"id": "r1", "display_name": "Ravitailleur 1"}]
    names = ["Ravitailleur 1", "Ravitailleur 2"]
    assert rt.resolve_ravitailleur("1", names, users) == ("Ravitailleur 1", users[0])
    assert rt.resolve_ravitailleur("ravi 2", names, users) == ("Ravitailleur 2", None)
    assert rt.resolve_ravitailleur("3", names, users) == (None, None)


def test_parse_blocks_box_carried_unload_and_cash():
    text = ("Livreur A\nBox 1\n12 DIV\n6 x US\n2 3F\n-2 MSX\n+3 DIV\ncash 300€\n\n"
            "b\n4 US\n\nLivreur A\nbox 2\nCash 50")
    out = rt.parse(lines(text), CAT, NAMES, USERS, BOXES)
    assert out.errors == []
    a, b, a2 = out.blocks
    assert (a.livreur, a.user["id"], a.box, a.cash) == ("Livreur A", "u-a", "Box 1", 300.0)
    assert a.load == [{"p": "DIV", "q": 15}, {"p": "US", "q": 6}, {"p": "3F", "q": 2}]
    assert a.unload == [{"p": "MSX", "q": 2}]
    assert (b.livreur, b.user, b.box, b.load) == ("Livreur B", None, "Box 1", [{"p": "US", "q": 4}])
    assert (a2.box, a2.load, a2.cash) == ("Box 2", [], 50.0)


def test_parse_reports_problems():
    text = "12 DIV\nLivreur Z\nLivreur B\n12 DIV\nBox 9\n2 KT\nDIV douze\n"
    out = rt.parse(lines(text), CAT, NAMES, USERS, BOXES)
    assert out.errors == [
        "ligne 2 : livreur inconnu « Livreur Z »",
        "ligne 5 : box inconnu « Box 9 » (box : Box 1, Box 2, Box 3)",
        "ligne 6 : produit inconnu « KT »",
        "ligne 7 : livreur inconnu « DIV douze »",
    ]


def test_parse_forgotten_livreur_or_box_is_asked():
    """Livreur ou box oublié : pas d'erreur, le bot demandera (rt.missing)."""
    out = rt.parse(lines("12 DIV\ncash 50\n\nLivreur B\n4 US"), CAT, NAMES, USERS, BOXES)
    assert out.errors == []
    first, second = out.blocks
    assert (first.livreur, first.box, first.load, first.cash) == (None, None, [{"p": "DIV", "q": 12}], 50.0)
    assert (second.livreur, second.box) == ("Livreur B", None)
    blocks = [{"livreur": b.livreur, "box": b.box, "load": b.load, "unload": b.unload} for b in out.blocks]
    assert rt.missing(blocks) == ("livreur", 0)
    blocks[0]["livreur"] = "Livreur A"
    assert rt.missing(blocks) == ("box", 0)
    blocks[0]["box"] = blocks[1]["box"] = "Box 1"
    assert rt.missing(blocks) is None
    assert rt.missing([{"livreur": "Livreur A", "box": None, "load": [], "unload": [], "cash": 20}]) is None
