"""MCP server front end (stdio). Five read-only tools over the same engine as the CLI."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, engine
from .engine import UsageError
from .web import Unavailable, Web

INSTRUCTIONS = (
    "bostonmove answers getting-around questions for greater Boston from live public data: MBTA departures and "
    "service alerts (subway, bus, commuter rail, ferry) and Bluebikes bike and dock counts. Places can be station "
    "names ('Park Street'), stop ids, addresses or landmarks ('Fenway Park'), or 'lat,lon'. Every result has "
    "'sources' and 'as_of'. Walking and cycling times are estimates; plan_trip's MBTA options are one route, or "
    "one change between subway and light-rail lines. It only reads public data."
)

mcp = MCPServer("bostonmove", instructions=INSTRUCTIONS, version=__version__)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
_web: Web | None = None

Place = Annotated[
    str,
    Field(description="A station or stop name, an MBTA stop id, an address or landmark in greater Boston, or 'lat,lon'.",
          min_length=1, max_length=200),
]


def web() -> Web:
    global _web
    if _web is None:
        _web = Web()
    return _web


async def _run(coro) -> dict[str, Any]:
    try:
        return await coro
    except (UsageError, Unavailable) as e:
        raise ToolError(str(e)) from None


@mcp.tool(title="Find transit stops and bike stations nearby", annotations=READ_ONLY)
async def find_nearby(
    place: Place,
    radius_m: Annotated[int, Field(description="Search radius in metres.", ge=100, le=1500)] = 400,
) -> dict[str, Any]:
    """Use to see what's near a place: MBTA stations and bus stops with walking distance, and Bluebikes stations with live bike and dock counts."""
    return await _run(engine.near(web(), place, radius_m))


@mcp.tool(title="Next departures from a stop", annotations=READ_ONLY)
async def next_departures(
    stop: Annotated[str, Field(description="A station or stop name or id, e.g. 'Northeastern', 'place-pktrm', '110'. "
                                           "A landmark or address uses its nearest stop.", min_length=1, max_length=200)],
    route: Annotated[str | None, Field(description="Only this route, e.g. 'Red', 'Green-E', '1', 'CR-Worcester'.")] = None,
) -> dict[str, Any]:
    """Use for "when's the next train/bus": upcoming departures at a stop by route and direction, live when the MBTA has predictions (else the timetable), plus alerts at that stop."""
    return await _run(engine.departures(web(), stop, route))


@mcp.tool(title="Current MBTA service alerts", annotations=READ_ONLY)
async def service_alerts(
    route: Annotated[str | None, Field(description="A route, e.g. 'Red', 'Orange', 'Green-B', '39'.")] = None,
    stop: Annotated[str | None, Field(description="A station or stop name or id.")] = None,
) -> dict[str, Any]:
    """Use to check for delays, suspensions, shuttles, detours and closures in effect now, for a route, a stop, or the whole system, most severe first."""
    return await _run(engine.service_alerts(web(), route, stop))


@mcp.tool(title="Bluebikes availability", annotations=READ_ONLY)
async def bike_availability(
    place: Place,
    need: Annotated[Literal["bike", "ebike", "dock"] | None,
                    Field(description="Only stations with a bike, an e-bike, or a free dock.")] = None,
) -> dict[str, Any]:
    """Use to find a Bluebikes bike or a free dock: the nearest stations with live counts of bikes, e-bikes and empty docks."""
    return await _run(engine.bikes(web(), place, need))


@mcp.tool(title="Ways to get from A to B", annotations=READ_ONLY)
async def plan_trip(origin: Place, destination: Place) -> dict[str, Any]:
    """Use for "how do I get from A to B": walking, Bluebikes and MBTA options with arrival times, using live departures and bike counts. MBTA options are one route, or one change between subway and light-rail lines (Red, Orange, Blue, Green, Mattapan); changes involving buses aren't planned."""
    return await _run(engine.plan(web(), origin, destination))


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request would flood the client's log
    mcp.run()
