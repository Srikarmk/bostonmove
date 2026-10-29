import pytest

from bostonmove import geo


def test_distance_park_street_to_south_station():
    # Park Street (42.35639, -71.0624) to South Station (42.35228, -71.05524): about 750 m
    d = geo.metres(42.35639, -71.0624, 42.35228, -71.05524)
    assert 700 < d < 800


def test_minutes_uses_detour_and_speed():
    assert geo.minutes(1000, geo.WALK_KMH) == pytest.approx(1.3 / 5 * 60)


@pytest.mark.parametrize("text,expected", [
    ("42.3601,-71.0589", (42.3601, -71.0589)),
    (" 42.34 , -71.09 ", (42.34, -71.09)),
    ("Park Street", None),
    ("142.0,-71.0", None),
])
def test_parse_latlon(text, expected):
    assert geo.parse_latlon(text) == expected


@pytest.mark.parametrize("query,name,at_least,below", [
    ("Park Street", "Park Street", 1.0, None),
    ("park st", "Park Street", 1.0, None),
    ("Northeastern", "Northeastern University", 0.9, None),
    ("Kendall", "Kendall/MIT", 0.9, None),
    ("harvard ave", "Harvard Avenue", 1.0, None),
    # A longer query is not a match for a shorter name that it starts with.
    ("Fenway Park", "Fenway", None, 0.8),
    ("Boston Common", "Boston College", None, 0.8),
])
def test_name_score(query, name, at_least, below):
    s = geo.name_score(query, name)
    if at_least is not None:
        assert s >= at_least
    if below is not None:
        assert s < below
