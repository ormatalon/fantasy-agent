"""Waiver targets (PRD 4.4): value over replacement on two horizons, game-started and bye exclusions, FAAB budget.

Free agents are made by dropping real players from the stored rosters; projections are seeded numbers.
Wide receivers rostered in Or Test League: Nacua, Washington, Watson, Nabers (mine); Chase, Smith-Njigba,
Jefferson, Rice, Egbuka (OrHuji); St. Brown, Lamb, London, McConkey, Collins (team 3); Brown, Pickens, Olave (team 4).
"""

import json

import pytest

from decisions import waivers
from decisions.tests.support import LEAGUE, drop, put_on_bye, seed_projections, set_game_status

RICE, EGBUKA, WILLIAMS, MCMILLAN = "10229", "12514", "7588", "12526"


def targets(conn, **kw):
    return waivers.get_waiver_targets(conn, LEAGUE, **kw)


def rostered_wrs(conn):
    return [r["player_id"] for r in conn.execute(
        "select rp.player_id from roster_player rp join player p using(player_id) where p.position = 'WR' and rp.role in ('starter', 'bench')")]


def test_free_agents_rank_by_value_over_the_replacement_level_and_flex_slots_split_credit(league):
    drop(league, RICE)  # Rice and McMillan (already free) are the free wide receivers
    wrs = rostered_wrs(league)
    means = {pid: 12.0 - 0.5 * i for i, pid in enumerate(wrs)}  # 12.0, 11.5, ... rostered; the Nth best is the replacement
    seed_projections(league, means={**means, RICE: 14.0, MCMILLAN: 12.0})
    r = targets(league, horizon="week", position="WR")
    week = r["lists"][0]
    # 4 fantasy teams x (2 WR slots + FLEX/3 + SUPER_FLEX/4) = 10.33, so the 10th best rostered WR is replacement
    n = 10
    expected = sorted(means.values(), reverse=True)[n - 1]
    first = week["candidates"][0]
    assert first["player"]["name"] == "Rashee Rice" and first["player"]["fantasy_status"] == "free_agent"
    assert first["replacement"] == {"pool": "WR", "rank_n": n, "points": expected}
    assert first["vorp"] == pytest.approx(14.0 - expected)
    assert [c["rank"] for c in week["candidates"]] == [1, 2]
    assert week["candidates"][1]["player"]["name"] == "Tetairoa McMillan" and first["vorp"] > week["candidates"][1]["vorp"]
    assert first["why"]["reasons"][0]["code"] == "replacement_level"
    assert first["faab_guide"] is None and r["budget"]["type"] == "rolling"


def test_two_horizons_come_back_when_none_is_named(league):
    drop(league, RICE)
    seed_projections(league, means={RICE: {11: 5.0, **{w: 25.0 for w in range(12, 19)}}})  # nothing this week, a lot after
    r = targets(league, position="WR")
    assert [x["horizon"] for x in r["lists"]] == ["week", "ros"]
    assert r["lists"][0]["candidates"] == [] or r["lists"][0]["candidates"][0]["player"]["name"] != "Rashee Rice"
    assert r["lists"][1]["candidates"][0]["player"]["name"] == "Rashee Rice"
    assert r["summary"].startswith("Top pickup") and "rest of season: Rashee Rice" in r["summary"]


def test_a_free_agent_whose_game_started_or_who_is_on_bye_is_left_out_this_week(league):
    drop(league, RICE)
    seed_projections(league, means={RICE: 20.0, MCMILLAN: 18.0}, byes=[(MCMILLAN, 11)])
    set_game_status(league, "KC", 11, "in_progress")  # Rice's game started
    put_on_bye(league, "CAR", 11)  # McMillan is on bye
    r = targets(league, horizon="week", position="WR")
    week = r["lists"][0]
    assert [c["player"]["name"] for c in week["candidates"]] == []
    assert {(x["player"]["name"], x["reason"]) for x in week["excluded"]} == {("Rashee Rice", "game_started"), ("Tetairoa McMillan", "bye")}
    ros = targets(league, horizon="ros", position="WR")["lists"][0]  # rest of season still lists them
    assert {c["player"]["name"] for c in ros["candidates"]} == {"Rashee Rice", "Tetairoa McMillan"} and ros["excluded"] == []


def test_a_rostered_player_projected_zero_does_not_drag_the_replacement_level_down(league):
    wrs = rostered_wrs(league)
    means = dict.fromkeys(wrs, 8.0) | {wrs[0]: 0.0, wrs[1]: 0.0}  # two rostered WRs at 0 (injured)
    seed_projections(league, means={**means, MCMILLAN: 11.0})
    first = targets(league, horizon="week", position="WR")["lists"][0]["candidates"][0]
    assert first["replacement"]["points"] == 8.0 and first["vorp"] == pytest.approx(3.0)


def test_faab_guide_stays_inside_the_minimum_bid_and_the_money_left(league):
    seed_projections(league, means={MCMILLAN: 30.0})
    row = league.execute("select format, settings from league").fetchone()
    fmt, settings = json.loads(row["format"]), json.loads(row["settings"])
    fmt.update(waiver_type="faab", faab_budget=100, faab_remaining={"1": 8})
    settings["waiver_bid_min"] = 1
    league.execute("update league set format = ?, settings = ?", (json.dumps(fmt), json.dumps(settings)))
    r = targets(league, horizon="week", position="WR")
    assert r["budget"] == {"type": "faab", "total": 100, "remaining": 8, "min_bid": 1}
    assert r["lists"][0]["candidates"][0]["faab_guide"] == 8  # 4% of 100 per point over replacement, 25 points: capped at the 8 left
    fmt["faab_remaining"] = {"1": 100}
    league.execute("update league set format = ?", (json.dumps(fmt),))
    big = targets(league, horizon="week", position="WR")["lists"][0]["candidates"][0]
    assert big["faab_guide"] == round(big["vorp"] * 0.04 * 100)
    seed_projections(league, means={MCMILLAN: 5.2})  # barely above replacement: the minimum bid
    assert targets(league, horizon="week", position="WR")["lists"][0]["candidates"][0]["faab_guide"] == 1


def test_a_position_with_no_slot_in_the_league_is_not_a_target(league):
    row = league.execute("select format from league").fetchone()
    fmt = json.loads(row["format"])
    assert any(r["position"] == "K" for r in league.execute("select position from player where player_id = '8259'"))
    drop(league, "8259")  # Dicker, a kicker, becomes a free agent
    seed_projections(league, means={"8259": 30.0})
    names = [c["player"]["name"] for c in targets(league, horizon="week")["lists"][0]["candidates"]]
    assert "Cameron Dicker" in names
    fmt["slots"] = [s for s in fmt["slots"] if s != "K"]
    league.execute("update league set format = ?", (json.dumps(fmt),))
    names = [c["player"]["name"] for c in targets(league, horizon="week")["lists"][0]["candidates"]]
    assert "Cameron Dicker" not in names
