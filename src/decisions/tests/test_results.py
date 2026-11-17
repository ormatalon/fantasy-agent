"""Week results, season leaders and the projection rows tool.

Matchup rows are the captured week 8 of Or Test League (Sleeper's players_points), served for every week by the
fake Sleeper; this week is 11, so the last completed week is 10.
"""

import json

import pytest

from decisions import projection_tool, results
from decisions.league import NoData
from decisions.tests.support import LEAGUE, seed_projections
from ingestion.tests.support import fixture
from projections.scoring import score


def test_week_results_score_result_opponent_scorers_and_points_left_on_the_bench(league):
    r = results.get_week_results(league, LEAGUE)  # last completed week
    assert (r["kind"], r["week"], r["result"]) == ("week_results", 10, "win")
    assert r["mine"] == {"fantasy_team": "OrMatalon", "fantasy_team_id": 1, "score": 180.66}
    assert r["opponent"] == {"fantasy_team": "OrHuji", "fantasy_team_id": 2, "score": 130.38}
    assert [(x["player"]["name"], x["slot"], x["points"]) for x in r["opponent_top_scorers"]] == [
        ("Ja'Marr Chase", "WR", 33.1), ("Jaxon Smith-Njigba", "WR", 23.8), ("Trey McBride", "TE", 20.2)]
    bench = r["my_bench"]
    assert bench["actual_points"] == 180.66
    # Saquon Barkley (12.4) and others on my bench outscored starters; the moves add up to the points left
    assert bench["points_left_on_bench"] > 0 and bench["optimal_points"] == pytest.approx(180.66 + bench["points_left_on_bench"], abs=0.06)
    assert sum(m["gain"] for m in bench["moves"]) == pytest.approx(bench["points_left_on_bench"], abs=0.2)
    assert all(m["gain"] > 0 and m["start"]["fantasy_status"] != "mine_starter" for m in bench["moves"] if m["start"]["name"] != "Tetairoa McMillan")
    assert r["summary"].startswith("Week 10: you won 180.7 to 130.4 against OrHuji;")
    assert r["as_of"]["rosters"]["source_label"] == "Sleeper matchups" and r["unprojected_scoring_items"] == []


def test_a_week_not_yet_played_or_without_a_matchup_is_refused(league):
    with pytest.raises(NoData, match="has not been played"):
        results.get_week_results(league, LEAGUE, week=11)
    league.execute("delete from matchup where week = 9")
    with pytest.raises(NoData, match="no matchup stored for week 9"):
        results.get_week_results(league, LEAGUE, week=9)


def put_stats(conn, week):
    for r in fixture("stats_2026_week8_sample.json"):
        conn.execute("insert or replace into player_week_stat values (2026, ?, ?, ?, '2026-11-17T09:00:00Z')",
                     (week, r["player_id"], json.dumps(r["stats"])))  # fmt: skip


def test_season_leaders_are_actual_points_in_the_league_scoring(league):
    put_stats(league, 8)
    r = results.get_season_leaders(league, LEAGUE, through_week=8)
    stats = {x["player_id"]: x["stats"] for x in fixture("stats_2026_week8_sample.json")}
    assert [(x["player"]["name"], x["games"]) for x in r["rows"]] == [("Josh Allen", 1), ("Jahmyr Gibbs", 1)]
    scoring = json.loads(league.execute("select scoring_settings from league").fetchone()[0])
    assert r["rows"][0]["points"] == round(score(stats["4984"], scoring), 1) and r["rows"][0]["points_per_game"] == r["rows"][0]["points"]
    assert r["source"] == "sleeper_stats" and r["through_week"] == 8 and r["unprojected_scoring_items"] == []
    assert any("stats not stored for weeks 1, 2, 3, 4, 5, 6, 7" in w for w in r["warnings"])  # only week 8 is stored


def test_season_leaders_by_position_and_games_played(league):
    put_stats(league, 8)
    put_stats(league, 9)
    rb = results.get_season_leaders(league, LEAGUE, position="RB", through_week=9)
    assert [x["player"]["name"] for x in rb["rows"]] == ["Jahmyr Gibbs"] and rb["rows"][0]["games"] == 2
    assert rb["rows"][0]["points_per_game"] == pytest.approx(rb["rows"][0]["points"] / 2, abs=0.1)
    assert "Top RBs in actual points through week 9: Jahmyr Gibbs leads" in rb["summary"]


