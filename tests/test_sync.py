import json
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


# --- roster refresh before each question ---

class FakeClient:
    """Stands in for SleeperClient; counts requests."""

    def __init__(self, rosters, fail=False):
        self.rosters, self.fail, self.requests = rosters, fail, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _record(self, name):
        if self.fail:
            raise RuntimeError("sleeper down")
        self.requests.append(name)

    def get_rosters(self, league_id):
        self._record("rosters")
        return self.rosters

    def get_matchups(self, league_id, week):
        self._record("matchups")
        return [{"roster_id": 1, "matchup_id": 1, "points": 0, "starters": [], "players": []}]

    def get_transactions(self, league_id, week):
        self._record("transactions")
        return []


def roster_conn(players=("p1", "p2")) -> sqlite3.Connection:
    conn = make_conn(synced_hours_ago=1)
    storage.set_state(conn, "current_week", "5")
    storage.set_state(conn, "active_user_id", "u1")
    storage.save_players(conn, {
        "p1": {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"},
        "p2": {"first_name": "Drake", "last_name": "Maye", "position": "QB", "team": "NE"},
        "p3": {"first_name": "Chuba", "last_name": "Hubbard", "position": "RB", "team": "CAR"},
    })
    storage.save_rosters(conn, "L1", [{"roster_id": 1, "owner_id": "u1", "players": list(players),
                                       "starters": list(players), "reserve": []}])
    return conn


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "SLEEPER_USERNAME", "someone")
    fake = FakeClient([{"roster_id": 1, "owner_id": "u1", "players": ["p1", "p3"],
                        "starters": ["p1", "p3"], "reserve": []}])
    monkeypatch.setattr(sync, "SleeperClient", lambda: fake)
    return fake


def my_players(conn) -> list:
    return json.loads(storage.get_roster(conn, "L1", 1)["players"])


def test_roster_refresh_saves_rosters_with_three_requests_and_leaves_the_full_sync_clock(client):
    conn = roster_conn()
    last_full = storage.get_state(conn, "last_synced_at")

    assert sync.refresh_rosters(conn, max_age_seconds=0, log=lambda _m: None) is True

    assert my_players(conn) == ["p1", "p3"]
    assert sorted(client.requests) == ["matchups", "rosters", "transactions"]
    assert storage.get_state(conn, "rosters_synced_at")
    assert storage.get_state(conn, "last_synced_at") == last_full


def test_roster_refresh_is_skipped_within_the_interval(client):
    conn = roster_conn()
    storage.set_state(conn, "rosters_synced_at", storage.now_iso())

    assert sync.refresh_rosters(conn, max_age_seconds=60, log=lambda _m: None) is False
    assert client.requests == []

    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    storage.set_state(conn, "rosters_synced_at", old.isoformat())
    assert sync.refresh_rosters(conn, max_age_seconds=60, log=lambda _m: None) is True


def test_default_interval_of_zero_refreshes_every_time(client):
    conn = roster_conn()
    storage.set_state(conn, "rosters_synced_at", storage.now_iso())
    assert config.ROSTER_REFRESH_SECONDS == 0
    assert sync.refresh_rosters(conn, log=lambda _m: None) is True


def test_failed_roster_refresh_is_logged_and_keeps_the_stored_roster(client):
    client.fail = True
    conn = roster_conn()
    messages = []

    assert sync.refresh_rosters(conn, max_age_seconds=0, log=messages.append) is False

    assert any("sleeper down" in m for m in messages)
    assert my_players(conn) == ["p1", "p2"]


def test_roster_change_is_recorded_with_names(client):
    conn = roster_conn()

    sync.refresh_rosters(conn, max_age_seconds=0, log=lambda _m: None)

    assert storage.get_state(conn, "roster_changed_at:L1")
    change = storage.get_state(conn, "roster_change:L1")
    assert "added Chuba Hubbard" in change
    assert "dropped Drake Maye" in change


def test_unchanged_roster_and_first_save_are_not_changes(client):
    conn = roster_conn(players=("p1", "p3"))
    sync.refresh_rosters(conn, max_age_seconds=0, log=lambda _m: None)
    assert storage.get_state(conn, "roster_changed_at:L1") is None

    fresh = make_conn(synced_hours_ago=1)
    sync.save_rosters_tracking_changes(fresh, "L1", client.rosters, "u1")
    assert storage.get_state(fresh, "roster_changed_at:L1") is None


def test_a_lineup_swap_is_a_change():
    conn = roster_conn()
    sync.save_rosters_tracking_changes(
        conn, "L1", [{"roster_id": 1, "owner_id": "u1", "players": ["p1", "p2"], "starters": ["p2", "p1"]}], "u1"
    )
    assert storage.get_state(conn, "roster_change:L1") == "lineup changed"


def test_before_a_question_a_stale_db_gets_a_full_sync_instead(calls, client):
    conn = roster_conn()
    storage.set_state(conn, "last_synced_at", (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())

    assert sync.refresh_before_question(conn, log=lambda _m: None) is True
    assert len(calls) == 1
    assert client.requests == []


def test_before_a_question_fresh_data_gets_a_roster_refresh(calls, client):
    conn = roster_conn()

    assert sync.refresh_before_question(conn, log=lambda _m: None) is False
    assert calls == []
    assert "rosters" in client.requests


def test_no_roster_refresh_after_a_failed_full_sync(monkeypatch, client):
    monkeypatch.setattr(sync, "run_sync", lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    conn = roster_conn()
    storage.set_state(conn, "last_synced_at", (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())

    assert sync.refresh_before_question(conn, log=lambda _m: None) is False
    assert client.requests == []
