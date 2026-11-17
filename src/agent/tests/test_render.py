"""Decision results (contract-shaped fixtures) render with SUMMARY first, AS OF, labeled lines, WARNINGS."""

import re

import pytest

from agent import render
from .conftest import contract

KINDS = {
    "lineup": render.lineup, "waivers": render.waivers, "stash": render.stash,
    "trade_evaluation": render.trade_evaluation, "trade_ideas": render.trade_ideas,
    "trade_ideas_after_deadline": render.trade_ideas, "week_results": render.week_results,
    "leaders": render.leaders, "projections": render.projections, "trending": render.trending,
}


@pytest.mark.parametrize("name", KINDS)
def test_summary_first_then_as_of_with_source_labels(name):
    result = contract(name)
    lines = KINDS[name](result).splitlines()
    assert lines[0] == f"SUMMARY: {result['summary']}"
    assert lines[1].startswith("AS OF: ")
    for source in render.sources_of(result):
        assert source["label"] in lines[1]


@pytest.mark.parametrize("name", KINDS)
def test_unprojected_scoring_items_and_warnings_are_stated(name):
    result = contract(name)
    text = KINDS[name](result)
    warnings = next((line for line in text.splitlines() if line.startswith("WARNINGS: ")), None)
    if not result["unprojected_scoring_items"] and not result["warnings"] and warnings is None:
        return  # actual points (week results, leaders) cover every scoring item
    for item in result["unprojected_scoring_items"]:
        assert render.SCORING_LABELS[item["key"]] in warnings and item["key"] not in warnings
    for w in result["warnings"]:
        assert w in warnings


def test_every_player_line_carries_its_fantasy_status_and_nfl_team():
    text = render.lineup(contract("lineup"))
    assert "Josh Allen, QB, NFL TEAM: BUF, MY ROSTER (starter, QB)" in text
    assert "Saquon Barkley, RB, NFL TEAM: PHI, MY ROSTER (bench)" in text
    assert "FREE AGENT" in render.waivers(contract("waivers"))
    assert "FANTASY TEAM: OrHuji" in render.trade_evaluation(contract("trade_evaluation"))


def test_lineup_prints_all_three_plans_with_tool_numbers():
    text = render.lineup(contract("lineup"))
    assert "YOUR LINEUP AS SET IN SLEEPER: projected 108.2, spread +10.2, win probability 68%" in text
    assert "EXPECTED-POINTS LINEUP: projected 112.4, spread +14.4, win probability 71%" in text
    assert "HIGH-RISK, HIGH-REWARD LINEUP: projected 111.0, spread +13.0, win probability 69%" in text
    assert "their projected total 98.0" in text
    assert "projected points +4.2" in text and "Barkley projects 15.1, Watson 10.9." in text
    assert "at FLEX, benching Christian Watson, projected points +4.2" in text
    assert "SUPER_FLEX: (empty)" in text and "EMPTY SLOTS (no eligible player): SUPER_FLEX" in text
    assert "LOCKED (starter, game started)" in text


def test_lineup_slot_changes_and_no_negative_gain_in_the_expected_plan():
    text = render.lineup(contract("lineup"))
    expected = text[text.index("EXPECTED-POINTS LINEUP:"):text.index("HIGH-RISK, HIGH-REWARD LINEUP:")]
    assert "  move Malik Nabers from FLEX to WR (still starting)" in expected.splitlines()
    assert "projected points -" not in expected and "upside" not in expected and "fewer" not in expected


def test_lineup_without_a_matchup_has_no_spread_or_win_probability():
    """Bye or no stored matchup: the provider sends matchup, spread and win probability as null."""
    r = contract("lineup") | {"matchup": None}
    for key in ("current", "expected", "high_risk"):
        r[key] |= {"spread": None, "win_probability": None}
    text = render.lineup(r)
    assert "WEEK 9 MATCHUP: no opponent stored (bye or not scheduled), so no spread and no win probability" in text
    assert "EXPECTED-POINTS LINEUP: projected 112.4" in text.splitlines()
    assert "spread +" not in text and "spread -" not in text and "%" not in text


def test_waivers_two_labeled_horizons_and_exclusions():
    text = render.waivers(contract("waivers"))
    assert "THIS WEEK:" in text and "REST OF SEASON:" in text
    assert "left out: Amon-Ra St. Brown (his game already started)" in text
    assert "FAAB guide" not in text  # rolling waivers: no dollars
    assert "BUDGET: no FAAB, waiver order is rolling" in text


def test_stash_quotes_news_prose_and_injury_source():
    text = render.stash(contract("stash"))
    assert "injury IR (Knee) (per official NFL injury report via nflverse" in text
    assert "Achane (knee) is expected to be designated to return later this month." in text


def test_stash_news_without_text_prints_the_title_only():
    r = contract("stash")
    r["candidates"][0]["news"][0]["text"] = None
    title = r["candidates"][0]["news"][0]["title"]
    text = render.stash(r)
    assert any(line.endswith(f"): {title}") for line in text.splitlines()) and "None" not in text


