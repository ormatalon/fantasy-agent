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
        "get_fantasy_team_roster",
        "get_nfl_team_roster",
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
        "switch_league",
        "get_injured_stash_candidates",
    } <= names


def ctx_with_cached_projections(conn, projections=()) -> AgentContext:
    ctx = make_ctx(conn)
    ctx._cache[("week", 2)] = list(projections)
    return ctx


def test_roster_shows_injury_designation_and_ir_slot_hint():
    conn = seeded_conn()
    storage.save_players(conn, {"p1": {"first_name": "Alpha", "last_name": "One", "position": "QB",
                                       "team": "BUF", "injury_status": "IR"}})
    tools = {t.name: t for t in build_tools(ctx_with_cached_projections(conn))}

    result = tools["get_my_roster"].invoke({})

    assert "[IR]" in result
    assert "IR slot" in result


def test_week_results_default_to_last_completed_week_with_opponent():
    conn = seeded_conn()
    storage.save_matchups(conn, "L1", 1, [
        {"roster_id": 1, "matchup_id": 3, "points": 101.2, "starters": ["p1"], "players": ["p1"],
         "players_points": {"p1": 101.2}},
        {"roster_id": 2, "matchup_id": 3, "points": 88.0, "starters": ["p2"], "players": ["p2"],
         "players_points": {"p2": 88.0}},
    ])
    tools = tools_by_name(conn)  # current week is 2 -> last completed is 1

    result = tools["get_week_results"].invoke({})

    assert "Week 1" in result
    assert "WIN 101.2 - 88.0 vs. Their Team" in result


def test_ambiguous_player_name_returns_candidates_instead_of_guessing():
    conn = seeded_conn()
    storage.save_players(conn, {"p3": {"first_name": "Alpha", "last_name": "Onesie", "position": "WR", "team": "KC"}})
    tools = tools_by_name(conn)

    result = tools["get_player_news"].invoke({"player_name": "alpha"})

    assert "ambiguous" in result
    assert "Alpha One (QB/BUF)" in result
    assert "Alpha Onesie (WR/KC)" in result


def test_graph_rebuilds_the_system_prompt_on_every_model_turn():
    from langchain_core.messages import AIMessage, HumanMessage

    from src.agent.graph import build_graph

    week = {"n": 3}
    seen = []

    def fake_llm(messages):
        seen.append(messages[0].content)
        return AIMessage(content="ok")

    graph = build_graph(fake_llm, [], lambda: f"week {week['n']}")
    thread = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [HumanMessage(content="hi")]}, config=thread)
    week["n"] = 4  # e.g. an auto-sync rolled the week over mid-chat
    graph.invoke({"messages": [HumanMessage(content="again")]}, config=thread)

    assert seen == ["week 3", "week 4"]


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

    result = tools["get_fantasy_team_roster"].invoke({"fantasy_team": "their"})

    assert "Beta Two" in result


def test_team_roster_tool_reports_miss_rather_than_guessing():
    tools = tools_by_name(seeded_conn())

    result = tools["get_fantasy_team_roster"].invoke({"fantasy_team": "nonexistent team"})

    assert "No fantasy team matching" in result


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


