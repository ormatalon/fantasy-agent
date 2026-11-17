"""Each result has the keys the agent's contract fixtures (src/agent/tests/fixtures/contract) show, all the way down."""

import json
from pathlib import Path

from decisions import lineup, projection_tool, results, stash, trades, waivers
from decisions.tests.support import LEAGUE, drop, seed_projections, set_game_status
from decisions.tests.test_stash import put_news
from decisions.tests.test_results import put_stats
from storage.db import set_settings

CONTRACT = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "contract"


def shape(x):
    """Nested key sets; a list is described by its first item; None matches anything."""
    if isinstance(x, dict):
        return {k: shape(v) for k, v in x.items()}
    if isinstance(x, list):
        return [shape(x[0])] if x else []
    return None


def same(mine, theirs, path=""):
    if mine is None or theirs is None:
        return []
    if isinstance(mine, dict) and isinstance(theirs, dict):
        out = [f"{path}: keys {sorted(mine)} != {sorted(theirs)}"] if set(mine) != set(theirs) else []
        for k in set(mine) & set(theirs):
            out += same(mine[k], theirs[k], f"{path}.{k}")
        return out
    if isinstance(mine, list) and isinstance(theirs, list):
        return same(mine[0], theirs[0], path + "[]") if mine and theirs else []
    return [] if type(mine) is type(theirs) or not isinstance(mine, (dict, list)) and not isinstance(theirs, (dict, list)) else [f"{path}: {mine} != {theirs}"]


def check(result, name):
    fixture = json.loads((CONTRACT / f"{name}.json").read_text(encoding="utf-8"))
    problems = same(shape(result), shape(fixture))
    assert not problems, "\n".join(problems)
    json.dumps(result)  # plain data only


def test_lineup(league):
    set_game_status(league, "BUF", 11, "in_progress")
    check(lineup.recommend_lineup(league, LEAGUE), "lineup")


def test_waivers(league):
    drop(league, "10229")
    seed_projections(league, means={"10229": 20.0, "12526": 18.0}, byes=[("12526", 11)])
    set_game_status(league, "KC", 11, "in_progress")
    check(waivers.get_waiver_targets(league, LEAGUE), "waivers")


def test_stash(league):
    drop(league, "10892")
    league.execute("update player set injury_status = 'Out', injury_body_part = 'Ankle' where player_id = '10892'")
    seed_projections(league, means={"10892": 0.0}, healthy={"10892": 90.0})
    put_news(league)
    check(stash.get_injured_stash(league, LEAGUE), "stash")


def test_trades(league):
    seed_projections(league, default=10.0, means={"7564": 20.0, "12481": 2.0, "7588": 2.0, "11439": 14.0})
    check(trades.evaluate_trade(league, LEAGUE, ["11632", "11439"], ["7564"], 2), "trade_evaluation")
    check(trades.find_trade_ideas(league, LEAGUE, target_player_id="7564", with_fantasy_team_id=2, my_tradeable=["11439"]), "trade_ideas")
    set_settings(league, {"nfl_state": {"season": 2026, "week": 12}})
    league.execute("update nfl_game set status = 'final' where week = 11")
    seed_projections(league, weeks=range(12, 19))
    check(trades.find_trade_ideas(league, LEAGUE, target_player_id="7564", with_fantasy_team_id=2), "trade_ideas_after_deadline")


def test_results_and_projections(league):
    check(results.get_week_results(league, LEAGUE), "week_results")
    put_stats(league, 8)
    check(results.get_season_leaders(league, LEAGUE, through_week=8), "leaders")
    check(projection_tool.get_projections(league, LEAGUE, position="RB", horizon="ros"), "projections")


def test_trending(league, monkeypatch):
    from decisions import trending
    from ingestion.tests.support import fixture

    monkeypatch.setattr(trending.sleeper, "get_trending", lambda kind, hours, limit: [{"player_id": "12526", "count": fixture("trending_add_24h.json")[0]["count"]}])
    check(trending.get_trending_players(league, LEAGUE), "trending")
