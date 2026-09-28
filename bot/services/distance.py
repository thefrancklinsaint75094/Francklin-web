"""Distance à vol d'oiseau (Haversine)."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def format_distance(meters: float) -> str:
    """« ~800 m » sous 1 km (arrondi à 100 m), sinon « ~1,2 km »."""
    if meters < 1000:
        rounded = max(100, int(round(meters / 100.0)) * 100)
        if rounded >= 1000:
            return "~1,0 km"
        return f"~{rounded} m"
    km = round(meters / 1000.0, 1)
    return f"~{km:.1f} km".replace(".", ",")
