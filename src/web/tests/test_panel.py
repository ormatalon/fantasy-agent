from storage import db
from ingestion.tests.support import TEST_LEAGUE, fixture, load_games


def pick_test_league(client, fake):
    fake.leagues = [l for l in fake.leagues if l["league_id"] == TEST_LEAGUE]
    client.put("/api/settings", json={"sleeper_username": "OrMatalon"})


def test_no_league_yet(client):
    r = client.get("/api/panel")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_league"


def test_roster_matches_sleeper(client, fake):
    pick_test_league(client, fake)
    panel = client.get("/api/panel").json()
    live = fixture("graphql_league_rosters.json")["data"]["league_rosters"][0]
    catalog = fixture("players_or_test_league.json")

    starters = [s for s in panel["slots"] if s["role"] == "starter"]
    assert [s["slot"] for s in starters] == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "IDP_FLEX", "IDP_FLEX", "K", "DEF"
    ]
    assert [s["player"]["player_id"] for s in starters] == live["starters"]
    for s in starters:
        ref = s["player"]
        c = catalog[ref["player_id"]]
        assert ref["name"] == (c.get("full_name") or f"{c['first_name']} {c['last_name']}")
        assert ref["position"] == catalog[ref["player_id"]]["position"]
        assert ref["fantasy_status"] == "mine_starter" and ref["lineup_slot"] == s["slot"]
    bench = [s for s in panel["slots"] if s["role"] == "bench"]
    assert [s["player"]["player_id"] for s in bench] == [p for p in live["players"] if p not in live["starters"]]
    assert all(s["slot"] == "BN" and s["player"]["fantasy_status"] == "mine_bench" for s in bench)


def test_matchup_record_and_points(client, fake):
    pick_test_league(client, fake)
    panel = client.get("/api/panel").json()
    week8 = fixture("matchups_week8.json")
    mine = next(m for m in week8 if m["roster_id"] == 1)
    opp = next(m for m in week8 if m["matchup_id"] == mine["matchup_id"] and m["roster_id"] != 1)
    assert panel["week"] == 11 and panel["fantasy_team"]["name"] == "OrMatalon"
    assert panel["fantasy_team"]["record"] == "4-0" and panel["fantasy_team"]["rank"] == 1
    assert panel["matchup"]["my_score"] == mine["points"]
    assert panel["matchup"]["opponent_score"] == opp["points"]
    assert panel["matchup"]["opponent_fantasy_team"] in {"OrHuji", f"Fantasy team {opp['roster_id']}"}



def test_playoff_bye_means_no_matchup(client, fake):
    pick_test_league(client, fake)
    fake.matchups = [{**m, "matchup_id": None} for m in fake.matchups]
    assert client.post("/api/refresh").json()["matchup"] is None


def test_a_move_in_the_sleeper_app_shows_on_the_next_request(client, fake):
    pick_test_league(client, fake)
    before = client.get("/api/panel").json()
    dropped = [s for s in before["slots"] if s["role"] == "bench"][-1]["player"]["player_id"]
    fake.rosters[0]["players"] = [p for p in fake.rosters[0]["players"] if p != dropped]
    after = client.post("/api/refresh").json()
    ids = [s["player"]["player_id"] for s in after["slots"] if s["player"]]
    assert dropped not in ids
    assert len(ids) == len([s for s in before["slots"] if s["player"]]) - 1


def test_polling_the_panel_does_not_hammer_sleeper(client, fake):
    pick_test_league(client, fake)
    client.get("/api/panel")
    fake.calls.clear()
    client.get("/api/panel")
    client.get("/api/panel")
    assert fake.calls == []


def test_failed_refresh_still_returns_stored_data(client, fake):
    pick_test_league(client, fake)
    client.get("/api/panel")
    fake.down = True
    r = client.post("/api/refresh")
    body = r.json()
    assert r.status_code == 200 and body["slots"]
    assert body["refresh"]["failed"][0]["code"] == "refresh_failed"
    assert body["refresh"]["stored_at"]


