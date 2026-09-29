"""Alerte « le livreur arrive » : vitesse, temps estimé, seuils."""
from datetime import datetime, timedelta, timezone

from bot.services import arrival

NOW = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)


def prev_at(seconds_ago: float, lat=48.8600, lon=2.3500):
    return {"lat": lat, "lon": lon, "updated_at": (NOW - timedelta(seconds=seconds_ago)).isoformat()}


def test_measured_speed():
    # ~111 m vers le nord en 20 s : ~5,6 m/s (20 km/h).
    speed = arrival.measured_speed(prev_at(20), 48.8610, 2.3500, NOW)
    assert 5.4 < speed < 5.8
    assert arrival.measured_speed(None, 48.861, 2.35, NOW) is None
    assert arrival.measured_speed(prev_at(2), 48.861, 2.35, NOW) is None       # trop rapproché
    assert arrival.measured_speed(prev_at(3600), 48.861, 2.35, NOW) is None    # trop ancien
    assert arrival.measured_speed(prev_at(30), 48.8600, 2.3500, NOW) is None   # à l'arrêt
    assert arrival.measured_speed(prev_at(10), 48.9600, 2.3500, NOW) is None   # saut de 11 km


def test_eta_and_threshold():
    assert arrival.eta_minutes(1250, None) == 5          # 15 km/h par défaut
    assert arrival.eta_minutes(1260, None) == 6
    assert arrival.eta_minutes(50, None) == 1
    assert arrival.eta_minutes(3000, 10.0) == 5          # 36 km/h mesurés
    assert arrival.should_notify(400, 9, 500, 5)         # quelques centaines de mètres
    assert arrival.should_notify(1200, 5, 500, 5)        # à 5 minutes
    assert not arrival.should_notify(1500, 6, 500, 5)
