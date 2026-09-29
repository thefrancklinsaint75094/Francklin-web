"""Moyen de déplacement : temps de trajet, reconnaissance du métro, stations, ordre du dispatch."""
from datetime import timedelta

import pytest

from bot.services import arrival, stations, transport
from bot.services.broadcast import select_eligible
from bot.timeutil import iso, now_utc

BASTILLE = (48.8532, 2.3691)
NATION = (48.8484, 2.3959)          # ≈ 2 km de Bastille
GARE_DE_LYON = (48.8443, 2.3736)


@pytest.fixture(autouse=True)
def no_stations():
    stations.set_stations([])
    yield
    stations.set_stations([])


def test_travel_minutes_by_mode():
    assert round(transport.travel_minutes(500, "transport"), 1) == 6.7          # à pied
    assert round(transport.travel_minutes(3000, "transport"), 1) == 17.2        # métro : 10 min + 3 km à 25 km/h
    assert transport.travel_minutes(3000, "deux_roues") == 9.0
    assert transport.travel_minutes(3000, None) == 9.0                          # inconnu : comme un deux-roues
    assert round(transport.travel_minutes(3000, "voiture"), 1) == 14.6          # stationnement compris


def test_transport_max_distance():
    assert transport.max_distance_km("transport") == 5
    assert transport.max_distance_km("voiture") == 25


def test_metro_needs_gap_jump_speed_and_two_stations():
    stations.set_stations([(*BASTILLE, "Bastille"), (*NATION, "Nation"), (*GARE_DE_LYON, "Gare de Lyon")])
    hit = transport.looks_like_metro(*BASTILLE, *NATION, seconds=420)
    assert hit == {"from": "Bastille", "to": "Nation", "km": 2.0, "minutes": 7}
    assert transport.looks_like_metro(*BASTILLE, *NATION, seconds=60) is None       # pas de trou
    assert transport.looks_like_metro(*BASTILLE, *NATION, seconds=1800) is None     # 4 km/h : à pied
    assert transport.looks_like_metro(*BASTILLE, 48.8532, 2.3700, seconds=420) is None   # 70 m : pas de saut
    far_from_station = (48.8600, 2.3950)
    assert transport.looks_like_metro(*BASTILLE, *far_from_station, seconds=420) is None
    # Sans liste de stations : trou + saut + vitesse suffisent.
    stations.set_stations([])
    assert transport.looks_like_metro(*BASTILLE, *far_from_station, seconds=420)["from"] is None


def test_parse_idfm_csv_keeps_metro_and_rer_once():
    text = ("﻿geo_point_2d;nom_gares;mode\n"
            "48.8532, 2.3691;Bastille;METRO\n"
            "48.8532, 2.3691;Bastille;METRO\n"
            "48.8443, 2.3736;Gare de Lyon;RER\n"
            "48.8400, 2.3000;Porte;TRAMWAY\n"
            "x;Cassée;METRO\n")
    assert stations.parse_csv(text) == [(48.8532, 2.3691, "Bastille"), (48.8443, 2.3736, "Gare de Lyon")]
    stations.set_stations(stations.parse_csv(text))
    name, dist = stations.nearest(48.8535, 2.3690)
    assert name == "Bastille" and dist < 50


def test_arrival_eta_walks_for_transport():
    assert arrival.eta_minutes(450, None, "transport") == 6        # 4,5 km/h
    assert arrival.eta_minutes(450, None, "deux_roues") == 2       # 15 km/h (comme avant)


def test_dispatch_order_uses_travel_time_and_transport_radius():
    now = now_utc()
    fresh = {"updated_at": iso(now - timedelta(minutes=1))}
    course = (48.8566, 2.3522)                                   # Hôtel de Ville
    walker = {"id": "w", "role": "livreur", "status": "active", "on_duty": True, "transport_mode": "transport"}
    rider = {"id": "r", "role": "livreur", "status": "active", "on_duty": True, "transport_mode": "deux_roues"}
    far_walker = {"id": "f", "role": "livreur", "status": "active", "on_duty": True, "transport_mode": "transport"}
    positions = {"w": {**fresh, "lat": 48.8566, "lon": 2.3660},      # ≈ 1 km à pied : ≈ 13 min
                 "r": {**fresh, "lat": 48.8566, "lon": 2.3860},      # ≈ 2,5 km en deux-roues : ≈ 7 min
                 "f": {**fresh, "lat": 48.8566, "lon": 2.4400}}      # ≈ 6,4 km : trop loin en transport
    out = select_eligible([walker, rider, far_walker], positions, {}, set(), *course, now, 30, 25)
    assert [lv["id"] for lv, _ in out] == ["r", "w"]


def test_final_mode_walk_only_when_clear():
    transport._stats.clear()
    for kmh in (4, 5, 3, 6):
        transport._sample(1, kmh)
    assert transport.final_mode({"id": 1}) == "pied"
    for kmh in (4, 12, 5, 6):                       # un passage à 12 km/h : pas « à pied »
        transport._sample(2, kmh)
    assert transport.final_mode({"id": 2}) is None
    transport._sample(3, 4)
    assert transport.final_mode({"id": 3}) is None  # trop peu de positions
    assert transport.final_mode({"id": 4, "detected_mode": "metro"}) == "metro"
    assert transport._stats == {}
