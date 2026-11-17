from storage import db
from conftest import TEST_LEAGUE, fixture


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
    assert panel["week"] == 9 and panel["fantasy_team"]["name"] == "OrMatalon"
    assert panel["fantasy_team"]["record"] == "4-0" and panel["fantasy_team"]["rank"] == 1
    assert panel["matchup"]["my_score"] == mine["points"]
    assert panel["matchup"]["opponent_score"] == opp["points"]
    assert panel["matchup"]["opponent_fantasy_team"] in {"OrHuji", f"Fantasy team {opp['roster_id']}"}
    first = panel["slots"][0]
    assert first["points"] == mine["players_points"][first["player"]["player_id"]]


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
