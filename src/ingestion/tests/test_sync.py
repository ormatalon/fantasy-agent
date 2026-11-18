import json
from datetime import timedelta

import pytest

from ingestion import sleeper, sync
from ingestion.sleeper import SleeperError
from ingestion.tests.support import MY_USER, TEST_LEAGUE, fixture


def test_discover_lists_leagues_as_league_info(fake):
    found = sync.discover("ormatalon")
    assert found["username"] == "OrMatalon" and found["season"] == 2026
    assert [l["name"] for l in found["leagues"]] == ["Or 8-Team League", "Or Test League", "FantaSteelers"]


def test_unknown_username(fake):
    with pytest.raises(sync.UsernameNotFound):
        sync.discover("nobody_here")


def test_refresh_stores_league_rosters_matchups_transactions(conn, fake):
    assert sync.refresh(conn, TEST_LEAGUE) == []
    assert conn.execute("select count(*) from fantasy_team").fetchone()[0] == 4
    me = conn.execute(
        "select * from fantasy_team where league_id = ? and owner_id = ?", (TEST_LEAGUE, MY_USER)
    ).fetchone()
    assert (me["wins"], me["losses"], me["name"]) == (4, 0, "OrMatalon")
    live = fixture("graphql_league_rosters.json")["data"]["league_rosters"][0]
    stored = conn.execute(
        "select player_id from roster_player where league_id = ? and roster_id = 1 and role = 'starter' "
        "order by slot_index",
        (TEST_LEAGUE,),
    ).fetchall()
    assert [r[0] for r in stored] == live["starters"]
    # the first sync backfills weeks 1 to 9
    assert conn.execute("select count(distinct week) from matchup").fetchone()[0] == 11
    assert conn.execute("select count(*) from transaction_log").fetchone()[0] > 0
    assert conn.execute("select count(*) from player").fetchone()[0] == 69
    fmt = json.loads(conn.execute("select format from league").fetchone()[0])
    assert fmt["defense"] == "idp"


def test_second_sync_fetches_only_recent_weeks(conn, fake):
    sync.refresh(conn, TEST_LEAGUE)
    fake.calls.clear()
    sync.refresh(conn, TEST_LEAGUE)
    assert sum("/matchups/" in c for c in fake.calls) == 2
    assert "/v1/players/nfl" not in fake.calls  # the catalog is fetched about daily


def test_a_move_in_sleeper_is_stored_with_a_diff(conn, fake):
    sync.refresh(conn, TEST_LEAGUE)
    assert conn.execute("select count(*) from roster_diff").fetchone()[0] == 0
    before = set(fake.rosters[0]["players"])
    dropped = fake.rosters[0]["players"][-1]
    fake.rosters[0]["players"] = [p for p in fake.rosters[0]["players"] if p != dropped] + ["2064"]
    sync.refresh(conn, TEST_LEAGUE)
    diff = conn.execute("select * from roster_diff").fetchone()
    assert (diff["roster_id"], json.loads(diff["added"]), json.loads(diff["dropped"])) == (1, ["2064"], [dropped])
    now = {r[0] for r in conn.execute("select player_id from roster_player where roster_id = 1")}
    assert now == (before - {dropped}) | {"2064"}


def test_failed_refresh_keeps_stored_rows_and_reports(conn, fake):
    sync.refresh(conn, TEST_LEAGUE)
    fake.down = True
    failed = sync.refresh(conn, TEST_LEAGUE)
    assert {f["source"] for f in failed} == {"sleeper"} and all(f["code"] == "refresh_failed" for f in failed)
    assert all(f["since"] for f in failed)
    assert conn.execute("select count(*) from roster_player").fetchone()[0] > 0
    assert sync.failures(conn)
    fake.down = False
    sync.refresh(conn, TEST_LEAGUE)
    assert sync.failures(conn) == []


def test_nothing_stored_and_unreachable_raises(conn, fake):
    fake.down = True
    with pytest.raises(SleeperError):
        sync.refresh(conn, TEST_LEAGUE)


def test_ensure_fresh_skips_a_recent_pull(conn, fake):
    sync.refresh(conn, TEST_LEAGUE)
    fake.calls.clear()
    sync.ensure_fresh(conn, TEST_LEAGUE, timedelta(seconds=30))
    assert fake.calls == []
    sync.ensure_fresh(conn, TEST_LEAGUE, timedelta(0))
    assert "/graphql" in fake.calls


def test_get_trending_returns_sleepers_list_as_is(fake):
    assert sleeper.get_trending("add", 24, 10) == fixture("trending_add_24h.json")
    assert sleeper.get_trending("drop") == fixture("trending_drop_24h.json")
    assert fake.calls == ["/v1/players/nfl/trending/add", "/v1/players/nfl/trending/drop"]


def test_failed_on_demand_nfl_team_roster_is_not_a_refresh_failure(conn):
    for source, scope in [("espn", "roster:SEA"), ("espn", "injury"), ("nflverse", "depth")]:
        sync._log(conn, source, scope, "429")
    assert sorted(f["source"] for f in sync.failures(conn)) == ["espn", "nflverse"]
