"""Prénoms des livreurs dans les messages : « Livreur A (Ketur) »."""
import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services import names
from bot.services.sales import resolve_livreur


@pytest.fixture(autouse=True)
def prenoms():
    names.set_cache({"Livreur A": "Ketur", "Livreur C": "Layla", "Livreur D": "<Jo>", "Livreur B": "livreur b"})
    yield
    names.set_cache({})


def test_decorate_text():
    assert names.decorate("📦 <b>Livreur A</b> · Box 1") == "📦 <b>Livreur A</b> (Ketur) · Box 1"
    assert names.decorate("Livreur A, Livreur C et Livreur AB") == "Livreur A (Ketur), Livreur C (Layla) et Livreur AB"
    assert names.decorate("Bannir Livreur A (Ketur) ?") == "Bannir Livreur A (Ketur) ?"          # déjà précisé
    assert names.decorate("Exemple :\n<code>Livreur A\n2 US 60</code>\nLivreur A") == \
        "Exemple :\n<code>Livreur A\n2 US 60</code>\nLivreur A (Ketur)"                   # pas dans les exemples
    assert names.decorate("Livreur D") == "Livreur D (&lt;Jo&gt;)"                        # échappé (HTML)
    assert names.decorate("Livreur B") == "Livreur B"                                     # prénom = nom : ignoré
    assert names.decorate_plain("👤 Livreur D") == "👤 Livreur D (<Jo>)"


def test_decorate_buttons_keep_callbacks():
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("👤 Livreur A", callback_data="assign_to:1:u")],
                                   [InlineKeyboardButton("Annuler", callback_data="x")]])
    out = names.decorate_markup(markup)
    assert [(b.text, b.callback_data) for row in out.inline_keyboard for b in row] == [
        ("👤 Livreur A (Ketur)", "assign_to:1:u"), ("Annuler", "x")]
    assert names.decorate_markup(None) is None


def test_lookup_by_prenom_or_displayed_name():
    assert names.label("Livreur A") == "Livreur A (Ketur)" and names.label("Livreur X") == "Livreur X"
    assert names.by_prenom("ketur") == "Livreur A" and names.by_prenom("Paul") is None
    assert names.strip_prenom("Livreur A (Ketur)") == "Livreur A"
    sheet = ["Livreur A", "Livreur B", "Livreur C"]
    assert resolve_livreur("Ketur", sheet, []) == ("Livreur A", None)
    assert resolve_livreur("livreur c (layla)", sheet, []) == ("Livreur C", None)
    assert resolve_livreur("C", sheet, []) == ("Livreur C", None)
