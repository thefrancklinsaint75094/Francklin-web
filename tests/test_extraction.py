"""Extraction : réponses Anthropic simulées (aucun appel réseau)."""
import json
from types import SimpleNamespace

import pytest

from bot.services import extraction
from bot.services.extraction import (
    ExtractionParseError, ExtractionUnavailable, Extractor, looks_like_complement, parse_response, to_order,
)


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, *, model, max_tokens, system, messages, extra_body=None):
        # Signature stricte, comme le SDK anthropic 1.x : un argument retiré
        # (ex. temperature) lève TypeError ici aussi.
        kwargs = dict(model=model, max_tokens=max_tokens, system=system, messages=messages, extra_body=extra_body)
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)])


def make(replies):
    fake = SimpleNamespace(messages=FakeMessages(replies))
    return Extractor("key", "claude-haiku-4-5-20251001", client=fake), fake.messages


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def _sleep(_):
        return None
    monkeypatch.setattr(extraction.asyncio, "sleep", _sleep)


# Les 4 exemples réels du §7.1, avec la réponse qu'Anthropic doit produire.
EXAMPLES = [
    (
        "12 rue de rivoli paris 4, digicode 45A32, 2 vodka + coca, 60€",
        [{"address": "12 rue de Rivoli, 75004 Paris", "address_detail": "digicode 45A32",
          "products": "2 vodka + coca", "price": 60, "requested_time": None}],
    ),
    (
        "Une bouteille de champagne 80 euros\n34 avenue de la republique 75011\n3eme étage gauche, il attend",
        [{"address": "34 avenue de la République, 75011 Paris", "address_detail": "3e étage gauche, il attend",
          "products": "1 bouteille de champagne", "price": 80.0, "requested_time": None}],
    ),
    (
        "75012 rue de charenton 45 / whisky + 2 red bull / 55",
        [{"address": "45 rue de Charenton, 75012 Paris", "address_detail": None,
          "products": "whisky + 2 red bull", "price": 55, "requested_time": None}],
    ),
    (
        "2 commandes :\n- 8 rue oberkampf 75011, 1 jack daniels, 45\n- montreuil 12 rue de paris, 2 rosé + glace, 38€, code 1234B",
        [
            {"address": "8 rue Oberkampf, 75011 Paris", "address_detail": None,
             "products": "1 jack daniels", "price": 45, "requested_time": None},
            {"address": "12 rue de Paris, Montreuil", "address_detail": "code 1234B",
             "products": "2 rosé + glace", "price": 38, "requested_time": None},
        ],
    ),
]


@pytest.mark.parametrize("message,reply", EXAMPLES)
async def test_real_examples(message, reply):
    ex, calls = make([json.dumps(reply, ensure_ascii=False)])
    orders = await ex.extract_text(message)
    assert len(orders) == len(reply)
    assert all(o.valid for o in orders)
    for o, r in zip(orders, reply):
        assert o.address == r["address"]
        assert o.price == float(r["price"])
        assert o.products == r["products"]
    call = calls.calls[0]
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert call["max_tokens"] == 1024
    assert call["extra_body"] == {"temperature": 0}
    assert call["system"] == extraction.SYSTEM_PROMPT
    assert call["messages"] == [{"role": "user", "content": message}]


def test_parse_strips_markdown_fences():
    text = '```json\n[{"address": "1 rue X", "address_detail": null, "products": "gin", "price": 20, "requested_time": null}]\n```'
    assert parse_response(text)[0]["address"] == "1 rue X"


def test_parse_single_object_is_wrapped():
    assert parse_response('{"address": "a"}') == [{"address": "a"}]


def test_parse_empty_array():
    assert parse_response("[]") == []


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        parse_response("Voici la commande : adresse 12 rue")


async def test_retry_once_on_bad_json_then_success():
    ex, calls = make(["pas du json", '[{"address":"1 rue A","products":"gin","price":20}]'])
    orders = await ex.extract_text("x")
    assert len(calls.calls) == 2
    assert orders[0].valid


async def test_two_bad_json_raise_parse_error():
    ex, calls = make(["nope", "toujours pas"])
    with pytest.raises(ExtractionParseError):
        await ex.extract_text("x")
    assert len(calls.calls) == 2


def test_real_sdk_rejects_temperature_keyword():
    """Garde-fou : le vrai SDK installé refuse `temperature` en argument direct."""
    import inspect

    from anthropic.resources.messages import AsyncMessages

    params = inspect.signature(AsyncMessages.create).parameters
    assert "temperature" not in params and "extra_body" in params


async def test_network_error_twice_is_unavailable():
    ex, calls = make([RuntimeError("boom"), RuntimeError("boom")])
    with pytest.raises(ExtractionUnavailable):
        await ex.extract_text("x")
    assert len(calls.calls) == 2


async def test_network_error_then_success():
    ex, calls = make([RuntimeError("boom"), "[]"])
    assert await ex.extract_text("bonjour") == []


def test_missing_price():
    o = to_order({"address": "12 rue de Rivoli", "products": "vodka", "price": None})
    assert o.missing == ["price"]
    assert not o.valid


def test_zero_or_negative_price_is_missing():
    assert to_order({"address": "a", "products": "b", "price": 0}).missing == ["price"]
    assert to_order({"address": "a", "products": "b", "price": -5}).missing == ["price"]


def test_price_as_string():
    assert to_order({"address": "a", "products": "b", "price": "60€"}).price == 60.0
    assert to_order({"address": "a", "products": "b", "price": "12,50"}).price == 12.5


def test_null_strings_are_none():
    o = to_order({"address": "null", "products": "", "price": 10})
    assert o.missing == ["address", "products"]


def test_complement_detection():
    o = to_order({"address": None, "address_detail": "digicode 45B", "products": None, "price": None})
    assert looks_like_complement([o])
    assert not looks_like_complement([to_order({"address": "1 rue A", "products": None, "price": None})])


async def test_image_block():
    ex, calls = make(["[]"])
    await ex.extract_image(b"\x89PNG....", "image/png", caption="commande de Paul")
    content = calls.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1]["text"].startswith(extraction.IMAGE_PROMPT)
    assert "commande de Paul" in content[1]["text"]


def test_missing_fields_text():
    from bot import texts
    o = to_order({"address": "12 rue de Rivoli", "products": "vodka", "price": None})
    assert texts.missing_fields(o) == "Il me manque le prix pour : 12 rue de Rivoli."
    o = to_order({"address": "12 rue de Rivoli", "products": None, "price": None})
    assert texts.missing_fields(o) == "Il me manque les produits et le prix pour : 12 rue de Rivoli."
