"""Fake Sleeper served from real captured responses (tests/fixtures/sleeper)."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ingestion import sleeper
from web.main import app

FIXTURES = Path(__file__).parent / "ingestion" / "tests" / "fixtures" / "sleeper"
TEST_LEAGUE = "1412371724632166400"
MY_USER = "1268547099817943040"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeSleeper:
    """Routes Sleeper URLs to fixtures. Tests change the attributes to simulate Sleeper changing."""

    def __init__(self):
        self.leagues = fixture("user_leagues_2026.json")
        self.rosters = fixture("graphql_league_rosters.json")["data"]["league_rosters"]
        self.matchups = fixture("matchups_week8.json")  # the week 8 capture serves every week
        self.down = False
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        path = request.url.path
        if path == "/graphql":
            return httpx.Response(200, json={"data": {"league_rosters": self.rosters}})
        parts = path.removeprefix("/v1/").split("/")
        match parts:
            case ["state", "nfl"]:
                return httpx.Response(200, json=fixture("state_nfl.json"))
            case ["user", name] if name.lower() == "ormatalon":
                return httpx.Response(200, json=fixture("user_OrMatalon.json"))
            case ["user", _]:
                return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
            case ["user", _, "leagues", "nfl", _]:
                return httpx.Response(200, json=self.leagues)
            case ["league", league_id]:
                return httpx.Response(200, json=next(l for l in self.leagues if l["league_id"] == league_id))
            case ["league", _, "users"]:
                return httpx.Response(200, json=fixture("league_users.json"))
            case ["league", _, "matchups", _]:
                return httpx.Response(200, json=self.matchups)
            case ["league", _, "transactions", _]:
                return httpx.Response(200, json=fixture("transactions_week8.json"))
            case ["players", "nfl"]:
                return httpx.Response(200, json=fixture("players_or_test_league.json"))
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    f = FakeSleeper()
    monkeypatch.setattr(sleeper, "client", httpx.Client(transport=httpx.MockTransport(f.handler)))
    return f


@pytest.fixture
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("FANTASY_DATA_DIR", str(tmp_path))
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.fixture
def conn(tmp_path, monkeypatch, fake):
    from storage import db

    monkeypatch.setenv("FANTASY_DATA_DIR", str(tmp_path))
    c = db.connect()
    db.migrate(c)
    yield c
    c.close()
