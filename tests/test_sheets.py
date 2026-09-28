"""Envoi vers Google Sheets : réponses simulées (aucun appel réseau)."""
import json

import httpx
import pytest

from bot.services import sheets

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


ROW = {"numero": 1, "date": "2026-09-28", "heure": "23:42", "franchise": "Franchisé 1", "livreur": "Livreur 1",
       "adresse": "12 Rue de Rivoli 75004 Paris", "complement": "", "produits": "2 vodka", "prix": 60.0}


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


def test_row_uses_paris_time():
    course = {"id": 7, "delivered_at": "2026-09-28T21:42:00+00:00", "franchise_id": "f", "livreur_id": "l",
              "address": "A", "address_detail": None, "products": "P", "price": "12.50"}
    r = sheets.row(course, {"f": {"display_name": "Franchisé 1"}, "l": {"display_name": "Livreur 2"}})
    assert (r["date"], r["heure"], r["prix"], r["complement"]) == ("2026-09-28", "23:42", 12.5, "")