def test_projection_rows_rank_by_mean_and_carry_sources_and_return_fields(league):
    seed_projections(league, default=10.0, means={"4866": 15.0, "6904": 0.0})
    league.execute("update projection set zero_reason = 'ir', return_week = 13, return_date = '2026-12-06', "
                   "return_date_source = 'espn', return_date_as_of = '2026-11-03T18:57:00Z' where player_id = '6904' and horizon = 'ros'")
    r = projection_tool.get_projections(league, LEAGUE, position="RB", horizon="week", limit=3)
    assert r["kind"] == "projections" and r["horizon"] == "week" and r["rows"][0]["player"]["name"] == "Saquon Barkley"
    assert [x["rank"] for x in r["rows"]] == [1, 2, 3] and r["rows"][0]["mean"] == 15.0 and r["rows"][0]["sources"][0]["source"] == "sleeper"
    hurts = projection_tool.get_projections(league, LEAGUE, player_ids=["6904", "99999"], horizon="ros")
    (row,) = hurts["rows"]
    assert (row["zero_reason"], row["return_week"], row["return_date_source"]) == ("ir", 13, "espn")
    assert "no projection for 99999" in hurts["warnings"]


def test_projection_rows_refuse_a_week_the_artifact_does_not_cover(league):
    with pytest.raises(NoData):
        projection_tool.get_projections(league, LEAGUE, position="RB", week=5)


def test_trending_players_carry_their_fantasy_status_in_my_league(league, monkeypatch):
    from decisions import trending

    real = fixture("trending_add_24h.json")  # Sleeper's counts; none of its ids is in this league's small catalog
    mine = [{"player_id": pid, "count": x["count"]} for pid, x in zip(["12526", "7564", "11632", "DEN"], real)]  # free agent, OrHuji, me, a defense (its id is the abbreviation)
    monkeypatch.setattr(trending.sleeper, "get_trending", lambda kind, hours, limit: [*mine, real[-1]][:limit])
    r = trending.get_trending_players(league, LEAGUE, "add", 24, 25)
    assert r["kind"] == "trending" and r["type"] == "add" and r["hours"] == 24
    assert list(r["as_of"]) == ["trending"] and r["as_of"]["trending"]["source_label"] == "Sleeper trending"
    assert [(x["rank"], x["player"]["fantasy_status"], x["count"]) for x in r["rows"]] == [
        (1, "free_agent", real[0]["count"]), (2, "fantasy_team:OrHuji", real[1]["count"]), (3, "mine_bench", real[2]["count"]),
        (4, "mine_starter", real[3]["count"])]
    assert r["rows"][3]["player"]["position"] == "DEF" and r["rows"][3]["player"]["name"] == "Denver Broncos"
    assert "context only, not a projection" in r["summary"]
    # the real trending ids are not in this league's 69-player catalog (checked against all three catalog fixtures)
    gone = real[-1]
    assert r["warnings"] == [f"1 trending players are not in the players catalog and are left out (Sleeper id, count): {gone['player_id']} ({gone['count']})"]


def test_a_slot_the_manager_left_empty_is_a_move_with_no_player_to_bench(league):
    # Shrader (K, 1.0 points in week 8's capture) is taken out of the lineup: the K slot was left empty
    league.execute("update matchup_player set is_starter = 0, slot_index = NULL where week = 10 and roster_id = 1 and player_id = '12185'")
    r = results.get_week_results(league, LEAGUE)
    (move,) = [m for m in r["my_bench"]["moves"] if m["slot"] == "K"]
    assert move["start"]["name"] == "Spencer Shrader" and move["bench"] is None and move["gain"] == 1.0


def test_full_season_projections_use_the_stored_weights_and_overlay_from_this_week(league, monkeypatch):
    from decisions import artifact_view
    from projections import artifact
    from storage.db import set_settings

    seen = {}
    set_settings(league, {"projection_weights": {"sleeper": 1.0, "nflverse": 0.0}})
    monkeypatch.setattr(artifact_view, "default_sources", lambda conn, season: [])
    monkeypatch.setattr(artifact, "build", lambda conn, league_id, season, week, sources, weights, overlay_from=None: seen.update(
        week=week, weights=weights, overlay_from=overlay_from) or ([], {}))
    projection_tool.get_projections(league, LEAGUE, position="RB", horizon="full_season")
    assert seen == {"week": 1, "weights": {"sleeper": 1.0, "nflverse": 0.0}, "overlay_from": 11}
