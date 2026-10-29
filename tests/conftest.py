import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakeweb import frozen_now, replay_client

from bostonmove.web import Web


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MBTA_API_KEY", raising=False)
    monkeypatch.delenv("BOSTONMOVE_NO_CACHE", raising=False)
    monkeypatch.setenv("BOSTONMOVE_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def web():
    w = Web(replay_client(), now=frozen_now)
    yield w
    await w._client.aclose()


@pytest.fixture
def web_with():
    """A replaying Web where some paths answer with an error status instead, e.g. {"/routes": 429}."""
    made = []

    def make(override):
        w = Web(replay_client(override), now=frozen_now)
        made.append(w)
        return w

    yield make
