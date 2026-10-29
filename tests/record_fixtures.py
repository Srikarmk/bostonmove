"""Re-record the fixtures the offline tests replay. Run from the repo root:

    uv run python tests/record_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from fakeweb import FIXTURES, Recorder

from bostonmove import engine
from bostonmove.web import UA, Web

SCENARIOS = [
    ("departures", ("Park Street",), {}),
    ("departures", ("Northeastern",), {"route": "E"}),
    ("departures", ("Fenway Park",), {}),
    ("service_alerts", (), {"route": "Orange"}),
    ("bikes", ("Kendall",), {"need": "dock"}),
    ("near", ("Fenway Park",), {}),
    ("plan", ("South Station", "Kendall"), {}),
    ("plan", ("Northeastern", "Harvard"), {}),
    ("plan", ("Back Bay", "42.3601,-71.0589"), {}),
]


# The Bluebikes data licence allows the data in a product but not as a stand-alone dataset, so the recorded
# feeds keep only the stations the tests use: central Boston and Cambridge.
BOX = (42.33, 42.38, -71.13, -71.04)  # lat min, lat max, lon min, lon max


def trim_bike_feeds():
    keep = None
    for f in FIXTURES.glob("*.json"):
        saved = json.loads(f.read_text())
        if saved.get("key", "").endswith("station_information.json?"):
            st = saved["body"]["data"]["stations"]
            st[:] = [x for x in st if BOX[0] <= x["lat"] <= BOX[1] and BOX[2] <= x["lon"] <= BOX[3]]
            keep = {x["station_id"] for x in st}
            f.write_text(json.dumps(saved))
    for f in FIXTURES.glob("*.json"):
        saved = json.loads(f.read_text())
        if saved.get("key", "").endswith("station_status.json?"):
            st = saved["body"]["data"]["stations"]
            st[:] = [x for x in st if x["station_id"] in keep]
            f.write_text(json.dumps(saved))
    return len(keep or ())


async def main():
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    FIXTURES.mkdir()
    os.environ["BOSTONMOVE_CACHE_DIR"] = tempfile.mkdtemp()
    now = time.time()
    (FIXTURES / "now.json").write_text(json.dumps({"now": now}))
    client = httpx.AsyncClient(transport=Recorder(), headers={"User-Agent": UA}, timeout=30)
    web = Web(client, now=lambda: now)
    for name, args, kwargs in SCENARIOS:
        await getattr(engine, name)(web, *args, **kwargs)
        print("recorded", name, args, kwargs)
    await client.aclose()
    print(len(list(FIXTURES.glob("*.json"))) - 1, "responses;", trim_bike_feeds(), "Bluebikes stations kept")


if __name__ == "__main__":
    if sys.argv[1:] == ["--trim-only"]:
        print(trim_bike_feeds(), "Bluebikes stations kept")
    else:
        asyncio.run(main())
