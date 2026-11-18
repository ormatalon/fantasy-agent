"""Decision test fixtures: Or Test League from captured Sleeper responses, with the real week 10 and 11 NFL
schedule and projections seeded over its real rosters (the captured Sleeper projections cover four players)."""

from datetime import UTC, datetime

import pytest

from decisions import artifact_view
from decisions.tests.support import LEAGUE, seed_projections
from ingestion import sync
from ingestion.nfl import service, sleeper_facts
from ingestion.tests.support import load_games
from storage.db import set_settings


NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)  # the Tuesday before week 11, whose kickoffs are 2026-11-20 to 24


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(service, "datetime", FrozenDatetime)  # game_ref and injury_fact read the clock: games are not started yet
    monkeypatch.setattr(sleeper_facts, "refresh_news", lambda conn, player_id: True)  # no Sleeper news call
    monkeypatch.setattr(artifact_view, "ensure", lambda conn, league_id, now=None: [])  # the tests seed the artifact


@pytest.fixture
def league(conn, fake):
    """Or Test League stored (this week 11), OrMatalon is fantasy team 1, real week 10-11 NFL schedule,
    every player projected 5.0 a week for weeks 11-18; tests call seed_projections again for what they change."""
    found = sync.discover("OrMatalon")
    set_settings(conn, {"sleeper_username": found["username"], "sleeper_user_id": found["user_id"],
                        "leagues": found["leagues"], "current_league_id": LEAGUE})  # fmt: skip
    sync.refresh(conn, LEAGUE)
    load_games(conn)
    seed_projections(conn, LEAGUE)
    return conn
