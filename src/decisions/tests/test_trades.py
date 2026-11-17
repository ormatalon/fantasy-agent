"""Trade evaluation and ideas (PRD 4.4): lineup value change for both sides, roster spots in uneven trades, deadline.

Or Test League week 11, fantasy team 1 (me) and OrHuji (fantasy team 2). 8 weeks left (11 to 18), so a
10-points-a-week difference is 80 for the rest of season. Projections are seeded numbers over the real rosters.
"""

import pytest

from decisions import trades
from decisions.league import NoData
from decisions.tests.support import LEAGUE, seed_projections
from storage.db import set_settings

NABERS, MCLAUGHLIN, BARKLEY = "11632", "11439", "4866"  # my bench
CHASE, RICE, EGBUKA, SKATTEBO, WILLIAMS = "7564", "10229", "12514", "12481", "7588"  # OrHuji
MCMILLAN = "12526"  # free agent


def evaluate(conn, give, get, horizon="ros"):
    return trades.evaluate_trade(conn, LEAGUE, give, get, 2, horizon)


def test_both_sides_get_a_starting_lineup_value_change_and_the_verdict(league):
    seed_projections(league, default=10.0, means={CHASE: 20.0})
    r = evaluate(league, [NABERS], [CHASE])
    # I start Chase (20) in a wide receiver spot instead of a 10: +10 a week; OrHuji's next best wide receiver is a 10
    assert r["mine"]["value_change"] == pytest.approx(80.0) and r["theirs"]["value_change"] == pytest.approx(-80.0)
    assert r["mine"]["lineup_value_after"] - r["mine"]["lineup_value_before"] == pytest.approx(80.0)
    assert r["mine"]["fantasy_team"] == "OrMatalon" and r["theirs"]["fantasy_team"] == "OrHuji"
    assert [p["name"] for p in r["mine"]["gives"]] == ["Malik Nabers"] and [p["name"] for p in r["mine"]["receives"]] == ["Ja'Marr Chase"]
    assert r["mine"]["receives"][0]["fantasy_status"] == "fantasy_team:OrHuji"
    assert r["verdict"] == "I gain more" and r["horizon"] == "ros" and r["mine"]["adjustments"] == r["theirs"]["adjustments"] == []
    assert "you gain 80.0" in r["summary"] and "OrHuji loses 80.0" in r["summary"]
    assert r["mine"]["why"]["reasons"][0]["code"] == "lineup_value"


def test_the_week_horizon_counts_only_this_week(league):
    seed_projections(league, default=10.0, means={CHASE: 20.0})
    r = evaluate(league, [NABERS], [CHASE], "week")
    assert r["mine"]["value_change"] == pytest.approx(10.0) and r["horizon"] == "week"


def test_verdict_labels(league):
    assert trades._verdict(5.0, 2.0) == "both gain" and trades._verdict(-1.0, -2.0) == "both lose"
    assert trades._verdict(0.0, 0.0) == "no change for either"
    assert trades._verdict(5.0, -2.0) == "I gain more" and trades._verdict(-5.0, 2.0) == "they gain more"


def test_two_for_one_gives_the_side_with_fewer_players_the_best_free_agent(league):
    seed_projections(league, default=10.0, means={CHASE: 20.0, EGBUKA: 1.0, MCMILLAN: 9.0})
    r = evaluate(league, [NABERS, MCLAUGHLIN], [CHASE])
    # I give two and get one: 17 - 2 + 1 = 16 players, one open spot goes to the best free agent
    assert [(a["action"], a["player"]["name"], a["reason"]) for a in r["mine"]["adjustments"]] == [("add_free_agent", "Tetairoa McMillan", "roster_spot")]
    # OrHuji gets two and gives one: 18 players, over the 17 allowed, so the lowest-value bench player goes
    assert [(a["action"], a["player"]["name"]) for a in r["theirs"]["adjustments"]] == [("drop", "Emeka Egbuka")]
    assert "roster_spot" in [x["code"] for x in r["theirs"]["why"]["reasons"]]


def test_the_free_agent_added_for_an_open_spot_is_the_one_that_raises_the_lineup_most(league):
    # McMillan (WR) and Dicker (K, dropped by OrHuji) are free; my K slot is held by Shrader at 5, Dicker would be 25
    from decisions.tests.support import drop

    drop(league, "8259")
    seed_projections(league, default=10.0, means={"8259": 25.0, MCMILLAN: 11.0, "12185": 5.0})
    r = evaluate(league, [NABERS, MCLAUGHLIN], [CHASE])
    assert r["mine"]["adjustments"][0]["player"]["name"] == "Cameron Dicker"  # better for the lineup than the higher-mean WR


def test_an_even_trade_changes_no_roster_spots(league):
    seed_projections(league, default=10.0)
    r = evaluate(league, [NABERS], [RICE])
    assert r["mine"]["adjustments"] == [] and r["theirs"]["adjustments"] == []


def test_players_must_be_on_the_rosters_named(league):
    seed_projections(league)
    with pytest.raises(NoData, match="not on my roster"):
        evaluate(league, [CHASE], [RICE])
    with pytest.raises(NoData, match="not on OrHuji's roster"):
        evaluate(league, [NABERS], [BARKLEY])


