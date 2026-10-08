"""Lecture d'un transfert entre livreurs (/swipe)."""
from bot.services import swipe as sw
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "US", "aliases": []}, {"id": 2, "name": "DIV", "aliases": []},
               {"id": 3, "name": "MSX", "aliases": []}])
NAMES = ["Livreur A", "Livreur B", "Livreur C"]
USERS = [{"id": "u-a", "display_name": "Livreur A"}]


def lines(text):
    return list(enumerate(text.splitlines(), 1))


def test_parse_blocks_separators_and_all():
    text = "Livreur A > Livreur B\n3 DIV\n2 x MSX\n1 DIV\n\nb → c\ntout\n\nLivreur C vers livreur a\n4 US"
    out = sw.parse(lines(text), CAT, NAMES, USERS)
    assert out.errors == []
    ab, bc, ca = out.blocks
    assert (ab.src, ab.dst, ab.src_user["id"], ab.dst_user) == ("Livreur A", "Livreur B", "u-a", None)
    assert ab.items == [{"p": "DIV", "q": 4}, {"p": "MSX", "q": 2}] and not ab.all
    assert (bc.src, bc.dst, bc.all, bc.items) == ("Livreur B", "Livreur C", True, [])
    assert (ca.src, ca.dst, ca.dst_user["id"], ca.items) == ("Livreur C", "Livreur A", "u-a", [{"p": "US", "q": 4}])


def test_parse_reports_problems():
    text = "3 DIV\nLivreur A > Livreur Z\nLivreur A > livreur a\nLivreur A\nLivreur A > Livreur B\n2 KT\n" \
           "Livreur B > Livreur C\ntout\n1 US\nLivreur C > Livreur A"
    out = sw.parse(lines(text), CAT, NAMES, USERS)
    assert out.errors == [
        "ligne 1 : écris d'abord « Livreur A > Livreur B »",
        "ligne 2 : livreur inconnu « Livreur Z »",
        "ligne 3 : le même livreur des deux côtés",
        "ligne 4 : « Livreur A » — attendu : « Livreur A > Livreur B », puis quantité et produit (ex. 3 DIV) ou « tout »",
        "ligne 6 : produit inconnu « KT »",
        "Livreur A > Livreur B : indique les produits (ex. 3 DIV) ou « tout »",
        "Livreur B > Livreur C : « tout » ou des produits, pas les deux",
        "Livreur C > Livreur A : indique les produits (ex. 3 DIV) ou « tout »",
    ]


def test_stock_helpers():
    known = {"Livreur A": {"DIV": 3, "US": 0, "MSX": -1, "KT": 2.0}}
    have = sw.stock_of("livreur a", known)
    assert sw.stock_of("Livreur B", known) is None
    assert sw.everything(have) == [{"p": "DIV", "q": 3}, {"p": "KT", "q": 2}]
    assert sw.shortages([{"p": "DIV", "q": 5}, {"p": "KT", "q": 2}, {"p": "US", "q": 1}], have) == [
        ("DIV", 5, 3.0), ("US", 1, 0.0)]
