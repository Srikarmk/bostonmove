"""The five operations behind both the CLI and the MCP server.

Every result says where its numbers came from (`sources`) and when (`as_of`). Walking and cycling times
are estimates from straight-line distance; transit ride times come from the MBTA schedule and waits from
live predictions when there are any. Trip options are direct routes only: no transfers yet.
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


async def _transit_option(web, now, route, o_stops, d_stops):
    # Each direction has its own stop order (a bus stops on opposite sides of the street each way).
    orders = await asyncio.gather(*(sources.route_stop_ids(web, route["id"], d) for d in (0, 1)))
    pairs = []
    for direction, order in enumerate(orders):
        idx = {sid: i for i, sid in enumerate(order)}
        pairs += [(a, b, direction) for a in o_stops for b in d_stops
                  if a["id"] in idx and b["id"] in idx and idx[a["id"]] < idx[b["id"]]]
    if not pairs:
        return None
    a, b, direction = min(pairs, key=lambda p: p[0]["metres"] + p[1]["metres"])
    walk_to = geo.minutes(a["metres"], geo.WALK_KMH)
    deps = await sources.predictions(web, a["id"], now, route["id"], direction)
    if not deps:
        deps = await sources.schedules(web, a["id"], now, route["id"], direction, limit=10)
    dep = next((d for d in deps if d["minutes"] is not None and d["minutes"] >= walk_to and d["status"] != "cancelled"),
               None)
    if not dep:
        return None
    times = await sources.trip_stop_times(web, dep["trip"])
    t_on = next((t for t in times if t["station"] == a["id"] or t["stop"] == a["id"]), None)
    t_off = next((t for t in times if (t["station"] == b["id"] or t["stop"] == b["id"])
                  and t_on and t["time"] and t_on["time"] and t["time"] > t_on["time"]), None)
    if not (t_on and t_off):
        return None  # this trip doesn't serve both stops in this order (a branch, or a short run)
    ride = (t_off["time"] - t_on["time"]).total_seconds() / 60
    walk_from = geo.minutes(b["metres"], geo.WALK_KMH)
    total = dep["minutes"] + ride + walk_from
    return {
        "mode": "transit", "route": route["id"], "route_name": route["name"], "type": route["type"],
        "headsign": dep["headsign"],
        "board": {"stop": a["name"], "id": a["id"], "walk_metres": a["metres"], "walk_minutes": round(walk_to)},
        "depart": {"time": dep["time"], "minutes": dep["minutes"], "live": dep["live"]},
        "ride_minutes": round(ride),
        "alight": {"stop": b["name"], "id": b["id"], "walk_metres": b["metres"], "walk_minutes": round(walk_from)},
        "total_minutes": round(total),
        "arrive": (now + timedelta(minutes=total)).isoformat(timespec="minutes"),
    }


async def _transit_options(web: Web, now: datetime, o: dict, d: dict) -> list[dict]:
    o_stops, d_stops = await asyncio.gather(sources.stops_near(web, o["lat"], o["lon"], NEAR_M),
                                            sources.stops_near(web, d["lat"], d["lon"], NEAR_M))
    o_stops, d_stops = o_stops[:30], d_stops[:30]
    r_o, r_d = await asyncio.gather(sources.routes_at(web, [s["id"] for s in o_stops]),
                                    sources.routes_at(web, [s["id"] for s in d_stops]))
    d_ids = {r["id"] for r in r_d}
    shared = sorted([r for r in r_o if r["id"] in d_ids], key=lambda r: ROUTE_ORDER.get(r["type_code"], 9))[:3]
    found = [x for x in [await _transit_option(web, now, r, o_stops, d_stops) for r in shared] if x]
    if found:
        current = await sources.alerts(web, ",".join(x["route"] for x in found))
        for x in found:
            # Only what matters for this ride: serious alerts on the route (severity 5+ of 10, e.g. delays,
            # shuttles, suspensions), or any alert at the boarding or alighting stop.
            mine = {x["board"]["id"], x["alight"]["id"]}
            x["alerts"] = [a["header"] for a in current if x["route"] in a["routes"]
                           and ((a["severity"] or 0) >= 5 or mine & set(a["stops"]))][:2]
    return found


async def plan(web: Web, origin: str, destination: str) -> dict:
    now = now_in_boston(web)
    o, d = await asyncio.gather(resolve(web, origin), resolve(web, destination))
    straight = geo.metres(o["lat"], o["lon"], d["lat"], d["lon"])
    options: list[dict] = []
    notes = [("Walking and cycling times are estimates from straight-line distance; transit times are from MBTA "
              "live predictions, or the timetable when there are none."),
             "Direct routes only: this doesn't plan transfers yet (mbta.com/trip-planner does)."]

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
        options += await _transit_options(web, now, o, d)
        if not any(x["mode"] == "transit" for x in options):
            notes.append(f"No direct MBTA route has a stop within {NEAR_M} m of both ends right now.")
    except Unavailable as e:  # keep the walking and cycling options rather than fail the whole plan
        notes.append(f"Transit options left out: {e}")

    options.sort(key=lambda x: x["total_minutes"])
    srcs = [SRC_MBTA, SRC_BIKES] + ([SRC_OSM] if "place" in (o["kind"], d["kind"]) else [])
    return {"from": _place(o), "to": _place(d), "straight_line_metres": round(straight), "options": options,
            "notes": notes, "as_of": now.isoformat(timespec="seconds"), "sources": srcs}