def test_evaluating_after_the_deadline_still_works_and_says_so(league):
    seed_projections(league, default=10.0)
    assert evaluate(league, [NABERS], [RICE])["deadline"] == {"week": 11, "passed": False}
    set_settings(league, {"nfl_state": {"season": 2026, "week": 12}})
    league.execute("update nfl_game set status = 'final' where week = 11")
    seed_projections(league, default=10.0, weeks=range(12, 19))
    r = evaluate(league, [NABERS], [RICE])
    assert r["deadline"] == {"week": 11, "passed": True} and "trade deadline passed (week 11)" in r["warnings"]


# I hold five running backs at 14 for four running-back-capable slots, so one is spare; OrHuji's second running
# back spot is held by a 2. Chase (20) beats my wide receivers (10). Giving a spare running back for Chase raises both.
SPARE_RB_SEASON = {"8151": 14.0, "12527": 14.0, "8228": 14.0, BARKLEY: 14.0, MCLAUGHLIN: 14.0, CHASE: 20.0, SKATTEBO: 2.0, WILLIAMS: 2.0}


def test_ideas_name_the_other_fantasy_team_and_both_value_changes(league):
    seed_projections(league, default=10.0, means=SPARE_RB_SEASON)
    r = trades.find_trade_ideas(league, LEAGUE, need_position="WR", my_tradeable=[MCLAUGHLIN])
    assert "OrHuji" in r["searched_fantasy_teams"] and len(r["searched_fantasy_teams"]) == 3
    top = r["ideas"][0]
    ev = top["evaluation"]
    assert (top["rank"], ev["theirs"]["fantasy_team"]) == (1, "OrHuji")
    assert [p["name"] for p in ev["mine"]["gives"]] == ["Jaleel McLaughlin"] and [p["name"] for p in ev["mine"]["receives"]] == ["Ja'Marr Chase"]
    assert ev["mine"]["value_change"] == pytest.approx(80.0) and ev["theirs"]["value_change"] == pytest.approx(16.0)  # both rise
    assert top["why"]["reasons"][0]["code"] == "lineup_value"
    assert top["why"]["reasons"][0]["values"]["their_change"] == ev["theirs"]["value_change"]
    assert all(i["evaluation"]["mine"]["value_change"] > 0 and i["evaluation"]["theirs"]["value_change"] > 0 for i in r["ideas"])
    gains = [i["evaluation"]["mine"]["value_change"] for i in r["ideas"]]
    assert gains == sorted(gains, reverse=True)


def test_asked_about_one_fantasy_team_only_that_one_is_searched(league):
    seed_projections(league, default=10.0, means=SPARE_RB_SEASON)
    r = trades.find_trade_ideas(league, LEAGUE, target_player_id=CHASE, with_fantasy_team_id=2)  # my whole roster is on offer
    assert r["searched_fantasy_teams"] == ["OrHuji"] and r["ideas"]
    assert {i["evaluation"]["theirs"]["fantasy_team"] for i in r["ideas"]} == {"OrHuji"}
    assert {i["evaluation"]["mine"]["gives"][0]["position"] for i in r["ideas"]} == {"RB"}  # only a spare running back helps them
    other = trades.find_trade_ideas(league, LEAGUE, target_player_id=CHASE, with_fantasy_team_id=3)
    assert other["ideas"] == [] and other["searched_fantasy_teams"] == []  # Chase is not on that fantasy team


def test_a_general_search_needs_to_know_which_of_my_players_are_tradeable(league):
    seed_projections(league)
    with pytest.raises(NoData, match="willing to trade"):
        trades.find_trade_ideas(league, LEAGUE, need_position="WR")


def test_no_ideas_after_the_trade_deadline(league):
    set_settings(league, {"nfl_state": {"season": 2026, "week": 12}})
    league.execute("update nfl_game set status = 'final' where week = 11")
    seed_projections(league, default=10.0, weeks=range(12, 19), means={CHASE: 20.0})
    r = trades.find_trade_ideas(league, LEAGUE, target_player_id=CHASE, with_fantasy_team_id=2)
    assert r["ideas"] == [] and r["deadline"] == {"week": 11, "passed": True}
    assert r["summary"] == "No trade ideas: the trade deadline passed (week 11)." and "trade deadline passed (week 11)" in r["warnings"]
    assert r["searched_fantasy_teams"] == ["OrHuji"]


def test_ir_and_taxi_players_do_not_count_against_the_roster_limit(league):
    from decisions.tests.support import move

    move(league, "10892", "reserve")  # Anderson to my IR: 16 active players and one on IR
    move(league, WILLIAMS, "reserve", roster_id=2)  # OrHuji: also 16 active
    seed_projections(league, default=10.0, means={CHASE: 20.0, EGBUKA: 1.0})
    r = evaluate(league, [NABERS, MCLAUGHLIN], [CHASE])
    # I give two, get one: 15 active against a limit of 17, so the one spot the trade opens is refilled
    assert len(r["mine"]["adjustments"]) == 1 and r["mine"]["adjustments"][0]["action"] == "add_free_agent"
    # OrHuji: 16 active - 1 + 2 = 17 is within the limit of 17: nobody is dropped (counting IR it would be 18)
    assert r["theirs"]["adjustments"] == []
