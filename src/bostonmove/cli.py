"""Command line front end.

  bostonmove next "Park Street" [--route Red]   next departures from a stop
  bostonmove alerts [--route Red] [--stop X]    service alerts in effect now
  bostonmove bikes "Kendall" [--need dock]      nearest Bluebikes stations with counts
  bostonmove near "Fenway Park"                 stops and bike stations near a place
  bostonmove plan "Northeastern" "Harvard"      walking, Bluebikes and direct MBTA options
  bostonmove mcp                                run as an MCP server (Claude Code, Cursor, Claude Desktop)

Add --json to any command for the full result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__, engine
from .engine import UsageError
from .web import Unavailable, Web


def _t(iso: str | None) -> str:
    return iso[11:16] if iso else "--:--"


def _when(n: dict) -> str:
    if n.get("status") == "cancelled":
        return "cancelled"
    m = n["minutes"]
    s = "now" if m == 0 else f"{m} min"
    return s + ("" if n["live"] else " (sched)")


def render(cmd: str, r: dict) -> str:
    out: list[str] = []
    if cmd == "next":
        out.append(f"{r['stop']['name']}  ·  {r['kind']}  ·  {_t(r['as_of'])}")
        if r.get("note"):
            out.append(r["note"])
        for g in r["departures"]:
            out.append(f"  {g['route_name']:>10}  to {g['headsign'] or '?':<28} " + ", ".join(_when(n) for n in g["next"]))
        for a in r["alerts"]:
            out.append(f"  ! {a['header']}")
    elif cmd == "alerts":
        scope = r["route"] or (r["stop"] or {}).get("name") or "all MBTA service"
        out.append(f"{r['count']} alert(s) in effect for {scope}")
        for a in r["alerts"]:
            out.append(f"  [{a['effect']}] {a['header']}")
    elif cmd == "bikes":
        out.append(f"Bluebikes near {r['place']['name'][:60]}  ·  counts at {_t(r['as_of'])} UTC")
        for s in r["stations"]:
            out.append(f"  {s['metres']:>5} m  {s['name'][:44]:<44} bikes {s['bikes']:>2} (e {s['ebikes']})  docks {s['docks']:>2}")
    elif cmd == "near":
        out.append(r["place"]["name"][:80])
        for s in r["transit"]:
            out.append(f"  {s['metres']:>5} m  {s['name']}  ({s['vehicle_type'] or s['kind']})")
        for s in r["bikes"]:
            out.append(f"  {s['metres']:>5} m  Bluebikes: {s['name']}  bikes {s['bikes']}, docks {s['docks']}")
    elif cmd == "plan":
        out.append(f"{r['from']['name'][:40]}  →  {r['to']['name'][:40]}  ({r['straight_line_metres']} m straight line)")
        for o in r["options"]:
            if o["mode"] == "walk":
                out.append(f"  {o['total_minutes']:>3} min  walk, about {o['metres']} m  (arrive ~{_t(o['arrive'])})")
            elif o["mode"] == "bluebikes":
                out.append(f"  {o['total_minutes']:>3} min  Bluebikes from {o['pick_up']['station']} "
                           f"({o['pick_up']['bikes']} bikes) to {o['drop_off']['station']} "
                           f"({o['drop_off']['docks']} docks)  (arrive ~{_t(o['arrive'])})")
            else:
                live = "" if o["depart"]["live"] else ", scheduled"
                out.append(f"  {o['total_minutes']:>3} min  {o['route_name']} to {o['headsign']}: board at "
                           f"{o['board']['stop']} ({o['board']['walk_minutes']} min walk) in {o['depart']['minutes']} min"
                           f"{live}, ride {o['ride_minutes']}, off at {o['alight']['stop']}  (arrive ~{_t(o['arrive'])})")
                for a in o.get("alerts", []):
                    out.append(f"           ! {a}")
        out += [f"  · {n}" for n in r["notes"]]
    return "\n".join(out)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bostonmove", description="MBTA departures and alerts, Bluebikes, and trip "
                                "options for greater Boston, from live public data. Read-only.")
    p.add_argument("--version", action="version", version=f"bostonmove {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    j = argparse.ArgumentParser(add_help=False)
    j.add_argument("--json", action="store_true", help="print the full result as JSON")
    s = sub.add_parser("next", parents=[j], help="next departures from a stop")
    s.add_argument("stop")
    s.add_argument("--route")
    s = sub.add_parser("alerts", parents=[j], help="service alerts in effect now")
    s.add_argument("--route")
    s.add_argument("--stop")
    s = sub.add_parser("bikes", parents=[j], help="nearest Bluebikes stations")
    s.add_argument("place")
    s.add_argument("--need", choices=["bike", "ebike", "dock"])
    s = sub.add_parser("near", parents=[j], help="stops and bike stations near a place")
    s.add_argument("place")
    s.add_argument("--radius", type=int, default=400)
    s = sub.add_parser("plan", parents=[j], help="ways to get from one place to another")
    s.add_argument("origin")
    s.add_argument("destination")
    sub.add_parser("mcp", help="run the MCP server over stdio")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.command == "mcp":
        from .server import main as serve
        serve()
        return 0

    async def go():
        web = Web()
        try:
            if a.command == "next":
                return await engine.departures(web, a.stop, a.route)
            if a.command == "alerts":
                return await engine.service_alerts(web, a.route, a.stop)
            if a.command == "bikes":
                return await engine.bikes(web, a.place, a.need)
            if a.command == "near":
                return await engine.near(web, a.place, a.radius)
            return await engine.plan(web, a.origin, a.destination)
        finally:
            await web.close()

    try:
        r = asyncio.run(go())
    except (UsageError, Unavailable) as e:
        print(f"bostonmove: {e}", file=sys.stderr)
        return 2
    print(json.dumps(r, indent=2, ensure_ascii=False) if a.json else render(a.command, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
