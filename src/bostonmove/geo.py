"""Distances, travel-time estimates and name matching. No network here."""

from __future__ import annotations

import difflib
import math
import re

# Straight-line distance times DETOUR approximates the street distance. Speeds are typical, not measured:
# every time built from them is labelled an estimate.
DETOUR = 1.3
WALK_KMH = 5.0
BIKE_KMH = 14.0

LATLON = re.compile(r"^\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def minutes(straight_m: float, kmh: float) -> float:
    return straight_m * DETOUR / 1000 / kmh * 60


def parse_latlon(text: str) -> tuple[float, float] | None:
    m = LATLON.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def norm(name: str) -> str:
    s = name.lower().replace("&", " and ").replace("@", " at ")
    s = re.sub(r"\b(st|street)\b\.?", "st", s)
    s = re.sub(r"\b(sq|square)\b\.?", "sq", s)
    s = re.sub(r"\b(ave|avenue)\b\.?", "ave", s)
    s = re.sub(r"\b(univ|university)\b\.?", "university", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_score(query: str, name: str) -> float:
    """1.0 for an exact match, then prefix, then all query words present, then fuzzy similarity."""
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    # The query may be short for the name ("Northeastern"), never longer: "Fenway Park" is not "Fenway".
    if n.startswith(q + " "):
        return 0.9
    qw, nw = q.split(), set(n.split())
    if all(any(w == x or (len(w) >= 3 and x.startswith(w)) for x in nw) for w in qw):
        return 0.8
    return 0.7 * difflib.SequenceMatcher(None, q, n).ratio()
