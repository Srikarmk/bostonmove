"""Record real API responses once, then replay them in tests with the clock frozen at recording time.

    uv run python tests/record_fixtures.py     # re-record (needs network; about two minutes keyless)

A request the fixtures don't have fails loudly with the URL, so a test can never reach the network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


def key(request: httpx.Request) -> str:
    u = urlsplit(str(request.url))
    q = "&".join(f"{k}={v}" for k, v in sorted(parse_qsl(u.query)))
    return f"{u.netloc}{u.path}?{q}"


def path_for(k: str) -> Path:
    return FIXTURES / (hashlib.sha256(k.encode()).hexdigest()[:16] + ".json")


def frozen_now() -> float:
    return json.loads((FIXTURES / "now.json").read_text())["now"]


def replay_client(override: dict | None = None) -> httpx.AsyncClient:
    """A client that answers from fixtures. `override` maps a URL fragment to a status code, or to
    (status, body), to return instead: {"/routes": 429} simulates the MBTA rate limit."""

    def handler(request: httpx.Request) -> httpx.Response:
        for frag, answer in (override or {}).items():
            if frag in str(request.url):
                status, body = answer if isinstance(answer, tuple) else (answer, {"errors": [{"status": str(answer)}]})
                return httpx.Response(status, json=body)
        p = path_for(key(request))
        if not p.exists():
            raise AssertionError(f"no fixture for {key(request)}; re-run tests/record_fixtures.py")
        saved = json.loads(p.read_text())
        return httpx.Response(saved["status"], json=saved["body"])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio

        for attempt in range(6):
            r = await self.inner.handle_async_request(request)
            await r.aread()
            if r.status_code != 429:
                break
            await asyncio.sleep(15)  # keyless MBTA limit is 20 a minute
        FIXTURES.mkdir(exist_ok=True)
        k = key(request)
        path_for(k).write_text(json.dumps({"key": k, "status": r.status_code, "body": r.json()}))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})
