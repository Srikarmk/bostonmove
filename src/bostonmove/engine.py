"""The five operations behind both the CLI and the MCP server.

Every result says where its numbers came from (`sources`) and when (`as_of`). Walking and cycling times
are estimates from straight-line distance; transit ride times come from the MBTA schedule and waits from
live predictions when there are any. Trip options are one MBTA route, or one change between subway and
light-rail lines.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import geo, sources
from .web import DAY, Unavailable, Web

BOSTON = ZoneInfo("America/New_York")
STOP_ID = re.compile(r"^(place-[a-z0-9-]+|\d{1,6}|[A-Z]{2,}-\d{3,})$")
# Order to try shared routes in: subway, light rail, commuter rail, ferry, then bus.
ROUTE_ORDER = {1: 0, 0: 1, 2: 2, 4: 3, 3: 4}
NEAR_M = 800  # how far either end of a trip may be from a stop or a bike station
RAIL = {0, 1}  # light rail and subway: the lines a trip may change between
TRANSFER_MIN = 3  # minutes allowed to change platforms
MIN_PER_STOP = 2  # rough minutes per stop, only for ranking which changes to price

SRC_MBTA = "MBTA V3 API (api-v3.mbta.com)"
SRC_BIKES = "Bluebikes GBFS feed (gbfs.bluebikes.com)"
SRC_OSM = "OpenStreetMap Nominatim; data © OpenStreetMap contributors (openstreetmap.org/copyright)"


class UsageError(ValueError):
    """The request can't be answered as asked; the message says what to change."""


def now_in_boston(web: Web) -> datetime:
    return datetime.fromtimestamp(web.now(), BOSTON)


# ---------------------------------------------------------------------------------------------- places


async def resolve(web: Web, text: str) -> dict:
    """A place from coordinates, an MBTA stop id, a station or Bluebikes station name, or an address."""
    text = (text or "").strip()
    if not text:
        raise UsageError("Give a place: a station or stop name, an address or landmark, or 'lat,lon'.")
    ll = geo.parse_latlon(text)
    if ll:
        return {"name": text, "lat": ll[0], "lon": ll[1], "kind": "coordinates", "source": "given"}
    if STOP_ID.match(text):
        s = await sources.stop(web, text)
        if s:
            return {"name": s["name"], "lat": s["lat"], "lon": s["lon"], "kind": "mbta stop", "id": s["id"],
                    "source": SRC_MBTA}

    stations, (bikes, _) = await asyncio.gather(sources.stations(web), sources.bike_stations(web))
    best_t = max(stations, key=lambda s: geo.name_score(text, s["name"]), default=None)
    best_b = max(bikes, key=lambda s: geo.name_score(text, s["name"]), default=None)
    st = geo.name_score(text, best_t["name"]) if best_t else 0
    sb = geo.name_score(text, best_b["name"]) if best_b else 0
    if best_t and st >= 0.8 and st >= sb:
        return {"name": best_t["name"], "lat": best_t["lat"], "lon": best_t["lon"], "kind": "mbta station",
                "id": best_t["id"], "source": SRC_MBTA}
    if best_b and sb >= 0.8:
        return {"name": best_b["name"], "lat": best_b["lat"], "lon": best_b["lon"], "kind": "bluebikes station",
                "id": best_b["id"], "source": SRC_BIKES}
    hits = await web.geocode(text)
    if hits:
        h = hits[0]
        return {"name": h.get("display_name", text), "lat": float(h["lat"]), "lon": float(h["lon"]),
                "kind": "place", "source": SRC_OSM}
    raise UsageError(f"Couldn't find {text!r} in greater Boston. Try a station name, a fuller address, or 'lat,lon'.")


def _place(p: dict) -> dict:
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in p.items()}


def _stop_row(s: dict) -> dict:
    return {"id": s["id"], "name": s["name"], "metres": s["metres"],
            "kind": "station" if s["station"] else "stop", "vehicle_type": s.get("vehicle_type"),
            "wheelchair_accessible": s.get("wheelchair")}


