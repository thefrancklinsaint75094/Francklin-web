"""Stock déclaré par le livreur (début / fin de service) : lecture et écarts avec le tableau."""
from bot.services import declare
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "US", "aliases": []}, {"id": 2, "name": "DIV", "aliases": ["divin"]},
               {"id": 3, "name": "3F", "aliases": []}])


def test_parse_formats():
    assert declare.parse("3 US, 2 divin", CAT).items == {"US": 3, "DIV": 2}
    assert declare.parse("US 3\nDIV x2\n2 3F + 1 US", CAT).items == {"US": 4, "DIV": 2, "3F": 2}
    assert declare.parse("us : 5 et 3F 1", CAT).items == {"US": 5, "3F": 1}
    for nothing in ("rien", "Rien", "aucun", "0"):
        out = declare.parse(nothing, CAT)
        assert out.items == {} and out.errors == []


def test_parse_errors():
    out = declare.parse("3 KT, beaucoup de US", CAT)
    assert out.errors == ["produit inconnu « KT »", "« beaucoup de US » — écris la quantité et le produit (ex. 3 US)"]
    assert declare.parse("", CAT).errors == ["écris au moins un produit (ex. 3 US) ou « rien »"]


def test_differences_with_sheet():
    assert declare.differences({"US": 3, "DIV": 2}, {"US": 3, "DIV": 2}) == []
    assert declare.differences({"US": 3}, {"us": 6, "DIV": 2}) == [("US", 3, 6), ("DIV", 0, 2)]
    assert declare.differences({"US": 1}, None) == []
    assert declare.positive({"US": 2.0, "DIV": 0, "3F": -1}) == {"US": 2}
