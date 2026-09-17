import json
import os
import sqlite3

import pytest

from src.agent.orchestrator import AgentError, build_context
from src.agent.tools import AgentContext, build_tools
from src.ingestion import storage


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    return conn


def seeded_conn() -> sqlite3.Connection:
    conn = make_conn()
    storage.save_league(
        conn,
        {"league_id": "L1", "season": "2026", "name": "Test League",
         "scoring_settings": {"rec": 1.0}, "roster_positions": ["QB", "FLEX", "BN"]},
    )
    storage.save_league_users(conn, "L1", [
        {"user_id": "u1", "display_name": "Me", "metadata": {"team_name": "My Team"}},
        {"user_id": "u2", "display_name": "Them", "metadata": {"team_name": "Their Team"}},
    ])
    storage.save_rosters(conn, "L1", [
        {"roster_id": 1, "owner_id": "u1", "players": ["p1"], "starters": ["p1"]},
        {"roster_id": 2, "owner_id": "u2", "players": ["p2"], "starters": ["p2"]},
    ])
    storage.save_matchups(conn, "L1", 2, [
        {"roster_id": 1, "matchup_id": 5, "points": 0, "starters": [], "players": []},
        {"roster_id": 2, "matchup_id": 5, "points": 0, "starters": [], "players": []},
    ])
    storage.save_players(conn, {
        "p1": {"first_name": "Alpha", "last_name": "One", "position": "QB", "team": "BUF"},
        "p2": {"first_name": "Beta", "last_name": "Two", "position": "RB", "team": "DAL"},
    })
    return conn


def make_ctx(conn: sqlite3.Connection) -> AgentContext:
    return AgentContext(conn=conn, league_id="L1", user_id="u1", season="2026", current_week=2)


def tools_by_name(conn: sqlite3.Connection) -> dict:
    return {t.name: t for t in build_tools(make_ctx(conn))}


def test_backtest_is_not_exposed_to_the_agent():
    # The evaluation harness is a dev-side regression guard; the agent has no
    # business invoking it mid-conversation.
    names = tools_by_name(seeded_conn())
    assert not any("backtest" in n for n in names)


def test_expected_tools_are_registered():
    names = set(tools_by_name(seeded_conn()))
    assert {
        "get_league_state",
        "get_my_roster",
        "get_team_roster",
        "get_recent_transactions",
        "get_projections",
        "get_season_projections",
        "recommend_lineup",
        "get_waiver_targets",
        "evaluate_proposed_trade",
        "get_player_news",
        "get_roster_news",
        "get_trending_players",
        "get_week_results",
        "sync_league",
    } <= names


def test_every_tool_has_a_description_the_model_can_route_on():
    for name, t in tools_by_name(seeded_conn()).items():
        assert t.description and len(t.description) > 20, f"{name} needs a usable description"


def test_league_state_tool_reports_week_and_opponent():
    tools = tools_by_name(seeded_conn())

    result = tools["get_league_state"].invoke({})

    assert "week 2" in result.lower()
    assert "Their Team" in result
    assert "My Team" in result


def test_team_roster_tool_matches_loosely_on_name():
    tools = tools_by_name(seeded_conn())

    result = tools["get_team_roster"].invoke({"team_name": "their"})

    assert "Beta Two" in result


def test_team_roster_tool_reports_miss_rather_than_guessing():
    tools = tools_by_name(seeded_conn())

    result = tools["get_team_roster"].invoke({"team_name": "nonexistent team"})

    assert "No team matching" in result


def test_build_context_requires_a_synced_league():
    with pytest.raises(AgentError, match="sync"):
        build_context(make_conn())


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_AGENT_TESTS"),
    reason="hits the live OpenRouter model; set RUN_LIVE_AGENT_TESTS=1 to run",
)
def test_live_agent_routes_a_lineup_question_to_the_lineup_tool():
    """Golden-prompt routing check. Opt-in because it needs network and a
    free-tier model that rate-limits; the offline tests above cover structure."""
    import config
    from langchain_core.messages import HumanMessage

    from src.agent.orchestrator import build_agent

    conn = storage.get_connection(config.DB_PATH)
    agent, _tools = build_agent(conn)
    result = agent.invoke(
        {"messages": [HumanMessage(content="Who should I start this week?")]},
        config={"configurable": {"thread_id": "test"}},
    )
    called = [
        tc["name"]
        for m in result["messages"]
        for tc in (getattr(m, "tool_calls", None) or [])
    ]
    assert "recommend_lineup" in called