def test_nothing_stored_and_sleeper_down(client, fake):
    pick_test_league(client, fake)
    c = db.connect()
    for table in ("league", "fantasy_team", "roster_player", "matchup", "refresh_log"):
        c.execute(f"delete from {table}")
    fake.down = True
    r = client.get("/api/panel")
    assert r.status_code == 502 and r.json()["error"]["code"] == "sleeper_unreachable"


def set_games(status: str):
    """All week 11 games in one state; the NFL schedule is a real capture."""
    conn = db.connect()
    load_games(conn)
    conn.execute("update nfl_game set status = ? where week = 11", (status,))


def test_points_are_null_until_the_players_game_starts(client, fake):
    pick_test_league(client, fake)
    set_games("scheduled")
    panel = client.post("/api/refresh").json()
    played = [s for s in panel["slots"] if s["player"] and s["player"]["game"] and s["player"]["game"]["status"] != "bye"]
    assert played and all(s["points"] is None for s in panel["slots"])
    assert all(s["player"]["game"]["status"] == "not_started" and s["player"]["game"]["kickoff"] for s in played)
    assert panel["matchup"]["state"] == "not_started"


def test_points_and_final_state_after_the_games(client, fake):
    pick_test_league(client, fake)
    set_games("final")
    panel = client.post("/api/refresh").json()
    mine = next(m for m in fixture("matchups_week8.json") if m["roster_id"] == 1)
    for s in panel["slots"]:
        game = s["player"]["game"]
        if game and game["status"] == "complete":
            assert s["points"] == mine["players_points"].get(s["player"]["player_id"], s["points"])
    assert any(s["points"] is not None for s in panel["slots"])
    assert panel["matchup"]["state"] == "final"


def test_live_state_when_a_game_is_in_progress(client, fake):
    pick_test_league(client, fake)
    set_games("scheduled")
    conn = db.connect()
    conn.execute("update nfl_game set status = 'in_progress' where game_id = (select game_id from nfl_game where week = 11 and (home = 'BUF' or away = 'BUF'))")
    panel = client.post("/api/refresh").json()
    assert panel["matchup"]["state"] == "live"
    buf = next(s for s in panel["slots"] if s["player"] and s["player"]["nfl_team"] == "BUF")
    assert buf["player"]["game"]["status"] == "in_progress" and buf["points"] is not None


def test_bye_week_player_has_a_bye_game_and_no_points(client, fake):
    pick_test_league(client, fake)
    conn = db.connect()
    load_games(conn, espn=True)
    panel = client.post("/api/refresh").json()
    bye = [s for s in panel["slots"] if s["player"] and s["player"]["game"] and s["player"]["game"]["status"] == "bye"]
    assert bye and all(s["points"] is None and s["player"]["game"]["kickoff"] is None for s in bye)


def test_no_game_data_means_unknown_state_not_a_guess(client, fake):
    pick_test_league(client, fake)
    panel = client.post("/api/refresh").json()
    assert panel["matchup"]["state"] is None and all(s["points"] is None for s in panel["slots"])


def test_manual_refresh_forces_the_nfl_sources(client, fake, monkeypatch):
    pick_test_league(client, fake)
    seen = []
    monkeypatch.setattr("ingestion.nfl.service.refresh", lambda conn, **kw: seen.append(kw) or [])
    client.get("/api/panel")
    assert seen == []
    client.post("/api/refresh")
    assert seen == [{"force": True}]


def test_sources_report_coverage_and_failures(client, fake):
    pick_test_league(client, fake)
    conn = db.connect()
    conn.execute("insert into refresh_log(source, scope, last_attempt_at, last_ok_at, coverage) values ('nflverse', 'injury', 't', 't', '{\"mapped\": 99, \"total\": 100}')")
    body = client.get("/api/sources").json()
    assert body["coverage"]["nflverse:injury"] == {"mapped": 99, "total": 100} and body["this_week"] == 11
