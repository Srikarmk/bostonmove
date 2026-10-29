"""Against the real APIs. Not run by default: uv run pytest -m live"""

import pytest

from bostonmove import engine
from bostonmove.web import Web

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_departures_and_bikes():
    w = Web()
    try:
        r = await engine.departures(w, "Park Street")
        assert r["stop"]["id"] == "place-pktrm"
        b = await engine.bikes(w, "Kendall")
        assert b["stations"]
    finally:
        await w.close()
