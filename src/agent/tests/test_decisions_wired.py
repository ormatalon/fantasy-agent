"""Decision tools against the real providers in src/decisions, over the Or Test League with the real week 10-11
NFL schedule (this week 11) and projections seeded over its real rosters (src/decisions/tests/support.py)."""

import inspect

import pytest

import decisions
from agent import chat, tools
from decisions import artifact_view
from decisions.tests.support import seed_projections
from ingestion.tests.support import load_games
from .conftest import TEST_LEAGUE, WIRED

ARGS = {  # one call per decision tool, arguments as the model would send them
    "get_projections": {"players": ["Jalen Hurts"], "horizon": "ros"},
    "get_season_leaders": {"position": "RB"},
    "recommend_lineup": {},
    "get_waiver_targets": {"position": "WR"},
    "get_injured_stash": {},
    "evaluate_trade": {"give": ["Malik Nabers"], "get": ["Justin Jefferson"]},
    "find_trade_ideas": {"need_position": "WR", "fantasy_team": "OrHuji", "my_tradeable": ["Malik Nabers"]},
    "get_trending_players": {},
    "get_week_results": {},
}


@pytest.fixture
def decided(ctx, monkeypatch):
    monkeypatch.setattr(tools, "PROVIDERS", decisions.PROVIDERS)
    monkeypatch.setattr(artifact_view, "ensure", lambda conn, league_id, now=None: [])  # the test seeds the artifact
    load_games(ctx.conn)
    seed_projections(ctx.conn)
    return ctx


def test_tools_use_the_decisions_providers_and_every_provider_has_a_tool():
    assert WIRED is decisions.PROVIDERS
    assert set(decisions.PROVIDERS) <= set(tools.WHAT) == set(ARGS)


def test_tool_arguments_bind_to_the_provider_signatures(ctx, monkeypatch):
    """Each tool resolves names to ids and passes inputs the real provider accepts (with_fantasy_team_id, ...)."""
    seen = {}

    def recorder(name):
        def provider(conn, league_id, **inputs):
            seen[name] = inputs
            raise decisions.NoData("recorded")
        return provider

    monkeypatch.setattr(tools, "PROVIDERS", {n: recorder(n) for n in decisions.PROVIDERS})
    for name in decisions.PROVIDERS:
        tools.run_tool(ctx, name, ARGS[name])
        inspect.signature(decisions.PROVIDERS[name]).bind(ctx.conn, ctx.league_id, **seen[name])
    assert seen["find_trade_ideas"] == {"target_player_id": None, "need_position": "WR", "with_fantasy_team_id": 2,
                                        "my_tradeable": ["11632"], "horizon": "ros", "limit": 5}


def test_every_decision_tool_answers_from_stored_data(decided):
    for name, args in ARGS.items():
        r = tools.run_tool(decided, name, args)
        if name not in decisions.PROVIDERS:
            assert r.text.startswith("ERROR: ") and "not available in this version yet" in r.text, name
            continue
        assert r.ok and r.text.startswith("SUMMARY: ") and "\nAS OF: " in r.text, (name, r.text)


def test_lineup_and_its_matchup(decided):
    text = tools.run_tool(decided, "recommend_lineup", {}).text
    assert "WEEK 11 MATCHUP: vs OrHuji (fantasy team), their projected total 55.0" in text
    assert "YOUR LINEUP AS SET IN SLEEPER: projected 60.0, spread +5.0, win probability 64%" in text
    qb = next(line for line in text.splitlines() if line.startswith("  QB: Josh Allen"))
    assert "vs MIA" in qb and qb.endswith("not started, projected 5.0")
    assert "LOCKED" not in text  # conftest NOW is before the week 11 kickoffs


def test_lineup_with_no_stored_matchup_has_no_spread(decided):
    decided.conn.execute("delete from matchup where week = 11")
    r = tools.run_tool(decided, "recommend_lineup", {})
    assert r.ok and "WEEK 11 MATCHUP: no opponent stored (bye or not scheduled)" in r.text
    assert "EXPECTED-POINTS LINEUP: projected 60.0" in r.text.splitlines() and "spread +" not in r.text
    assert "no opponent stored for week 11" in r.text.splitlines()[-1]


def test_ir_player_with_no_return_date_says_so(decided):
    text = tools.run_tool(decided, "get_projections", ARGS["get_projections"]).text
    assert "1. Jalen Hurts, QB, NFL TEAM: PHI, FANTASY TEAM: OrHuji, injury: IR" in text
    assert "  RETURN: unknown (no source has a return date)" in text.splitlines()


def test_week_results_have_one_source(decided):
    r = tools.run_tool(decided, "get_week_results", {})
    assert r.text.startswith("SUMMARY: Week 10: you won 180.7 to 130.4 against OrHuji")
    assert [s["label"] for s in r.sources] == ["Sleeper matchups"]


def test_no_data_reaches_the_model_as_a_plain_failure(decided):
    """decisions.NoData: the message, no exception name or stack trace."""
    r = tools.run_tool(decided, "get_week_results", {"week": 11})
    assert not r.ok and r.text == "ERROR: week 11 has not been played; the last completed week is 10"
    r = tools.run_tool(decided, "recommend_lineup", {"week": 3})
    assert not r.ok and r.text == "ERROR: projections cover weeks 11 to 18; week 3 is not one of them"


def test_trending_question_routes_to_the_trending_tool(decided, openrouter):
    """Recorded live 2026-11-17 (stage 2.5 acceptance): the model calls get_trending_players and answers."""
    openrouter.reply("live_trending_1_tool_call.json", "live_trending_2_answer.json")
    qid = chat.start_question(decided.conn, "Who is trending up in adds?")
    events = []
    chat.run_question(decided.conn, qid, lambda event, payload: events.append((event, payload)))
    tool = next(r for r in chat.trace(decided.conn, TEST_LEAGUE, qid)["rows"] if r["role"] == "tool")
    assert tool["meta"]["name"] == "get_trending_players" and tool["meta"]["ok"]
    assert tool["content"].startswith("SUMMARY: ") and "AS OF: Sleeper trending" in tool["content"]
    assert events[-1][0] == "answer" and "Sleeper trending" in {s["label"] for s in events[-1][1]["sources"]}