def test_trade_evaluation_shows_both_sides_roster_spots_verdict_deadline():
    text = render.trade_evaluation(contract("trade_evaluation"))
    assert "change +6.3" in text and "change +2.1" in text
    assert "roster spot: adds the best free agent Tetairoa McMillan" in text
    assert "roster spot: drops Javonte Williams" in text
    assert "VERDICT: I gain more" in text and "TRADE DEADLINE: week 11 (not passed)" in text


def test_trade_ideas_after_deadline_has_no_ideas():
    text = render.trade_ideas(contract("trade_ideas_after_deadline"))
    assert "IDEAS: none" in text and "trade deadline passed (week 11)" in text
    assert "Why it helps them: Their starting lineup value rises by 2.1." in render.trade_ideas(contract("trade_ideas"))


def test_week_results_prints_optimal_points_bench_and_moves():
    text = render.week_results(contract("week_results"))
    assert "WEEK 8 RESULT: WIN, OrMatalon 180.7, OrHuji (fantasy team) 130.4" in text
    assert "best possible lineup from your roster 188.5 (from actual points, not a projection)" in text
    assert "points left on your bench 7.8" in text
    assert "start Saquon Barkley instead of Christian Watson, +7.8" in text


def test_projections_return_line_with_and_without_a_date():
    text = render.projections(contract("projections"))
    assert ("RETURN: week 13 (return date 2026-12-06, source ESPN injury list" in text
            and "a news estimate, not an official report). Weeks before 13 project 0 (on IR)" in text)
    assert "projected 80.0, likely range 62.7 to 97.3, 5 games, 0 counted while on IR" in text  # ros row, not 0
    # IR with no return date: Sleeper's own rows, not zeroed (data contract section 7)
    assert "Myles Garrett, DE, NFL TEAM: LAR, FANTASY TEAM: OrHuji, injury: IR" in text
    assert "projected 12.0, likely range 6.5 to 17.5, 8 games, sources: sleeper 100%" in text
    no_date = "RETURN: unknown (no source has a return date)"
    assert f"  {no_date}" in text.splitlines()
    assert f"Myles Garrett: {no_date}" in text.splitlines()[-1]  # also stated in WARNINGS
    assert "likely range 100.4 to 140.4" in text  # mean 120.4, sd 20


def test_projections_return_date_after_week_18_projects_0_throughout():
    row = contract("projections")["rows"][1] | {"return_week": None, "return_date": "2027-01-20", "mean": 0.0}
    assert render.return_line(row).startswith("RETURN: after week 18 (return date 2027-01-20, source ESPN injury list")
    assert render.return_line(row).endswith("Projected 0 for every remaining week (on IR)")


def test_week_projection_row_on_ir_is_0():
    r = contract("projections") | {"horizon": "week"}
    r["rows"] = [r["rows"][1] | {"mean": 0.0, "variance": 0.0, "n_games": 1}]
    assert "projected 0.0, likely range 0.0 to 0.0, 1 games, projected 0 (on IR)" in render.projections(r)


def test_week_results_move_into_a_slot_left_empty():
    r = contract("week_results")
    r["my_bench"]["moves"][0]["bench"] = None
    assert "start Saquon Barkley instead of the empty slot, +7.8" in render.week_results(r)


def test_leaders_are_actual_points_not_projections():
    text = render.leaders(contract("leaders"))
    assert "ACTUAL POINTS IN YOUR LEAGUE SCORING THROUGH WEEK 8 (not projections)" in text
    assert "98.3 points, 8 games, 12.3 per game" in text


def test_trending_is_labeled_context_only():
    assert "(context only, not a projection)" in render.trending(contract("trending"))


@pytest.mark.parametrize("name", KINDS)
def test_no_snake_case_names_anywhere_including_warnings(name):
    """Field names and scoring keys invite the model to copy them (T1 rule 7); slot names are upper case."""
    assert not re.findall(r"[a-z]+_[a-z_]+", KINDS[name](contract(name)))


def test_unprojected_items_are_plain_words_with_points_each():
    warnings = next(l for l in render.lineup(contract("lineup")).splitlines() if l.startswith("WARNINGS: "))
    for label in ("defensive or special teams touchdown (6 points each)", "field goal of 50-59 yards (5 points each)",
                  "defensive touchdown (6 points each)", "zero points allowed (10 points each)"):
        assert label in warnings
    assert not re.findall(r"[a-z]+_[a-z_]+", warnings)


def test_every_scoring_key_in_the_league_fixtures_has_a_label():
    from ingestion.tests.support import fixture
    keys = set().union(*[league["scoring_settings"] for league in fixture("user_leagues_2026.json")])
    assert keys <= set(render.SCORING_LABELS)


def test_unmapped_scoring_key_is_spelled_out_and_logged(caplog):
    assert render.scoring_label("bonus_fd_qb") == "bonus fd qb"
    assert "bonus_fd_qb" in caplog.text
