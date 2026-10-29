"""The engine on recorded real responses (tests/fixtures), with the clock frozen at recording time."""

import pytest

from bostonmove import engine
from bostonmove.engine import UsageError

pytestmark = pytest.mark.anyio


async def test_resolve_kinds(web):
    assert (await engine.resolve(web, "Park Street"))["kind"] == "mbta station"
    assert (await engine.resolve(web, "Kendall"))["name"] == "Kendall/MIT"
    assert (await engine.resolve(web, "42.3601,-71.0589"))["kind"] == "coordinates"
    fenway_park = await engine.resolve(web, "Fenway Park")
    assert fenway_park["kind"] == "place" and "OpenStreetMap" in fenway_park["source"]


async def test_departures_are_grouped_live_and_sorted(web):
    r = await engine.departures(web, "Park Street")
    assert r["stop"]["id"] == "place-pktrm"
    assert r["kind"] == "live predictions"
    assert r["departures"], "Park Street always has departures at recording time"
    firsts = [min(n["minutes"] for n in g["next"] if n["minutes"] is not None) for g in r["departures"]]
    assert firsts == sorted(firsts)
    for g in r["departures"]:
        assert 1 <= len(g["next"]) <= 3 and g["headsign"]
    assert {g["route"] for g in r["departures"]} >= {"Red"}


async def test_departures_filtered_by_route_name(web):
    r = await engine.departures(web, "Northeastern", route="E")
    assert {g["route"] for g in r["departures"]} == {"Green-E"}


async def test_landmark_uses_nearest_stop_and_says_so(web):
    r = await engine.departures(web, "Fenway Park")
    assert "nearest one" in r["note"]


async def test_route_names(web):
    assert await engine.route_id(web, "red line") == "Red"
    assert await engine.route_id(web, "Orange") == "Orange"
    assert await engine.route_id(web, "E") == "Green-E"
    with pytest.raises(UsageError):
        await engine.route_id(web, "Purple Line")


async def test_alerts_for_a_route(web):
    r = await engine.service_alerts(web, route="Orange")
    assert r["route"] == "Orange" and r["count"] >= len(r["alerts"])
    sev = [a["severity"] or 0 for a in r["alerts"]]
    assert sev == sorted(sev, reverse=True)


async def test_bikes_need_dock_only_returns_stations_with_docks(web):
    r = await engine.bikes(web, "Kendall", need="dock")
    assert r["stations"]
    assert all(s["docks"] > 0 and s["returning"] for s in r["stations"])
    assert [s["metres"] for s in r["stations"]] == sorted(s["metres"] for s in r["stations"])
    for s in r["stations"]:
        assert s["classic"] + s["ebikes"] == s["bikes"]


async def test_bikes_rejects_unknown_need(web):
    with pytest.raises(UsageError):
        await engine.bikes(web, "Kendall", need="scooter")


async def test_plan_south_station_to_kendall_finds_the_red_line(web):
    r = await engine.plan(web, "South Station", "Kendall")
    modes = [o["mode"] for o in r["options"]]
    assert "transit" in modes and "bluebikes" in modes and "walk" in modes
    red = next(o for o in r["options"] if o["mode"] == "transit")
    assert red["route"] == "Red" and red["headsign"] == "Alewife"
    assert red["board"]["id"] == "place-sstat" and red["alight"]["id"] == "place-knncl"
    assert 0 < red["ride_minutes"] < 20
    assert [o["total_minutes"] for o in r["options"]] == sorted(o["total_minutes"] for o in r["options"])
    assert any("Direct routes only" in n for n in r["notes"])


async def test_plan_finds_a_bus_stopping_on_the_right_side(web):
    # Route 1 stops on opposite sides of Mass Ave each way; the option must board toward Harvard.
    r = await engine.plan(web, "Northeastern", "Harvard")
    bus = [o for o in r["options"] if o["mode"] == "transit" and o["route"] == "1"]
    assert bus and bus[0]["headsign"].startswith("Harvard")


async def test_rate_limit_keeps_walking_and_bike_options(web_with):
    w = web_with({"/routes": 429})
    r = await engine.plan(w, "South Station", "42.3601,-71.0589")
    assert {o["mode"] for o in r["options"]} <= {"walk", "bluebikes"}
    assert any("rate limit" in n and "MBTA_API_KEY" in n for n in r["notes"])
    await w._client.aclose()


async def test_unknown_place(web_with):
    w = web_with({"nominatim": (200, [])})  # the geocoder finds nothing
    with pytest.raises(UsageError):
        await engine.resolve(w, "zzqqxx nowhere")
    await w._client.aclose()


async def test_empty_place():
    with pytest.raises(UsageError):
        await engine.resolve(None, "  ")
