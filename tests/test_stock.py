"""Alertes de stock du livreur : calculs, textes et lecture via le script."""
import json

import httpx
import pytest

from bot import texts
from bot.services import sheets, stock
from bot.services.catalog import Catalog

CAT = Catalog([{"id": 1, "name": "US", "aliases": []}, {"id": 2, "name": "DIV", "aliases": []}])
LIVREUR = {"display_name": "Livreur 1", "real_name": "Karim"}


def test_course_quantities():
    assert stock.course_quantities({"products": "2 us (60 €) + 1 DIV (30 €)", "price": 90}, CAT) == {"US": 2, "DIV": 1}
    assert stock.course_quantities({"products": "2 US", "price": 60}, CAT) == {"US": 2}


def test_assignment_warnings():
    s = {"US": 2, "div": 5, "KT": 0}
    assert stock.assignment_warnings({"US": 2}, s) == [("US", 2, 2.0)]            # les 2 derniers
    assert stock.assignment_warnings({"US": 3, "DIV": 1}, s) == [("US", 3, 2.0)]  # pas assez
    assert stock.assignment_warnings({"KT": 1, "AMN": 1}, s) == [("KT", 1, 0.0), ("AMN", 1, 0.0)]
    assert stock.assignment_warnings({"DIV": 4}, s) == []


def test_emptied_after_delivery():
    assert stock.emptied_after_delivery({"US": 2, "DIV": 1}, {"US": 2, "DIV": 5}) == ["US"]
    assert stock.emptied_after_delivery({"US": 1}, {"US": 2}) == []


def test_alert_texts():
    course = {"id": 142}
    text = texts.stock_assignment_alert(course, LIVREUR, "Livreur A",
                                        [("US", 2, 2.0), ("KT", 1, 0.0), ("DIV", 3, 1.0)])
    assert text.splitlines() == [
        "⚠️ Stock — course #142 (Livreur 1 (Livreur A))",
        "• Ce sont les 2 derniers US de Livreur 1 (Livreur A)",
        "• Livreur 1 (Livreur A) n'a plus de KT sur lui (1 commandé)",
        "• Livreur 1 (Livreur A) n'a que 1 DIV sur lui pour 3 commandés",
    ]
    assert texts.stock_empty_alert(course, LIVREUR, "Livreur 1", ["US"]) == (
        "📭 Livreur 1 n'a plus de US sur lui (course #142 livrée). Pense à le recharger (/recharge).")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/abc/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s3cret")


async def test_fetch_stock(configured):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "livreur": "Livreur A", "stock": {"US": 2, "DIV": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await sheets.fetch_stock(LIVREUR, client=c) == ({"US": 2, "DIV": 0}, "Livreur A")
    assert seen == [{"secret": "s3cret", "action": "stock", "livreur": "Livreur 1", "livreur_nom": "Karim"}]

    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "livreur": "Livreur 9", "stock": None}))) as c:
        assert await sheets.fetch_stock(LIVREUR, client=c) == (None, "Livreur 9")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="<html><title>Fout</title></html>"))) as c:
        assert await sheets.fetch_stock(LIVREUR, client=c) == (None, "Livreur 1")


def test_inflight_and_deltas():
    stock._inflight.clear()
    stock.add_inflight("l1", "course:1", {"US": -2})
    stock.add_inflight("l1", "restock:5", {"us": 3, "DIV": 1})
    assert stock.inflight_deltas("l1") == {"US": -2, "us": 3, "DIV": 1}
    assert stock.apply_deltas({"US": 2, "KT": 1}, stock.inflight_deltas("l1")) == {"US": 3.0, "KT": 1, "DIV": 1.0}
    stock.remove_inflight("l1", "course:1")
    assert stock.inflight_deltas("l1") == {"us": 3, "DIV": 1}
    # Un mouvement jamais confirmé finit par être oublié.
    stock.add_inflight("l1", "course:9", {"DIV": -1})
    stock._inflight["l1"]["restock:5"] = (0.0, {"us": 3, "DIV": 1})
    assert stock.inflight_deltas("l1") == {"DIV": -1}
    stock._inflight.clear()


def test_last_one_text():
    assert texts.stock_assignment_alert({"id": 7}, LIVREUR, "Livreur 1", [("US", 1, 1.0)]).splitlines()[1] == (
        "• C'est le dernier US de Livreur 1")
