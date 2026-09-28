from datetime import date, datetime, timezone

from bot.config import PARIS
from bot.timeutil import night_bounds, night_label, night_start_date, parse_ts


def paris(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=PARIS)


def test_before_6am_belongs_to_previous_evening():
    assert night_start_date(paris(2026, 9, 4, 2, 30), 6) == date(2026, 9, 3)


def test_after_6am_belongs_to_next_night():
    # Livrée à 6h05 : nuit suivante.
    assert night_start_date(paris(2026, 9, 4, 6, 5), 6) == date(2026, 9, 4)
    # Envoyée à 17h50, livrée à 18h20 : comptée dans la nuit du 4 au 5.
    assert night_start_date(paris(2026, 9, 4, 18, 20), 6) == date(2026, 9, 4)


def test_bounds_are_6am_to_6am_paris():
    start, end = night_bounds(date(2026, 9, 3), 6)
    assert start == paris(2026, 9, 3, 6).astimezone(timezone.utc)
    assert end == paris(2026, 9, 4, 6).astimezone(timezone.utc)


def test_labels():
    assert night_label(date(2026, 9, 3)) == "3 au 4 septembre"
    assert night_label(date(2026, 9, 30)) == "30 septembre au 1er octobre"


def test_parse_ts_variants():
    a = parse_ts("2026-09-28T18:23:38.12345+00:00")
    b = parse_ts("2026-09-28T18:23:38.123450Z")
    assert a == b and a.tzinfo is not None
    assert parse_ts("2026-09-28T18:23:38+00:00").second == 38
    assert parse_ts(None) is None