def _bike_row(b: dict) -> dict:
    return {"id": b["id"], "name": b["name"], "metres": b["metres"], "bikes": b["bikes"], "ebikes": b["ebikes"],
            "classic": b["classic"], "docks": b["docks"], "renting": b["renting"], "returning": b["returning"]}


async def near(web: Web, place: str, radius_m: int = 400, limit: int = 6) -> dict:
    p = await resolve(web, place)
    stops, (bikes, bikes_at) = await asyncio.gather(
        sources.stops_near(web, p["lat"], p["lon"], radius_m), sources.bike_stations(web))
    return {
        "place": _place(p),
        "transit": [_stop_row(s) for s in stops[:limit]],
        "bikes": [_bike_row(b) for b in sources.nearest(bikes, p["lat"], p["lon"], limit, within_m=radius_m * 2)],
        "as_of": {"transit": "stop locations, refreshed daily", "bikes": bikes_at},
        "sources": [SRC_MBTA, SRC_BIKES] + ([SRC_OSM] if p["kind"] == "place" else []),
    }


# ---------------------------------------------------------------------------------------------- departures


async def _mbta_stop(web: Web, text: str) -> tuple[dict, str | None]:
    p = await resolve(web, text)
    if p["kind"] in ("mbta station", "mbta stop"):
        return {"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"]}, None
    stops = await sources.stops_near(web, p["lat"], p["lon"], 400)
    if not stops:
        raise UsageError(f"No MBTA stop within 400 m of {p['name']}. Try find_nearby with a larger radius.")
    s = stops[0]
    return ({"id": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"]},
            f"{text!r} isn't a stop, so this is the nearest one, {s['metres']} m away.")


async def route_id(web: Web, text: str) -> str:
    """'Red', 'red line', 'E', 'Green Line E', '1', 'CR-Worcester', 'Framingham/Worcester' → an MBTA route id."""
    doc = await web.mbta("/routes", {}, ttl=DAY, disk=True)
    t = text.strip()
    for d in doc["data"]:
        if d["id"].lower() == t.lower():
            return d["id"]
    short = {"b": "Green-B", "c": "Green-C", "d": "Green-D", "e": "Green-E"}
    key = geo.norm(t).replace(" line", "").replace("green ", "").strip()
    if key in short:
        return short[key]

    def score(d):
        a = d["attributes"]
        names = [d["id"], a.get("short_name") or "", a.get("long_name") or ""]
        return max(geo.name_score(t, n) for n in names if n)

    best = max(doc["data"], key=score)
    if score(best) < 0.8:
        raise UsageError(f"No MBTA route called {text!r}. Use an id like 'Red', 'Green-E', '1' or 'CR-Worcester'.")
    return best["id"]


async def departures(web: Web, stop: str, route: str | None = None, limit: int = 10) -> dict:
    now = now_in_boston(web)
    s, note = await _mbta_stop(web, stop)
    rid = await route_id(web, route) if route else None
    rows = await sources.predictions(web, s["id"], now, rid)
    live = bool(rows)
    if not rows:
        rows = await sources.schedules(web, s["id"], now, rid, limit=30)
    groups: dict[tuple, dict] = {}
    for r in rows:
        k = (r["route"], r["direction_id"], r["headsign"])
        g = groups.setdefault(k, {"route": r["route"], "route_name": r["route_name"], "headsign": r["headsign"],
                                  "direction_id": r["direction_id"], "next": []})
        if len(g["next"]) < 3:
            g["next"].append({"minutes": r["minutes"], "time": r["time"], "live": r["live"], "status": r["status"]})
    ordered = sorted(groups.values(), key=lambda g: min((n["minutes"] for n in g["next"] if n["minutes"] is not None),
                                                       default=10**6))
    stop_alerts = await sources.alerts(web, stop_id=s["id"])
    out = {
        "stop": s, "departures": ordered[:limit], "alerts": stop_alerts[:5],
        "as_of": now.isoformat(timespec="seconds"),
        "kind": "live predictions" if live else "timetable (no live predictions right now)",
        "sources": [SRC_MBTA],
    }
    if note:
        out["note"] = note
    if not ordered:
        out["note"] = (out.get("note", "") + " No more departures today from this stop"
                       + (f" on route {rid}." if rid else ".")).strip()
    return out


async def service_alerts(web: Web, route: str | None = None, stop: str | None = None, limit: int = 15) -> dict:
    rid = await route_id(web, route) if route else None
    s = (await _mbta_stop(web, stop))[0] if stop else None
    rows = await sources.alerts(web, rid, s["id"] if s else None)
    return {"route": rid, "stop": s, "count": len(rows), "alerts": rows[:limit],
            "as_of": now_in_boston(web).isoformat(timespec="seconds"), "sources": [SRC_MBTA]}


# ---------------------------------------------------------------------------------------------- bikes


async def bikes(web: Web, place: str, need: str | None = None, limit: int = 5) -> dict:
    if need not in (None, "bike", "ebike", "dock"):
        raise UsageError("need is one of 'bike', 'ebike' or 'dock'.")
    p = await resolve(web, place)
    rows, updated = await sources.bike_stations(web)
    if need == "bike":
        rows = [r for r in rows if r["renting"] and r["bikes"] > 0]
    elif need == "ebike":
        rows = [r for r in rows if r["renting"] and r["ebikes"] > 0]
    elif need == "dock":
        rows = [r for r in rows if r["returning"] and r["docks"] > 0]
    found = sources.nearest(rows, p["lat"], p["lon"], limit, within_m=2000)
    return {
        "place": _place(p), "need": need, "stations": [_bike_row(r) for r in found],
        "as_of": updated, "note": "Counts are as of the feed time; a bike or dock can be taken before you arrive.",
        "sources": [SRC_BIKES] + ([SRC_OSM] if p["kind"] == "place" else []),
    }


# ---------------------------------------------------------------------------------------------- trips


def _route_index(stops: list[dict]) -> dict[str, int]:
    return {s["id"]: i for i, s in enumerate(stops)}


async def _ride(web: Web, now: datetime, route: dict, a: dict, b: dict, direction: int, ready: float):
    """The first trip on `route` from stop a to stop b that leaves a at least `ready` minutes from now, as a leg
    and the minutes from now it reaches b. Live predictions first, then the timetable."""
    dep = None
    for live in (True, False):
        rows = await sources.trips_between(web, a["id"], b["id"], now, route["id"], direction, live)
        dep = next((r for r in rows if r["minutes"] >= ready and r["status"] != "cancelled"), None)
        if dep:
            break
    if not dep:
        return None
    leg = {"route": route["id"], "route_name": route["name"], "type": route["type"], "headsign": dep["headsign"],
           "board": {"stop": a["name"], "id": a["id"]},
           "depart": {"time": dep["time"], "minutes": dep["minutes"], "live": dep["live"]},
           "ride_minutes": round(dep["ride_minutes"]), "alight": {"stop": b["name"], "id": b["id"]}}
    return leg, dep["minutes"] + dep["ride_minutes"]


def _transit(now: datetime, a: dict, b: dict, legs: list[tuple[dict, float]]) -> dict:
    walk_from = geo.minutes(b["metres"], geo.WALK_KMH)
    total = legs[-1][1] + walk_from
    return {
        "mode": "transit", "transfers": len(legs) - 1,
        "walk_to": {"stop": a["name"], "metres": a["metres"], "minutes": round(geo.minutes(a["metres"], geo.WALK_KMH))},
        "legs": [leg for leg, _ in legs],
        "walk_from": {"stop": b["name"], "metres": b["metres"], "minutes": round(walk_from)},
        "total_minutes": round(total),
        "arrive": (now + timedelta(minutes=total)).isoformat(timespec="minutes"),
    }


async def _direct_option(web, now, route, o_stops, d_stops):
    # Each direction has its own stop order (a bus stops on opposite sides of the street each way).
    orders = await asyncio.gather(*(sources.route_stops(web, route["id"], d) for d in (0, 1)))
    pairs = []
    for direction, order in enumerate(orders):
        idx = _route_index(order)
        pairs += [(a, b, direction) for a in o_stops for b in d_stops
                  if a["id"] in idx and b["id"] in idx and idx[a["id"]] < idx[b["id"]]]
    if not pairs:
        return None
    a, b, direction = min(pairs, key=lambda p: p[0]["metres"] + p[1]["metres"])
    ride = await _ride(web, now, route, a, b, direction, geo.minutes(a["metres"], geo.WALK_KMH))
    return _transit(now, a, b, [ride]) if ride else None


async def _transfer_options(web, now, o_stops, d_stops, r_o, r_d, limit=2):
    """One change between subway and light-rail lines: a line near the start to a station it shares with a
    line near the destination. Candidates are ranked by walking plus stops travelled, and the best `limit`
    pairs of lines are priced with live departures."""
    o_ids, d_ids = {r["id"] for r in r_o}, {r["id"] for r in r_d}
    first = [r for r in r_o if r["type_code"] in RAIL and r["id"] not in d_ids]
    second = [r for r in r_d if r["type_code"] in RAIL and r["id"] not in o_ids]
    if not first or not second:
        return []
    keys = [(r["id"], d) for r in {r["id"]: r for r in first + second}.values() for d in (0, 1)]
    stops = dict(zip(keys, await asyncio.gather(*(sources.route_stops(web, rid, d) for rid, d in keys))))
    idx = {k: _route_index(v) for k, v in stops.items()}
    names = {s["id"]: s["name"] for v in stops.values() for s in v}

    best: dict[tuple, tuple] = {}
    for r1 in first:
        for r2 in second:
            for d1 in (0, 1):
                i1 = idx[(r1["id"], d1)]
                for d2 in (0, 1):
                    i2 = idx[(r2["id"], d2)]
                    shared = [x for x in i1 if x in i2]
                    for a in (s for s in o_stops if s["id"] in i1):
                        for b in (s for s in d_stops if s["id"] in i2):
                            for x in shared:
                                if not (i1[a["id"]] < i1[x] and i2[x] < i2[b["id"]]):
                                    continue
                                score = (geo.minutes(a["metres"] + b["metres"], geo.WALK_KMH)
                                         + MIN_PER_STOP * (i1[x] - i1[a["id"]] + i2[b["id"]] - i2[x]))
                                k = (r1["id"], r2["id"])
                                if k not in best or score < best[k][0]:
                                    best[k] = (score, r1, r2, a, x, b, d1, d2)

    found = []
    for _, r1, r2, a, x, b, d1, d2 in sorted(best.values(), key=lambda c: c[0])[:limit]:
        change = {"id": x, "name": names[x]}
        leg1 = await _ride(web, now, r1, a, change, d1, geo.minutes(a["metres"], geo.WALK_KMH))
        if not leg1:
            continue
        leg2 = await _ride(web, now, r2, change, b, d2, leg1[1] + TRANSFER_MIN)
        if leg2:
            found.append(_transit(now, a, b, [leg1, leg2]))
    return found


async def _transit_options(web: Web, now: datetime, o: dict, d: dict, notes: list[str]) -> list[dict]:
    o_stops, d_stops = await asyncio.gather(sources.stops_near(web, o["lat"], o["lon"], NEAR_M),
                                            sources.stops_near(web, d["lat"], d["lon"], NEAR_M))
    o_stops, d_stops = o_stops[:30], d_stops[:30]
    r_o, r_d = await asyncio.gather(sources.routes_at(web, [s["id"] for s in o_stops]),
                                    sources.routes_at(web, [s["id"] for s in d_stops]))
    d_ids = {r["id"] for r in r_d}
    shared = sorted([r for r in r_o if r["id"] in d_ids], key=lambda r: ROUTE_ORDER.get(r["type_code"], 9))[:3]
    found = []
    for r in shared:
        x = await _direct_option(web, now, r, o_stops, d_stops)
        if x:
            found.append(x)
    # A change is only worth pricing when no subway or light-rail line goes the whole way; each one costs
    # several more requests against the MBTA's keyless limit.
    rail = {r["id"] for r in shared if r["type_code"] in RAIL}
    if not any(x["legs"][0]["route"] in rail for x in found):
        try:
            found += await _transfer_options(web, now, o_stops, d_stops, r_o, r_d)
        except Unavailable as e:
            notes.append(f"Options with a change left out: {e}")
    if found:
        try:
            current = await sources.alerts(web, ",".join(sorted({leg["route"] for x in found for leg in x["legs"]})))
        except Unavailable as e:
            current = []
            notes.append(f"Service alerts left out: {e}")
        for x in found:
            # Only what matters for this ride: serious alerts on its routes (severity 5+ of 10, e.g. delays,
            # shuttles, suspensions), or any alert at a stop where it boards, changes or gets off.
            routes = {leg["route"] for leg in x["legs"]}
            mine = {leg[e]["id"] for leg in x["legs"] for e in ("board", "alight")}
            x["alerts"] = [a["header"] for a in current if routes & set(a["routes"])
                           and ((a["severity"] or 0) >= 5 or mine & set(a["stops"]))][:2]
    return found


async def plan(web: Web, origin: str, destination: str) -> dict:
    now = now_in_boston(web)
    o, d = await asyncio.gather(resolve(web, origin), resolve(web, destination))
    straight = geo.metres(o["lat"], o["lon"], d["lat"], d["lon"])
    options: list[dict] = []
    notes = [("Walking and cycling times are estimates from straight-line distance; transit times are from MBTA "
              "live predictions, or the timetable when there are none."),
             (f"MBTA options are one route, or one change between subway and light-rail lines with {TRANSFER_MIN} "
              "min to change; changes involving buses aren't planned (mbta.com/trip-planner does them).")]

    if straight <= 5000:
        w = geo.minutes(straight, geo.WALK_KMH)
        options.append({"mode": "walk", "metres": round(straight * geo.DETOUR), "total_minutes": round(w),
                        "arrive": (now + timedelta(minutes=w)).isoformat(timespec="minutes"), "estimate": True})

    bikes_rows, bikes_at = await sources.bike_stations(web)
    start = sources.nearest([b for b in bikes_rows if b["renting"] and b["bikes"] > 0], o["lat"], o["lon"], 1, NEAR_M)
    end = sources.nearest([b for b in bikes_rows if b["returning"] and b["docks"] > 0], d["lat"], d["lon"], 1, NEAR_M)
    if start and end and start[0]["id"] != end[0]["id"]:
        a, b = start[0], end[0]
        ride = geo.minutes(geo.metres(a["lat"], a["lon"], b["lat"], b["lon"]), geo.BIKE_KMH)
        total = geo.minutes(a["metres"], geo.WALK_KMH) + ride + geo.minutes(b["metres"], geo.WALK_KMH)
        options.append({
            "mode": "bluebikes",
            "pick_up": {"station": a["name"], "walk_metres": a["metres"], "bikes": a["bikes"], "ebikes": a["ebikes"]},
            "drop_off": {"station": b["name"], "walk_metres": b["metres"], "docks": b["docks"]},
            "ride_minutes": round(ride), "total_minutes": round(total),
            "arrive": (now + timedelta(minutes=total)).isoformat(timespec="minutes"), "estimate": True,
            "counts_as_of": bikes_at})
    elif not start:
        notes.append(f"No Bluebikes station with a bike within {NEAR_M} m of the start.")
    elif not end:
        notes.append(f"No Bluebikes station with a free dock within {NEAR_M} m of the destination.")

    try:
        options += await _transit_options(web, now, o, d, notes)
        if not any(x["mode"] == "transit" for x in options):
            notes.append(f"No MBTA option right now with stops within {NEAR_M} m of both ends and at most one "
                         "change between subway lines.")
    except Unavailable as e:  # keep the walking and cycling options rather than fail the whole plan
        notes.append(f"Transit options left out: {e}")

    options.sort(key=lambda x: x["total_minutes"])
    srcs = [SRC_MBTA, SRC_BIKES] + ([SRC_OSM] if "place" in (o["kind"], d["kind"]) else [])
    return {"from": _place(o), "to": _place(d), "straight_line_metres": round(straight), "options": options,
            "notes": notes, "as_of": now.isoformat(timespec="seconds"), "sources": srcs}
