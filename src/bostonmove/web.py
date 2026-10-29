"""HTTP for the three public sources, with caching and each source's rate rules.

- MBTA V3 API: 20 requests a minute without a key, 1,000 with a free one (MBTA_API_KEY).
- Bluebikes GBFS feed: no key; the feed's own `ttl` says how long a copy stays fresh.
- OpenStreetMap Nominatim (place names to coordinates): at most one request a second, an identifying
  User-Agent, and results cached, per its usage policy.

Live data (predictions, alerts, bike counts) is cached in memory for under a minute. Slow-changing data
(station lists, a route's stop order, geocoded places) is also kept on disk for a day.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from . import __version__

MBTA = "https://api-v3.mbta.com"
GBFS = "https://gbfs.lyft.com/gbfs/1.1/bos/en"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = f"bostonmove/{__version__} (+https://github.com/Srikarmk/bostonmove)"

DAY = 24 * 3600
MAX_WAIT = 20  # seconds to wait out the MBTA's per-minute limit before giving up


class Unavailable(Exception):
    """A source could not be reached or refused the request."""


def cache_dir() -> Path | None:
    if os.environ.get("BOSTONMOVE_NO_CACHE"):
        return None
    d = os.environ.get("BOSTONMOVE_CACHE_DIR")
    return Path(d) if d else Path.home() / ".cache" / "bostonmove"


class Web:
    """One per process (or per test). `client` can be an httpx.AsyncClient with a mock transport."""

    def __init__(self, client: httpx.AsyncClient | None = None, now=time.time):
        self._client = client
        self._own = client is None
        self.now = now
        self._mem: dict[str, tuple[float, Any]] = {}
        self._geo_lock = asyncio.Lock()
        self._geo_last = 0.0

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True)
        return self._client

    async def close(self):
        if self._own and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ cache

    def _disk(self, key: str) -> Path | None:
        d = cache_dir()
        return d / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json") if d else None

    def _cached(self, key: str, ttl: float, disk: bool) -> Any | None:
        hit = self._mem.get(key)
        if hit and self.now() - hit[0] < ttl:
            return hit[1]
        p = self._disk(key) if disk else None
        if p and p.exists():
            try:
                saved = json.loads(p.read_text())
                if self.now() - saved["at"] < ttl:
                    self._mem[key] = (saved["at"], saved["data"])
                    return saved["data"]
            except (ValueError, KeyError, OSError):
                pass
        return None

    def _store(self, key: str, data: Any, disk: bool):
        at = self.now()
        self._mem[key] = (at, data)
        p = self._disk(key) if disk else None
        if p:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"at": at, "data": data}))
            except OSError:
                pass

    async def _get(self, source: str, url: str, params: dict, headers: dict | None = None) -> Any:
        client = await self._http()
        for attempt in (1, 2):
            try:
                r = await client.get(url, params=params, headers=headers)
            except httpx.HTTPError as e:
                raise Unavailable(f"{source} could not be reached ({type(e).__name__}).") from None
            # The MBTA counts requests per minute and says when the count resets: a short wait beats failing.
            wait = float(r.headers.get("x-ratelimit-reset", 0)) - time.time()
            if r.status_code != 429 or attempt == 2 or not 0 < wait <= MAX_WAIT:
                break
            await asyncio.sleep(wait + 0.5)
        if r.status_code == 429:
            hint = "" if os.environ.get("MBTA_API_KEY") or source != "MBTA" else (
                " Without a key the MBTA allows 20 requests a minute; set MBTA_API_KEY (free at "
                "https://api-v3.mbta.com) for 1,000.")
            raise Unavailable(f"{source} rate limit reached; try again in a minute.{hint}")
        if r.status_code >= 400:
            raise Unavailable(f"{source} answered HTTP {r.status_code}.")
        try:
            return r.json()
        except ValueError:
            raise Unavailable(f"{source} sent a response that isn't JSON.") from None

    # ------------------------------------------------------------------ sources

    async def mbta(self, path: str, params: dict | None = None, ttl: float = 20, disk: bool = False) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = "mbta " + path + " " + json.dumps(params, sort_keys=True)
        hit = self._cached(key, ttl, disk)
        if hit is not None:
            return hit
        headers = {"x-api-key": os.environ["MBTA_API_KEY"]} if os.environ.get("MBTA_API_KEY") else None
        data = await self._get("MBTA", MBTA + path, params, headers)
        self._store(key, data, disk)
        return data

    async def gbfs(self, feed: str, disk: bool = False, ttl: float | None = None) -> dict:
        key = "gbfs " + feed
        hit = self._cached(key, ttl if ttl is not None else 60, disk)
        if hit is not None:
            return hit
        data = await self._get("Bluebikes", f"{GBFS}/{feed}.json", {})
        self._store(key, data, disk)
        return data

    async def geocode(self, query: str) -> list[dict]:
        key = "geo " + query.strip().lower()
        hit = self._cached(key, 30 * DAY, True)
        if hit is not None:
            return hit
        async with self._geo_lock:  # Nominatim's policy: at most one request a second
            wait = 1.0 - (time.monotonic() - self._geo_last)
            if wait > 0:
                await asyncio.sleep(wait)
            # Bounded to greater Boston, the area MBTA and Bluebikes serve.
            data = await self._get("OpenStreetMap", NOMINATIM, {
                "q": query, "format": "jsonv2", "limit": 3, "viewbox": "-71.40,42.60,-70.80,42.15", "bounded": 1,
            })
            self._geo_last = time.monotonic()
        self._store(key, data, True)
        return data
