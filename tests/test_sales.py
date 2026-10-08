"""Lecture du récap de ventes (/ventes)."""
from bot.services import sales
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "US", "aliases": []}, {"id": 2, "name": "DIV", "aliases": ["divin"]},
               {"id": 3, "name": "MSX", "aliases": []}])
USERS = [{"id": "u-a", "display_name": "Livreur A"}]
NAMES = ["Livreur A", "Livreur B"]


def test_parse_cash_by_default_and_cb_or_virement_anywhere():
    text = ("/ventes\nLivreur A\n2 US 60\nCB 1 divin 30€\n\n"
            "b\n3 x MSX 90\n1 virement US 30 euros\n2 US CB 60\nEspèces 1 US 30")
    out = sales.parse(text, CAT, NAMES, USERS)
    assert out.errors == []
    assert [(b.livreur, b.user["id"] if b.user else None) for b in out.blocks] == [("Livreur A", "u-a"), ("Livreur B", None)]
    assert out.blocks[0].lines == [{"pay": "especes", "q": 2, "p": "US", "x": 60.0},
                                   {"pay": "virement", "q": 1, "p": "DIV", "x": 30.0}]
    assert out.blocks[1].lines == [{"pay": "especes", "q": 3, "p": "MSX", "x": 90.0},
                                   {"pay": "virement", "q": 1, "p": "US", "x": 30.0},
                                   {"pay": "virement", "q": 2, "p": "US", "x": 60.0},
                                   {"pay": "especes", "q": 1, "p": "US", "x": 30.0}]
    assert out.count == 6


def test_parse_reports_every_problem():
    text = "2 US 60\nLivreur Z\nLivreur A\n2 KT 60\nCB 1 US 35\nCB deux US\n2 US\n"
    out = sales.parse(text, CAT, NAMES, USERS)
    assert out.errors == [
        "ligne 1 : écris d'abord le nom du livreur (ex. « Livreur A »)",
        "ligne 2 : livreur inconnu « Livreur Z »",
        "ligne 4 : produit inconnu « KT »",
        "ligne 5 : prix 35 € — les prix vont de 10 en 10 €",
        "ligne 6 : « CB deux US » — attendu : quantité, produit, prix (ex. 2 US 60, ou CB 2 US 60)",
        "ligne 7 : « 2 US » — attendu : quantité, produit, prix (ex. 2 US 60, ou CB 2 US 60)",
    ]


def test_parse_accepts_free_products():
    out = sales.parse("Livreur A\n1 US 0\n2 DIV 0€\nCB 1 MSX 0", CAT, NAMES, USERS)
    assert out.errors == []
    assert [(line["p"], line["x"], line["pay"]) for line in out.blocks[0].lines] == [
        ("US", 0.0, "especes"), ("DIV", 0.0, "especes"), ("MSX", 0.0, "virement")]


def test_target_night_for_a_late_recap():
    from datetime import date

    wed = date(2026, 10, 7)                                   # nuit du mercredi 7 au jeudi 8
    assert sales.target_night("lundi", wed) == date(2026, 10, 5)
    assert sales.target_night("Mardi", wed) == date(2026, 10, 6)
    assert sales.target_night("mercredi", wed) == wed         # le jour même : la nuit en cours
    assert sales.target_night("jeudi", wed) == date(2026, 10, 1)   # le jeudi d'avant
    assert sales.target_night("dim", wed) == date(2026, 10, 4)
    assert sales.target_night("hier", wed) == date(2026, 10, 6)
    assert sales.target_night("aujourd’hui", wed) == wed
    assert sales.target_night("demain", wed) is None and sales.target_night("lu", wed) is None
