"""The MBTA V3 API and the Bluebikes GBFS feed, turned into plain records."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .geo import metres
from .web import DAY, Web

BOSTON = ZoneInfo("America/New_York")

ROUTE_TYPES = {0: "light rail", 1: "subway", 2: "commuter rail", 3: "bus", 4: "ferry"}
CANCELLED = {"CANCELLED", "SKIPPED"}


def _included(doc: dict) -> dict[tuple[str, str], dict]:
    return {(i["type"], i["id"]): i for i in doc.get("included", [])}


def _rel(item: dict, name: str) -> str | None:
    data = (item.get("relationships", {}).get(name) or {}).get("data")
    return data.get("id") if isinstance(data, dict) else None


def route_name(a: dict) -> str | None:
    """'Red Line', 'Green Line E', '1', 'Framingham/Worcester Line': rail by its long name, a bus by its number."""
    if a.get("type") in (0, 1, 2, 4):
        return a.get("long_name") or a.get("short_name")
    return a.get("short_name") or a.get("long_name")


def when(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


# ---------------------------------------------------------------------------------------------- MBTA


def _stop(d: dict) -> dict:
    a = d["attributes"]
    return {"id": d["id"], "name": a["name"], "lat": a["latitude"], "lon": a["longitude"],
            "municipality": a.get("municipality"), "parent": _rel(d, "parent_station"),
            "station": a.get("location_type") == 1, "vehicle_type": ROUTE_TYPES.get(a.get("vehicle_type")),
            "wheelchair": {1: True, 2: False}.get(a.get("wheelchair_boarding"))}


async def stations(web: Web) -> list[dict]:
    """Every MBTA station (subway, light rail, commuter rail, ferry), for matching names."""
    doc = await web.mbta("/stops", {"filter[location_type]": "1"}, ttl=DAY, disk=True)
    return [_stop(d) for d in doc["data"]]


async def stop(web: Web, stop_id: str) -> dict | None:
    doc = await web.mbta("/stops", {"filter[id]": stop_id}, ttl=DAY, disk=True)
    return _stop(doc["data"][0]) if doc["data"] else None


async def stops_near(web: Web, lat: float, lon: float, radius_m: float) -> list[dict]:
    """Stations and bus stops within radius_m, nearest first. A platform is folded into its station."""
    doc = await web.mbta("/stops", {
        "filter[latitude]": f"{lat:.5f}", "filter[longitude]": f"{lon:.5f}",
        "filter[radius]": f"{radius_m / 111_000:.5f}", "filter[location_type]": "0,1", "sort": "distance",
        "page[limit]": "60",
    }, ttl=DAY, disk=True)
    out: dict[str, dict] = {}
    for d in doc["data"]:
        s = _stop(d)
        key = s["parent"] or s["id"]
        if key not in out:
            if s["parent"]:
                s = {**s, "id": s["parent"], "station": True, "parent": None}
            s["metres"] = round(metres(lat, lon, s["lat"], s["lon"]))
            out[key] = s
        else:
            out[key].setdefault("vehicle_type", s["vehicle_type"])
    return sorted(out.values(), key=lambda s: s["metres"])


async def routes_at(web: Web, stop_ids: list[str]) -> list[dict]:
    if not stop_ids:
        return []
    doc = await web.mbta("/routes", {"filter[stop]": ",".join(stop_ids)}, ttl=DAY, disk=True)
    return [{"id": d["id"], "type": ROUTE_TYPES.get(d["attributes"]["type"]), "type_code": d["attributes"]["type"],
             "name": route_name(d["attributes"]),
             "long_name": d["attributes"]["long_name"],
             "directions": d["attributes"]["direction_names"],
             "destinations": d["attributes"]["direction_destinations"]} for d in doc["data"]]


async def route_stops(web: Web, route: str, direction: int) -> list[dict]:
    """A route's stops in travel order for one direction (stations for rail, stops for bus), as id and name."""
    doc = await web.mbta("/stops", {"filter[route]": route, "filter[direction_id]": str(direction)},
                         ttl=DAY, disk=True)
    return [{"id": d["id"], "name": d["attributes"]["name"]} for d in doc["data"]]


def _departure(p: dict, inc: dict, now: datetime, live: bool) -> dict | None:
    a = p["attributes"]
    t = when(a.get("departure_time") or a.get("arrival_time"))
    status = a.get("schedule_relationship")
    if t is None and status not in CANCELLED:
        return None
    if t is not None and (t - now).total_seconds() < -60:  # already left
        return None
    trip = inc.get(("trip", _rel(p, "trip") or ""), {}).get("attributes", {})
    route_id = _rel(p, "route")
    route = inc.get(("route", route_id or ""), {}).get("attributes", {})
    return {
        "route": route_id, "route_name": route_name(route) or route_id,
        "direction_id": a.get("direction_id"),
        "headsign": trip.get("headsign") or (route.get("direction_destinations") or [None, None])[a.get("direction_id") or 0],
        "time": t.isoformat(timespec="minutes") if t else None,
        "minutes": max(0, round((t - now).total_seconds() / 60)) if t else None,
        "live": live, "status": a.get("status") or ("cancelled" if status in CANCELLED else None),
        "trip": _rel(p, "trip"),
    }


async def predictions(web: Web, stop_id: str, now: datetime, route: str | None = None,
                      direction: int | None = None) -> list[dict]:
    doc = await web.mbta("/predictions", {
        "filter[stop]": stop_id, "filter[route]": route,
        "filter[direction_id]": None if direction is None else str(direction),
        "include": "trip,route", "sort": "departure_time",
    }, ttl=15)
    inc = _included(doc)
    rows = [_departure(p, inc, now, live=True) for p in doc["data"]]
    return [r for r in rows if r]


