from bot.services.distance import format_distance, haversine_m


def test_same_point_is_zero():
    assert haversine_m(48.85, 2.35, 48.85, 2.35) == 0


def test_known_distance_paris():
    # Hôtel de Ville → Place de la République : ~1,5 km à vol d'oiseau
    d = haversine_m(48.8566, 2.3522, 48.8674, 2.3636)
    assert 1400 < d < 1600


def test_paris_montreuil():
    d = haversine_m(48.8566, 2.3522, 48.8638, 2.4485)
    assert 6500 < d < 7500


def test_symmetry():
    a = haversine_m(48.80, 2.30, 48.90, 2.40)
    b = haversine_m(48.90, 2.40, 48.80, 2.30)
    assert abs(a - b) < 1e-6


def test_format_meters_rounded_to_100():
    assert format_distance(780) == "~800 m"
    assert format_distance(20) == "~100 m"
    assert format_distance(949) == "~900 m"


def test_format_km_one_decimal_french():
    assert format_distance(1200) == "~1,2 km"
    assert format_distance(3449) == "~3,4 km"
    assert format_distance(12000) == "~12,0 km"


def test_format_just_below_km():
    assert format_distance(990) == "~1,0 km"
