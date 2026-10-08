import pytest

from src.ingestion import storage
from src.ingestion.nfl_teams import resolve_team
from tests.test_agent import ctx_with_cached_projections, seeded_conn
from src.agent.tools import build_tools


@pytest.mark.parametrize("query", ["SEA", "sea", "Seattle", "seahawks", "Seattle Seahawks", "the Seahawks"])
def test_team_resolves_from_abbreviation_city_or_nickname(query):
    assert resolve_team(query) == ["SEA"]


def test_shared_city_is_ambiguous_and_unknown_is_empty():
    assert sorted(resolve_team("New York")) == ["NYG", "NYJ"]
    assert sorted(resolve_team("los angeles")) == ["LAC", "LAR"]
    assert resolve_team("Rams") == ["LAR"]
    assert resolve_team("OAK") == ["LV"]
    assert resolve_team("Their Team") == []


def nfl_conn(roster_positions=("QB", "WR", "FLEX", "BN")):
    conn = seeded_conn()
    storage.save_league(conn, {"league_id": "L1", "season": "2026", "name": "Test League",
                               "scoring_settings": {"rec": 1.0}, "roster_positions": list(roster_positions)})
    storage.save_players(conn, {
        "dk": {"first_name": "DK", "last_name": "Metcalf", "position": "WR", "team": "PIT", "depth_chart_order": 1},
        "jsn": {"first_name": "Jaxon", "last_name": "Smith-Njigba", "position": "WR", "team": "SEA", "depth_chart_order": 1},
        "kupp": {"first_name": "Cooper", "last_name": "Kupp", "position": "WR", "team": "SEA", "depth_chart_order": 3},
        "new": {"first_name": "Practice", "last_name": "Guy", "position": "WR", "team": "SEA"},
        "qb": {"first_name": "Sam", "last_name": "Darnold", "position": "QB", "team": "SEA", "depth_chart_order": 1,
               "injury_status": "Questionable"},
        "lb": {"first_name": "Line", "last_name": "Backer", "position": "LB", "team": "SEA", "depth_chart_order": 1},
        "ot": {"first_name": "Big", "last_name": "Tackle", "position": "OT", "team": "SEA", "depth_chart_order": 1},
    })
    storage.save_rosters(conn, "L1", [
        {"roster_id": 1, "owner_id": "u1", "players": ["p1", "kupp", "qb"], "starters": ["qb"]},
        {"roster_id": 2, "owner_id": "u2", "players": ["p2", "jsn"], "starters": ["p2"]},
    ])
    return conn


def nfl_tool(conn, name="get_nfl_team_roster"):
    return {t.name: t for t in build_tools(ctx_with_cached_projections(conn))}[name]


def test_lists_only_the_current_team_in_depth_chart_order_with_ownership():
    result = nfl_tool(nfl_conn()).invoke({"team": "Seattle"})

    assert "Seattle Seahawks (SEA)" in result
    assert "Metcalf" not in result
    wrs = result.split("WR:")[1]
    assert wrs.index("Smith-Njigba") < wrs.index("Kupp") < wrs.index("Practice Guy")
    assert "Smith-Njigba (WR/SEA) - Their Team" in result
    assert "Kupp (WR/SEA) - MY ROSTER (bench)" in result
    assert "Darnold (QB/SEA) [Questionable] - MY ROSTER (starter)" in result
    assert "Practice Guy (WR/SEA) - free agent" in result
    assert "Tackle" not in result


def test_idp_players_appear_only_in_idp_leagues():
    assert "Backer" not in nfl_tool(nfl_conn()).invoke({"team": "SEA"})
    assert "LB:" in nfl_tool(nfl_conn(("QB", "WR", "LB", "BN"))).invoke({"team": "SEA"})


def test_ambiguous_and_unknown_teams_are_reported_not_guessed():
    tool = nfl_tool(nfl_conn())
    assert "ambiguous" in tool.invoke({"team": "New York"})
    assert "No NFL team matching" in tool.invoke({"team": "Narnia"})


def test_fantasy_team_lookup_points_nfl_team_names_to_the_nfl_tool():
    result = nfl_tool(nfl_conn(), "get_team_roster").invoke({"team_name": "Seattle"})
    assert "get_nfl_team_roster" in result


def test_ownership_summary_lists_my_players_with_slot_first():
    result = nfl_tool(nfl_conn()).invoke({"team": "SEA"})

    assert ("Rostered in my league: MY ROSTER: Sam Darnold (starter), Cooper Kupp (bench); "
            "Their Team: Jaxon Smith-Njigba. Everyone else is a free agent.") in result
