"""Test helpers: real captured responses (fixtures/) and a fake Sleeper that serves them."""

import json
from pathlib import Path

import httpx

from ingestion import sleeper

FIXTURES = Path(__file__).parent / "fixtures" / "sleeper"
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
        # the state capture is from 2026-11-03 (week 9); the NFL captures are from 2026-11-17 (week 11)
        self.state = {**fixture("state_nfl.json"), "week": 11, "leg": 11, "display_week": 10}
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
                return httpx.Response(200, json=self.state)
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




# ---- NFL facts loaded from the captured fixtures, without any network

NFL_FIXTURES = Path(__file__).parent / "fixtures"


def csv_rows(name: str) -> list[dict]:
    import csv

    with open(NFL_FIXTURES / "nflverse" / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def espn_json(name: str):
    return json.loads((NFL_FIXTURES / "espn" / name).read_text(encoding="utf-8"))


def load_catalog(conn, monkeypatch):
    """Sleeper players catalog of SEA, ARI and DK Metcalf (real capture, 2026-11-17)."""
    from ingestion import sync

    catalog = json.loads((FIXTURES / "players_sea_ari_metcalf.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(sleeper, "get_players_catalog", lambda: catalog)
    assert sync.refresh_players(conn) is None


def load_games(conn, espn: bool = False):
    """nflverse schedule of weeks 10 and 11 (and ESPN's scoreboards on top) with this week = 11."""
    from ingestion.nfl import espn as espn_mod, service
    from ingestion.nfl.base import FetchResult
    from ingestion.nfl.crosswalk import Crosswalk
    from ingestion.nfl.nflverse import NflverseSource
    from storage.db import set_settings

    set_settings(conn, {"nfl_state": {"season": 2026, "week": 11}})
    service.seed_teams(conn)
    result = NflverseSource(Crosswalk(conn), {}).schedule_from(csv_rows("games_2026_weeks10_11.csv"), 2026, "2026-11-17T09:00:00Z")
    service._store(conn, "nflverse", "schedule", result, 2026, [])
    if espn:
        result = espn_mod.EspnSource(Crosswalk(conn)).schedule_from(
            [espn_json("scoreboard_week10.json"), espn_json("scoreboard_week11.json")], 2026)
        service._store(conn, "espn", "schedule", result, 2026, [])


def load_nfl_facts(conn, monkeypatch):
    """Catalog, crosswalk, rosters, depth, official and ESPN injuries, snaps, games: all from fixtures."""
    from ingestion.nfl import espn as espn_mod, service
    from ingestion.nfl.crosswalk import Crosswalk
    from ingestion.nfl.nflverse import NflverseSource

    load_catalog(conn, monkeypatch)
    load_games(conn, espn=True)
    xwalk = Crosswalk(conn)
    nv = NflverseSource(xwalk, {})
    as_of = "2026-11-17T07:01:21Z"
    roster = csv_rows("roster_weekly_2026_sea_ari_metcalf.csv")
    latest = {}
    for r in roster:
        if r["gsis_id"] and (r["gsis_id"] not in latest or int(r["week"]) >= int(latest[r["gsis_id"]]["week"])):
            latest[r["gsis_id"]] = r
    results = {
        ("nflverse", "roster"): nv.roster_from(list(latest.values()), as_of),
    }
    xwalk.save(conn)
    results[("nflverse", "depth")] = nv.depth_from(csv_rows("depth_charts_2026_sea_ari_latest.csv"), "2026-11-17T14:59:52Z")
    results[("nflverse", "injury")] = nv.injury_from(csv_rows("injuries_2026_sea_ari.csv"), 2026, as_of)
    results[("nflverse", "usage")] = nv.usage_from(csv_rows("snap_counts_2026_sea_ari.csv"), 2026, as_of)
    results[("espn", "injury")] = espn_mod.EspnSource(xwalk).injury_from(espn_json("injuries_sea_ari.json"), 2026, 11)
    for (source, kind), result in results.items():
        service._store(conn, source, kind, result, 2026, [])
    return results
