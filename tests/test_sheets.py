"""Envoi vers Google Sheets : réponses simulées (aucun appel réseau)."""
import json

import httpx
import pytest

from bot.services import sheets
from bot.services.catalog import Catalog
from bot.services.rules_extraction import parse_template

URL = "https://script.google.com/macros/s/abc/exec"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", URL)
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s3cret")

    async def no_sleep(_):
        return None
    monkeypatch.setattr(sheets.asyncio, "sleep", no_sleep)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


ROW = {"numero": 1, "onglet": "Lundi", "vendeur": "Franchisé 1", "livreur": "Livreur 1", "statut": "OK",
       "adresse": "12 Rue de Rivoli 75004 Paris", "lignes": [{"produit": "DIV", "qte": 2, "prix": 60.0}], "prix": 60.0}
CAT = Catalog([{"id": i, "name": n, "aliases": []} for i, n in enumerate(["DIV", "DOUCE", "KT", "CHAMPAGNE", "US"])])


async def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_WEBHOOK_URL", raising=False)
    assert not sheets.enabled()
    assert await sheets.send_rows([ROW]) == 0


async def test_posts_secret_and_follows_apps_script_redirect(configured):
    seen = []

    def handler(request):
        if request.method == "POST":
            seen.append(json.loads(request.content))
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/echo?x=1"})
        return httpx.Response(200, json={"ok": True, "added": 1})

    async with client(handler) as c:
        assert await sheets.send_rows([ROW], client=c) == 1
    assert seen == [{"secret": "s3cret", "rows": [ROW]}]


async def test_script_refusal_is_an_error(configured):
    async with client(lambda r: httpx.Response(200, json={"ok": False, "error": "secret"})) as c:
        with pytest.raises(RuntimeError, match="secret"):
            await sheets.send_rows([ROW], client=c)


async def test_retry_once_then_success(configured):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True, "added": 1})

    async with client(handler) as c:
        assert await sheets.send_rows([ROW], client=c) == 1
    assert len(calls) == 2


def test_row_goes_to_the_night_tab_with_status_ok():
    # Livrée lundi 28 septembre 2026 à 23h42 (Paris) → onglet Lundi.
    course = {"id": 7, "delivered_at": "2026-09-28T21:42:00+00:00", "franchise_id": "f", "livreur_id": "l",
              "address": "A", "address_detail": "digicode 1234", "products": "2 DIV", "price": "12.50"}
    users = {"f": {"display_name": "Franchisé 1", "real_name": "Merlin"}, "l": {"display_name": "Livreur 2"}}
    r = sheets.row(course, users)
    assert (r["onglet"], r["date"], r["heure"], r["statut"]) == ("Lundi", "2026-09-28", "23:42", "OK")
    assert (r["vendeur"], r["vendeur_nom"], r["livreur"], r["livreur_nom"]) == ("Franchisé 1", "Merlin", "Livreur 2", "")
    assert r["lignes"] == [{"produit": "DIV", "qte": 2, "prix": 12.5}]
    assert "digicode" not in str(r)  # le complément d'adresse ne va jamais dans la feuille


def test_delivery_before_6am_belongs_to_previous_night():
    # Mardi 29 septembre à 3h (Paris) → nuit du lundi.
    assert sheets.day_tab("2026-09-29T01:00:00+00:00") == "Lundi"
    # Mardi 29 septembre à 7h (Paris) → Mardi.
    assert sheets.day_tab("2026-09-29T05:00:00+00:00") == "Mardi"
    assert sheets.day_tab("2026-10-04T20:00:00+00:00") == "Dimanche"


def test_product_lines_from_template_order():
    order = parse_template("12 rue de Rivoli 75004 Paris\n2 div 60\n1 kt 5\n1 douce 10\n1 us 20", CAT)[0]
    assert sheets.product_lines(order["products"], float(order["price"]), CAT) == [
        {"produit": "DIV", "qte": 2, "prix": 60.0},
        {"produit": "KT", "qte": 1, "prix": 5.0},
        {"produit": "DOUCE", "qte": 1, "prix": 10.0},
        {"produit": "US", "qte": 1, "prix": 20.0},
    ]


def test_product_lines_free_text_puts_total_on_first_line():
    assert sheets.product_lines("2 champagne, div x3", 90.0, CAT) == [
        {"produit": "CHAMPAGNE", "qte": 2, "prix": 90.0},
        {"produit": "DIV", "qte": 3, "prix": ""},
    ]
    assert sheets.product_lines("50 mousseux", 60.0, CAT) == [{"produit": "mousseux", "qte": 50, "prix": 60.0}]
    assert sheets.product_lines("Coca 1,5L", 5.0) == [{"produit": "Coca 1,5L", "qte": 1, "prix": 5.0}]


def test_line_prices_that_do_not_add_up_fall_back_to_total():
    assert sheets.product_lines("1 DIV (10 €) + 1 KT (5 €)", 20.0) == [
        {"produit": "DIV", "qte": 1, "prix": 20.0},
        {"produit": "KT", "qte": 1, "prix": ""},
    ]
    assert sheets.product_lines("1 DIV (12,50 €) + 1 KT (5 €)", 17.5)[0]["prix"] == 12.5


async def test_login_page_is_explained(configured):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(302, headers={"Location": "https://accounts.google.com/ServiceLogin?continue=x"})
        return httpx.Response(200, text="<html><title>Google Accounts</title></html>")

    async with client(handler) as c:
        with pytest.raises(RuntimeError, match="Qui a accès : Tout le monde"):
            await sheets.send_rows([ROW], client=c)


async def test_other_html_page_shows_its_title(configured):
    async with client(lambda r: httpx.Response(500, text="<html><title>Erreur interne</title></html>")) as c:
        with pytest.raises(RuntimeError, match=r"HTTP 500\) : « Erreur interne »"):
            await sheets.send_rows([ROW], client=c)
    page = "<html><body>Authorization is required to perform that action.</body></html>"
    async with client(lambda r: httpx.Response(200, text=page)) as c:
        with pytest.raises(RuntimeError, match="doit être autorisé"):
            await sheets.send_rows([ROW], client=c)


async def test_dutch_google_error_page_shows_its_text(configured):
    page = ("<html><head><title>Fout</title><style>p{}</style></head><body><div>"
            "TypeError: Cannot read properties of undefined (reading &#39;x&#39;) (regel 42, bestand Code)"
            "</div></body></html>")
    async with client(lambda r: httpx.Response(200, text=page)) as c:
        with pytest.raises(RuntimeError, match=r"« TypeError: Cannot read properties of undefined \(reading 'x'\)"):
            await sheets.send_rows([ROW], client=c)
    page = "<html><title>Fout</title><body>Autorisatie is vereist om die actie uit te voeren.</body></html>"
    async with client(lambda r: httpx.Response(200, text=page)) as c:
        with pytest.raises(RuntimeError, match="doit être autorisé.*Autorisatie is vereist"):
            await sheets.send_rows([ROW], client=c)
