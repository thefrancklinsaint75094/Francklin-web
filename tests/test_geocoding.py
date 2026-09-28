"""Géocodage BAN : réponses simulées via httpx.MockTransport."""
import httpx
import pytest

from bot.services import geocoding
from bot.services.geocoding import GeocodingUnavailable, build_params, district, geocode, in_zone, parse_response

ALLOWED = ("75", "77", "78", "91", "92", "93", "94", "95")


def feature(label, postcode, city, lon, lat, score=0.9, type_="housenumber"):
    return {
        "type": "FeatureCollection",
        "features": [{
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"label": label, "postcode": postcode, "city": city, "score": score, "type": type_},
        }],
    }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def _sleep(_):
        return None
    monkeypatch.setattr(geocoding.asyncio, "sleep", _sleep)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_district_paris():
    assert district("75001", "Paris") == "Paris 1er"
    assert district("75004", "Paris") == "Paris 4e"
    assert district("75012", "Paris") == "Paris 12e"
    assert district("75020", "Paris") == "Paris 20e"
    assert district("75116", "Paris") == "Paris 16e"


def test_district_banlieue():
    assert district("93100", "Montreuil") == "Montreuil 93100"


def test_zone():
    assert in_zone("75011", ALLOWED)
    assert in_zone("93100", ALLOWED)
    assert not in_zone("69003", ALLOWED)
    assert not in_zone("60000", ALLOWED)
    assert not in_zone("", ALLOWED)


def test_params_with_postcode():
    p = build_params("12 rue de Rivoli, 75004 Paris")
    assert p == {"q": "12 rue de Rivoli, 75004 Paris", "limit": 1, "autocomplete": 0, "postcode": "75004"}


def test_params_without_postcode():
    assert "postcode" not in build_params("12 rue de Paris, Montreuil")


def test_parse_order_lon_lat():
    r = parse_response(feature("12 Rue de Rivoli 75004 Paris", "75004", "Paris", 2.3577, 48.8556))
    assert r.lat == 48.8556 and r.lon == 2.3577
    assert r.label == "12 Rue de Rivoli 75004 Paris"
    assert r.has_housenumber


def test_parse_low_score_is_none():
    assert parse_response(feature("x", "75004", "Paris", 2.3, 48.8, score=0.3)) is None


def test_parse_no_features():
    assert parse_response({"features": []}) is None


def test_street_only_flagged():
    r = parse_response(feature("Rue de Charenton 75012 Paris", "75012", "Paris", 2.38, 48.84, type_="street"))
    assert r is not None and not r.has_housenumber


async def test_geocode_sends_expected_query():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=feature("8 Rue Oberkampf 75011 Paris", "75011", "Paris", 2.37, 48.86))

    async with client_for(handler) as c:
        r = await geocode("8 rue Oberkampf, 75011 Paris", client=c)
    assert r.postcode == "75011"
    assert seen[0]["postcode"] == "75011" and seen[0]["autocomplete"] == "0" and seen[0]["limit"] == "1"


async def test_geocode_retries_without_wrong_postcode():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        if "postcode" in request.url.params:
            return httpx.Response(200, json={"features": []})
        return httpx.Response(200, json=feature("12 Rue de Paris 93100 Montreuil", "93100", "Montreuil", 2.44, 48.86))

    async with client_for(handler) as c:
        r = await geocode("12 rue de Paris 93000", client=c)
    assert r.city == "Montreuil"
    assert len(calls) == 2


async def test_geocode_out_of_zone_result_is_returned():
    def handler(request):
        return httpx.Response(200, json=feature("3 Rue de la République 69002 Lyon", "69002", "Lyon", 4.83, 45.76))

    async with client_for(handler) as c:
        r = await geocode("3 rue de la république lyon", client=c)
    assert not in_zone(r.postcode, ALLOWED)


async def test_geocode_unavailable_after_two_attempts():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(503)

    async with client_for(handler) as c:
        with pytest.raises(GeocodingUnavailable):
            await geocode("12 rue de Rivoli", client=c)
    assert count == 2


async def test_geocode_recovers_on_second_attempt():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(200, json=feature("12 Rue de Rivoli 75004 Paris", "75004", "Paris", 2.35, 48.85))

    async with client_for(handler) as c:
        r = await geocode("12 rue de Rivoli", client=c)
    assert r is not None
