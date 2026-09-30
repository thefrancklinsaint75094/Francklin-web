"""Lecture du récap de ventes (/ventes)."""
from bot.services import sales
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "US", "aliases": []}, {"id": 2, "name": "DIV", "aliases": ["divin"]},
               {"id": 3, "name": "MSX", "aliases": []}])
USERS = [{"id": "u-a", "display_name": "Livreur A"}]
NAMES = ["Livreur A", "Livreur B"]


def test_parse_blocks_payments_and_aliases():
    text = ("/ventes\nLivreur A\nEspèces 2 US 60\nvirement 1 divin 30€\n\n"
            "b\nCash 3 x MSX 90\nEsp 1 US 30 euros")
    out = sales.parse(text, CAT, NAMES, USERS)
    assert out.errors == []
    assert [(b.livreur, b.user["id"] if b.user else None) for b in out.blocks] == [("Livreur A", "u-a"), ("Livreur B", None)]
    assert out.blocks[0].lines == [{"pay": "especes", "q": 2, "p": "US", "x": 60.0},
                                   {"pay": "virement", "q": 1, "p": "DIV", "x": 30.0}]
    assert out.blocks[1].lines == [{"pay": "especes", "q": 3, "p": "MSX", "x": 90.0},
                                   {"pay": "especes", "q": 1, "p": "US", "x": 30.0}]
    assert out.count == 4


def test_parse_reports_every_problem():
    text = "Espèces 2 US 60\nLivreur Z\nLivreur A\nEspèces 2 KT 60\nVirement 1 US 35\nEspèces deux US\n"
    out = sales.parse(text, CAT, NAMES, USERS)
    assert out.errors == [
        "ligne 1 : écris d'abord le nom du livreur (ex. « Livreur A »)",
        "ligne 2 : livreur inconnu « Livreur Z »",
        "ligne 4 : produit inconnu « KT »",
        "ligne 5 : prix 35 € — les prix vont de 10 en 10 €",
        "ligne 6 : « Espèces deux US » — attendu : paiement, quantité, produit, prix (ex. Espèces 2 US 60)",
    ]
