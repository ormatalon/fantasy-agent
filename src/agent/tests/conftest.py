"""Agent test fixtures: the Or Test League from captured Sleeper responses and a fake OpenRouter
that replays recorded responses (fixtures/openrouter). No live calls."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agent import chat, llm, tools
from ingestion import sync
from ingestion.nfl import service, sleeper_facts
from ingestion.tests.support import load_nfl_facts
from storage.db import set_settings
from web.models import DEFAULT_MODEL

HERE = Path(__file__).parent
OPENROUTER = HERE / "fixtures" / "openrouter"
CONTRACT = HERE / "fixtures" / "contract"
KEY = "sk-or-v1-test-SECRET-0123456789"
TEST_LEAGUE = "1412371724632166400"  # Or Test League (src/conftest.py)
WIRED = tools.PROVIDERS  # the providers as wired, before the autouse fixture unwires them
NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)  # captures are from 2026-11-17; week 11 kicks off 2026-11-20


class FrozenDatetime(datetime):
    """datetime whose now() is NOW, so a game's status does not change with the real date."""

    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.astimezone().replace(tzinfo=None)


def recorded(name: str) -> dict:
    return json.loads((OPENROUTER / name).read_text(encoding="utf-8"))


def contract(name: str) -> dict:
    return json.loads((CONTRACT / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Tracing never on in tests; retries without waiting; no question left running; no provider wired."""
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm, "BACKOFF", (0, 0))
    monkeypatch.setattr(chat, "_running", None)
    monkeypatch.setattr(tools, "PROVIDERS", dict.fromkeys(tools.PROVIDERS))
    monkeypatch.setattr(sleeper_facts, "refresh_news", lambda conn, player_id: True)  # no Sleeper news call
    monkeypatch.setattr(service, "team_roster_refresh", lambda *a, **k: None)  # no ESPN call
    monkeypatch.setattr(service, "refresh", lambda conn, **kw: [])  # no NFL source pulled before a question
    monkeypatch.setattr(service, "datetime", FrozenDatetime)  # game status: kickoff passed or not


@pytest.fixture
def league(conn, fake):
    """Or Test League stored, OrMatalon's settings and a key saved."""
    found = sync.discover("OrMatalon")
    set_settings(conn, {
        "sleeper_username": found["username"], "sleeper_user_id": found["user_id"], "leagues": found["leagues"],
        "current_league_id": TEST_LEAGUE, "openrouter_api_key": KEY, "model": DEFAULT_MODEL,
    })
    sync.refresh(conn, TEST_LEAGUE)
    return conn


@pytest.fixture
def ctx(league):
    return tools.Ctx(league, TEST_LEAGUE)


@pytest.fixture
def nfl_ctx(ctx, monkeypatch):
    """The league plus NFL facts captured 2026-11-17 (SEA, ARI, DK Metcalf; this week 11).

    The SEA/ARI catalog replaces the league's, so only players in it have names."""
    ctx.conn.execute("delete from refresh_log where scope = 'catalog'")  # else kept as fresh
    load_nfl_facts(ctx.conn, monkeypatch)
    return ctx


class FakeOpenRouter:
    """Serves queued chat completion responses (status, body) and the /key counter."""

    def __init__(self):
        self.completions: list[tuple[int, dict]] = []
        self.key_info = recorded("key_info_free_tier.json")
        self.requests: list[httpx.Request] = []

    def reply(self, *names_or_bodies, status: int = 200):
        for x in names_or_bodies:
            self.completions.append((status, recorded(x) if isinstance(x, str) else x))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json=self.key_info)
        status, body = self.completions.pop(0)
        return httpx.Response(status, json=body)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/chat/completions")]


@pytest.fixture
def openrouter(monkeypatch):
    f = FakeOpenRouter()
    monkeypatch.setattr(llm, "http", httpx.Client(transport=httpx.MockTransport(f.handler)))
    return f
