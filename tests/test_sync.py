import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import config
from src.ingestion import storage, sync


def make_conn(synced_hours_ago: float | None, active_league: str | None = "L1") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    if active_league:
        storage.set_state(conn, "active_league_id", active_league)
    if synced_hours_ago is not None:
        ts = datetime.now(timezone.utc) - timedelta(hours=synced_hours_ago)
        storage.set_state(conn, "last_synced_at", ts.isoformat())
    return conn


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    monkeypatch.setattr(config, "SLEEPER_USERNAME", "someone")
    monkeypatch.setattr(sync, "run_sync", lambda conn, **kw: recorded.append(kw))
    return recorded


def test_fresh_data_is_not_resynced(calls):
    assert sync.sync_if_stale(make_conn(synced_hours_ago=1), max_age_hours=2, log=lambda _m: None) is False
    assert calls == []


def test_stale_data_resyncs_the_active_league(calls):
    assert sync.sync_if_stale(make_conn(synced_hours_ago=3), max_age_hours=2, log=lambda _m: None) is True
    assert calls[0]["league_query"] == "L1"


def test_db_synced_before_timestamps_existed_counts_as_stale(calls):
    assert sync.sync_if_stale(make_conn(synced_hours_ago=None), max_age_hours=2, log=lambda _m: None) is True


def test_never_synced_db_is_left_for_an_explicit_sync(calls):
    # Picking a league may need the user, so auto-sync never makes that choice.
    assert sync.sync_if_stale(make_conn(None, active_league=None), log=lambda _m: None) is False
    assert calls == []


def test_a_failed_auto_sync_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(config, "SLEEPER_USERNAME", "someone")

    def boom(conn, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(sync, "run_sync", boom)
    messages = []
    assert sync.sync_if_stale(make_conn(synced_hours_ago=5), max_age_hours=2, log=messages.append) is False
    assert any("network down" in m for m in messages)


def test_league_selection():
    leagues = [{"league_id": "1", "name": "Work League"}, {"league_id": "2", "name": "Family League"}]
    never = lambda _l: pytest.fail("should not prompt")  # noqa: E731
    assert sync._select_leagues(leagues, "family", False, never) == [leagues[1]]
    assert sync._select_leagues(leagues, "2", False, never) == [leagues[1]]
    assert sync._select_leagues(leagues, None, True, never) == leagues
    with pytest.raises(sync.SleeperError, match="Work League"):
        sync._select_leagues(leagues, "league", False, never)


def test_a_named_league_that_does_not_exist_fails_even_with_one_league():
    only = [{"league_id": "1", "name": "FantaSteelers"}]
    never = lambda _l: pytest.fail("should not prompt")  # noqa: E731
    assert sync._select_leagues(only, None, False, never) == only
    with pytest.raises(sync.SleeperError, match="FantaSteelers"):
        sync._select_leagues(only, "Work League", False, never)
