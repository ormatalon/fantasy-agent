import json

import pytest

from ingestion import leagues, projection_pulls, refresh, sleeper, sync
from ingestion.nfl import service
from ingestion.tests.support import TEST_LEAGUE, fixture
from storage.db import get_setting


def known():
    return [{"league_id": l["league_id"], "name": l["name"]} for l in fixture("user_leagues_2026.json")]


def test_resolve_league_by_id_name_and_prefix():
    ls = known()
    assert leagues.resolve_league(ls, league_id=TEST_LEAGUE)["name"] == "Or Test League"
    assert leagues.resolve_league(ls, league_name="fantasteelers")["name"] == "FantaSteelers"
    assert leagues.resolve_league(ls, league_name="or test")["name"] == "Or Test League"


def test_unknown_or_ambiguous_league_lists_the_real_names():
    with pytest.raises(leagues.LeagueNotFound) as e:
        leagues.resolve_league(known(), league_name="Or")  # a prefix of two leagues is not unique
    assert e.value.available == ["Or 8-Team League", "Or Test League", "FantaSteelers"]


def test_select_league_loads_then_stores_the_choice(conn, fake):
    leagues.select_league(conn, TEST_LEAGUE)
    assert get_setting(conn, "current_league_id") == TEST_LEAGUE
    fake.down = True
    with pytest.raises(sleeper.SleeperError):
        leagues.select_league(conn, "1382038888268185600")
    assert get_setting(conn, "current_league_id") == TEST_LEAGUE  # a failed switch keeps the old league


def test_before_question_refreshes_sleeper_then_the_due_nfl_sources(conn, fake, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "refresh", lambda conn, **kw: calls.append(kw))
    refresh.before_question(conn, TEST_LEAGUE)
    assert calls == [{}]  # the due-check, not forced
    assert conn.execute("select count(*) from roster_player").fetchone()[0] > 0


def test_transaction_settings_are_stored(conn, fake):
    sync.refresh(conn, TEST_LEAGUE)
    stored = conn.execute("select type, settings from transaction_log").fetchall()
    assert stored and all(r["settings"] is not None for r in stored)
    waivers = [json.loads(r["settings"]) for r in stored if r["type"] == "waiver"]
    assert all("seq" in w for w in waivers)  # a rolling league: a claim order, no bid


def test_projection_pulls_are_kept_and_unchanged_ones_are_not_stored_again(conn, monkeypatch):
    rows = fixture("projections_2026_week9_sample.json")
    monkeypatch.setattr(sleeper, "get_projections", lambda season, week=None: rows)
    assert projection_pulls.pull(conn, 2026, [9]) == len(rows)
    stored = conn.execute("select * from projection_snapshot where week = 9").fetchone()
    assert json.loads(stored["stats"]) and stored["source_modified"].endswith("Z")
    assert projection_pulls.pull(conn, 2026, [9]) == 0  # nothing changed
    rows[0]["stats"]["pts_ppr"] += 1.5
    monkeypatch.setattr(projection_pulls, "now_iso", lambda: "2026-11-18T00:00:00Z")  # a later pull (second resolution)
    assert projection_pulls.pull(conn, 2026, [9]) == 1  # the changed player gets a second snapshot
    count = conn.execute("select count(*) from projection_snapshot where player_id = ?", (rows[0]["player_id"],))
    assert count.fetchone()[0] == 2
    projection_pulls.pull(conn, 2026, [], include_season=True)
    assert conn.execute("select count(*) from projection_snapshot where week = 0").fetchone()[0] > 0