async def schedules(web: Web, stop_id: str, now: datetime, route: str | None = None,
                    direction: int | None = None, limit: int = 20) -> list[dict]:
    """Timetable departures from now on today, for when there are no live predictions."""
    doc = await web.mbta("/schedules", {
        "filter[stop]": stop_id, "filter[route]": route,
        "filter[direction_id]": None if direction is None else str(direction),
        "filter[date]": now.date().isoformat(), "filter[min_time]": now.strftime("%H:%M"),
        "include": "trip,route", "sort": "departure_time", "page[limit]": str(limit),
    }, ttl=300)
    inc = _included(doc)
    rows = [_departure(p, inc, now, live=False) for p in doc["data"]]
    return [r for r in rows if r]


async def trips_between(web: Web, a_id: str, b_id: str, now: datetime, route: str, direction: int,
                        live: bool = True) -> list[dict]:
    """Trips on one route and direction that call at stop a and then at stop b, soonest first: the departure
    from a (as in `predictions`) plus the ride time to b. One request for both stops, paired up by trip: live
    predictions, or the next 90 minutes of the timetable when `live` is False."""
    params = {"filter[stop]": f"{a_id},{b_id}", "filter[route]": route, "filter[direction_id]": str(direction),
              "include": "trip,route,stop"}
    if live:
        doc = await web.mbta("/predictions", params, ttl=15)
    else:
        start = now.hour * 60 + now.minute
        end = start + 90  # past midnight the timetable counts on, e.g. 24:40
        doc = await web.mbta("/schedules", {**params, "filter[date]": now.date().isoformat(),
                                            "filter[min_time]": f"{start // 60:02d}:{start % 60:02d}",
                                            "filter[max_time]": f"{end // 60:02d}:{end % 60:02d}"}, ttl=300)
    inc = _included(doc)
    calls: dict[str, dict[str, dict]] = {}
    for p in doc["data"]:
        sid = _rel(p, "stop") or ""
        at = {sid, _rel(inc.get(("stop", sid), {}), "parent_station")}  # a platform, and its station
        end_ = a_id if a_id in at else b_id if b_id in at else None
        if end_:
            calls.setdefault(_rel(p, "trip") or "", {})[end_] = p
    out = []
    for c in calls.values():
        if a_id not in c or b_id not in c:
            continue
        aa, ba = c[a_id]["attributes"], c[b_id]["attributes"]
        t_a = when(aa.get("departure_time") or aa.get("arrival_time"))
        t_b = when(ba.get("arrival_time") or ba.get("departure_time"))
        dep = _departure(c[a_id], inc, now, live)
        if dep and t_a and t_b and t_b > t_a:
            out.append({**dep, "ride_minutes": (t_b - t_a).total_seconds() / 60})
    return sorted(out, key=lambda r: r["minutes"])


async def alerts(web: Web, route: str | None = None, stop_id: str | None = None) -> list[dict]:
    """Alerts in effect right now."""
    doc = await web.mbta("/alerts", {"filter[datetime]": "NOW", "filter[route]": route, "filter[stop]": stop_id},
                         ttl=60)
    out = []
    for d in doc["data"]:
        a = d["attributes"]
        routes = sorted({e["route"] for e in a.get("informed_entity", []) if e.get("route")})
        stops = sorted({e["stop"] for e in a.get("informed_entity", []) if e.get("stop")})
        out.append({"id": d["id"], "effect": a["effect"].replace("_", " ").lower(), "severity": a.get("severity"),
                    "header": a.get("short_header") or a.get("header"), "routes": routes, "stops": stops,
                    "lifecycle": (a.get("lifecycle") or "").lower() or None,
                    "updated": a.get("updated_at"), "url": a.get("url")})
    return sorted(out, key=lambda x: -(x["severity"] or 0))


# ---------------------------------------------------------------------------------------------- Bluebikes


async def bike_stations(web: Web) -> tuple[list[dict], str]:
    """Installed Bluebikes stations with live counts, and when the feed was last updated (Boston time, ISO)."""
    info = await web.gbfs("station_information", disk=True, ttl=DAY)
    status = await web.gbfs("station_status")
    live = {s["station_id"]: s for s in status["data"]["stations"]}
    out = []
    for s in info["data"]["stations"]:
        st = live.get(s["station_id"])
        if not st or not st.get("is_installed"):
            continue
        bikes, ebikes = st.get("num_bikes_available", 0), st.get("num_ebikes_available", 0)
        out.append({
            "id": s["station_id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
            "capacity": s.get("capacity"),
            # In this feed num_bikes_available counts every bike, e-bikes included.
            "bikes": bikes, "ebikes": ebikes, "classic": max(0, bikes - ebikes),
            "docks": st.get("num_docks_available", 0),
            "renting": bool(st.get("is_renting")), "returning": bool(st.get("is_returning")),
            "reported": datetime.fromtimestamp(st["last_reported"], BOSTON).isoformat(timespec="seconds")
            if st.get("last_reported", 0) > DAY else None,
        })
    updated = datetime.fromtimestamp(status.get("last_updated", 0), BOSTON).isoformat(timespec="seconds")
    return out, updated


def nearest(items: list[dict], lat: float, lon: float, limit: int, within_m: float | None = None) -> list[dict]:
    rows = [{**x, "metres": round(metres(lat, lon, x["lat"], x["lon"]))} for x in items]
    rows = [r for r in rows if within_m is None or r["metres"] <= within_m]
    return sorted(rows, key=lambda r: r["metres"])[:limit]
