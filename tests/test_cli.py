import json

import pytest

from bostonmove import cli
from bostonmove.web import Web


@pytest.fixture
def replay(monkeypatch):
    from fakeweb import frozen_now, replay_client

    monkeypatch.setattr(cli, "Web", lambda: Web(replay_client(), now=frozen_now))


def test_next_text(replay, capsys):
    assert cli.main(["next", "Park Street"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Park Street") and "Red Line" in out


def test_plan_json(replay, capsys):
    assert cli.main(["plan", "South Station", "Kendall", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["options"][0]["total_minutes"] <= data["options"][-1]["total_minutes"]


def test_bikes_text(replay, capsys):
    assert cli.main(["bikes", "Kendall", "--need", "dock"]) == 0
    assert "docks" in capsys.readouterr().out


def test_usage_error_exits_2(replay, capsys):
    assert cli.main(["alerts", "--route", "Purple Line"]) == 2
    assert "No MBTA route" in capsys.readouterr().err
