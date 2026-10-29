import pytest
from mcp import Client

from bostonmove import server
from bostonmove.server import mcp

pytestmark = pytest.mark.anyio

TOOLS = {"find_nearby", "next_departures", "service_alerts", "bike_availability", "plan_trip"}


@pytest.fixture
async def client(web, monkeypatch):
    monkeypatch.setattr(server, "_web", web)
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


async def test_lists_five_read_only_tools(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.description and len(t.description) < 300 and t.description.startswith("Use ")
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
        assert t.annotations.open_world_hint is True
    assert tools["plan_trip"].input_schema["required"] == ["origin", "destination"]
    assert tools["bike_availability"].input_schema["properties"]["need"]["anyOf"][0]["enum"] == ["bike", "ebike", "dock"]


async def test_next_departures_tool(client):
    res = await client.call_tool("next_departures", {"stop": "Park Street"})
    assert not res.is_error
    data = res.structured_content
    assert data["stop"]["id"] == "place-pktrm" and data["departures"] and data["sources"]


async def test_plan_trip_tool(client):
    res = await client.call_tool("plan_trip", {"origin": "South Station", "destination": "Kendall"})
    assert not res.is_error
    assert any(o["mode"] == "transit" for o in res.structured_content["options"])


async def test_bad_input_is_a_tool_error_not_a_crash(client):
    res = await client.call_tool("service_alerts", {"route": "Purple Line"})
    assert res.is_error
    assert "No MBTA route" in res.content[0].text