def superflex_conn(reserve=("hurt",)) -> sqlite3.Connection:
    conn = make_conn()
    storage.save_league(conn, {"league_id": "L1", "season": "2026", "name": "Test League",
                               "scoring_settings": {"rec": 1.0},
                               "roster_positions": ["QB", "SUPER_FLEX", "BN", "IR"]})
    storage.save_league_users(conn, "L1", [{"user_id": "u1", "display_name": "Me", "metadata": {}}])
    storage.save_players(conn, {
        "allen": {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"},
        "maye": {"first_name": "Drake", "last_name": "Maye", "position": "QB", "team": "NE"},
        "hurt": {"first_name": "Star", "last_name": "Hurt", "position": "QB", "team": "KC"},
    })
    storage.save_rosters(conn, "L1", [{"roster_id": 1, "owner_id": "u1", "players": ["maye", "allen", "hurt"],
                                       "starters": ["allen", "maye"], "reserve": list(reserve)}])
    return conn


def proj(pid: str, name: str, mean: float):
    from src.projections.engine import AdjustedProjection

    return AdjustedProjection(player_id=pid, name=name, position="QB", team="X", mean=mean, variance=1.0,
                              matchup_multiplier=1.0, injury_multiplier=1.0, num_sources=1)


def test_my_roster_shows_the_lineup_set_in_sleeper_by_slot():
    conn = superflex_conn()
    tools = {t.name: t for t in build_tools(ctx_with_cached_projections(conn, [proj("allen", "Josh Allen", 24.0)]))}

    result = tools["get_my_roster"].invoke({})

    assert "QB: Josh Allen (QB/X): 24.0 pts" in result
    assert "SUPER_FLEX: Drake Maye (QB/NE)" in result
    assert "Bench: (none)" in result
    assert result.index("IR:") < result.index("Star Hurt")


def test_recommended_lineup_never_starts_a_player_in_an_ir_slot():
    conn = superflex_conn()
    projections = [proj("allen", "Josh Allen", 20.0), proj("maye", "Drake Maye", 15.0), proj("hurt", "Star Hurt", 30.0)]
    tools = {t.name: t for t in build_tools(ctx_with_cached_projections(conn, projections))}

    result = tools["recommend_lineup"].invoke({})

    assert "Star Hurt" not in result.split("Total projected")[0]
    # Healthy again but still in the IR slot: say so.
    assert "move him out of IR" in result


def test_system_prompt_carries_my_roster_and_follows_the_stored_roster():
    from src.agent.orchestrator import system_prompt_for

    conn = superflex_conn(reserve=())
    ctx = make_ctx(conn)
    storage.set_state(conn, "rosters_synced_at", "2026-10-06T12:00:00+00:00")

    text = system_prompt_for(ctx)
    assert "MY ROSTER right now" in text
    assert "QB: Josh Allen (QB/BUF)" in text

    storage.save_rosters(conn, "L1", [{"roster_id": 1, "owner_id": "u1", "players": ["maye", "allen"],
                                       "starters": ["maye", "allen"], "reserve": []}])
    storage.set_state(conn, "roster_changed_at:L1", "2026-10-06T12:01:00+00:00")
    storage.set_state(conn, "roster_change:L1", "lineup changed")

    text = system_prompt_for(ctx)
    assert "QB: Drake Maye (QB/NE)" in text
    assert "Last change detected 2026-10-06 12:01 UTC: lineup changed." in text


def _history(tool_name="get_my_roster"):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    return [
        HumanMessage(content="who's on my team?", additional_kwargs={"asked_at": "2026-10-06T12:00:00+00:00"}),
        AIMessage(content="", tool_calls=[{"name": tool_name, "args": {}, "id": "c1"}]),
        ToolMessage(content="old roster", name=tool_name, tool_call_id="c1"),
        AIMessage(content="You have Charbonnet."),
        HumanMessage(content="and now?", additional_kwargs={"asked_at": "2026-10-06T12:10:00+00:00"}),
        AIMessage(content="", tool_calls=[{"name": tool_name, "args": {}, "id": "c2"}]),
        ToolMessage(content="new roster", name=tool_name, tool_call_id="c2"),
    ]


def _tool_contents(messages):
    return [m.content for m in messages if m.type == "tool"]


def test_earlier_roster_results_are_hidden_only_after_a_roster_change():
    from src.agent.graph import OUTDATED_RESULT, hide_outdated_results
    from src.agent.tools import ROSTER_TOOLS

    changed = "2026-10-06T12:05:00+00:00"
    assert _tool_contents(hide_outdated_results(_history(), changed, ROSTER_TOOLS)) == [OUTDATED_RESULT, "new roster"]
    # No change since: follow-ups keep the numbers they need.
    assert _tool_contents(hide_outdated_results(_history(), None, ROSTER_TOOLS)) == ["old roster", "new roster"]
    # A change before the earlier question doesn't outdate it.
    before = "2026-10-06T11:00:00+00:00"
    assert _tool_contents(hide_outdated_results(_history(), before, ROSTER_TOOLS)) == ["old roster", "new roster"]
    # Tools that don't depend on rosters are left alone.
    league = _history("get_league_state")
    assert _tool_contents(hide_outdated_results(league, changed, ROSTER_TOOLS)) == ["old roster", "new roster"]


def test_the_current_questions_results_are_never_hidden():
    from src.agent.graph import hide_outdated_results
    from src.agent.tools import ROSTER_TOOLS

    after_everything = "2026-10-06T13:00:00+00:00"
    assert _tool_contents(hide_outdated_results(_history(), after_everything, ROSTER_TOOLS))[-1] == "new roster"


def test_graph_sends_hidden_results_but_keeps_the_stored_ones():
    from langchain_core.messages import AIMessage

    from src.agent.graph import OUTDATED_RESULT, build_graph
    from src.agent.tools import ROSTER_TOOLS

    sent = []

    def fake_llm(messages):
        sent.append(messages)
        return AIMessage(content="ok")

    graph = build_graph(fake_llm, [], lambda: "sys", roster_changed_at=lambda: "2026-10-06T12:05:00+00:00",
                        roster_tools=ROSTER_TOOLS)
    thread = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": _history()[:5]}, config=thread)

    assert _tool_contents(sent[0]) == [OUTDATED_RESULT]
    assert _tool_contents(graph.get_state(thread).values["messages"]) == ["old roster"]
