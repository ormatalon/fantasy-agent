"""Lineup rules (PRD 4.4) on the real Or Test League rosters, week 11 NFL schedule.

My starters in slot order: QB Allen 4984, RB Walker 8151, RB Jeanty 12527, WR Nacua 9493, WR Washington 9487,
TE Bowers 11604, FLEX Warren 8228, SUPER_FLEX Watson 8167, IDP_FLEX Rousseau 7627, IDP_FLEX Lloyd 8329,
K Shrader 12185, DEF DEN. Bench: Anderson 10892, McLaughlin 11439, Nabers 11632, Goff 3163, Barkley 4866.
Projections are seeded numbers over those real players.
"""

import math

import pytest

from decisions import lineup
from decisions.tests.support import LEAGUE, drop, move, put_on_bye, seed_projections, set_game_status

ALLEN, WARREN, WATSON, NABERS, GOFF, BARKLEY, SHRADER = "4984", "8228", "8167", "11632", "3163", "4866", "12185"


def rec(conn, **kw):
    return lineup.recommend_lineup(conn, LEAGUE, **kw)


def test_a_bench_player_who_projects_more_replaces_the_starter(league):
    seed_projections(league, default=10.0, means={BARKLEY: 15.0, WARREN: 8.0})
    r = rec(league)
    (change,) = r["expected"]["changes"]
    assert (change["slot"], change["player_out"]["name"], change["player_in"]["name"], change["mean_gain"]) == (
        "FLEX", "Jaylen Warren", "Saquon Barkley", 7.0)
    assert change["player_in"]["fantasy_status"] == "mine_bench" and change["player_out"]["lineup_slot"] == "FLEX"
    assert change["why"]["reasons"][0]["code"] == "projection_edge" and change["why"]["reasons"][0]["values"]["gain"] == 7.0
    assert r["current"]["changes"] == []
    assert r["expected"]["mine"]["mean"] - r["current"]["mine"]["mean"] == pytest.approx(7.0, abs=0.1)
    assert r["summary"].startswith("Start Saquon Barkley over Jaylen Warren at FLEX; the expected-points lineup projects")


def test_no_change_for_an_equal_or_tiny_gain(league):
    seed_projections(league, default=10.0, means={BARKLEY: 10.05, WARREN: 10.0})  # gain 0.05 is below 0.1
    assert rec(league)["expected"]["changes"] == []
    seed_projections(league, default=10.0, means={BARKLEY: 10.0, WARREN: 10.0})
    r = rec(league)
    assert r["expected"]["changes"] == [] and r["summary"].startswith("Keep the lineup set in Sleeper")


def test_starters_are_not_shuffled_between_slots_at_equal_value(league):
    seed_projections(league, default=7.0)  # everyone equal: RB/WR/FLEX/SUPER_FLEX could be dealt out in many ways
    assert rec(league)["expected"]["changes"] == []


def test_a_locked_starter_stays_and_a_locked_bench_player_cannot_come_in(league):
    seed_projections(league, default=10.0, means={ALLEN: 3.0, GOFF: 30.0, BARKLEY: 15.0, WARREN: 8.0})
    set_game_status(league, "BUF", 11, "in_progress")  # Allen's game started (so did Rousseau's): they cannot be benched
    set_game_status(league, "DET", 11, "final")  # Goff's game is over: he cannot be started even at 30 points
    r = rec(league)
    assert {(x["player"]["name"], x["side"], x["reason"]) for x in r["locked"]} == {
        ("Josh Allen", "starter", "game_started"), ("Greg Rousseau", "starter", "game_started"), ("Jared Goff", "bench", "game_final")}
    names = [s["player"]["name"] for s in r["expected"]["lineup"] if s["player"]]
    assert r["expected"]["lineup"][0]["player"]["name"] == "Josh Allen" and "Jared Goff" not in names
    assert "Saquon Barkley" in names  # a free bench player still replaces Warren at FLEX


def test_an_empty_slot_is_filled_even_by_a_zero_point_player(league):
    drop(league, WARREN)  # FLEX is now empty
    seed_projections(league, default=0.0, means={ALLEN: 20.0})
    r = rec(league)
    flex = r["expected"]["lineup"][6]
    assert flex["slot"] == "FLEX" and flex["player"] is not None and flex["mean"] == 0.0
    assert r["empty_slots"] == []
    assert [c["player_out"] for c in r["expected"]["changes"]] == [None]  # filling the empty slot


def test_a_slot_with_no_eligible_player_is_reported_empty(league):
    drop(league, SHRADER)  # no other K on my roster
    r = rec(league)
    assert r["empty_slots"] == ["K"] and r["expected"]["lineup"][10]["player"] is None


def test_nobody_is_started_from_an_ir_slot_and_a_player_who_cannot_be_on_ir_is_flagged(league):
    seed_projections(league, default=10.0, means={BARKLEY: 40.0})
    move(league, BARKLEY, "reserve")
    r = rec(league)
    assert BARKLEY not in [s["player"]["player_id"] for s in r["expected"]["lineup"] if s["player"]]
    (flag,) = r["ir_flags"]
    assert flag["player"]["name"] == "Saquon Barkley" and flag["player"]["fantasy_status"] == "mine_ir"
    assert "does not allow" in flag["reason"]


def test_a_starter_on_bye_is_replaced_and_the_why_says_bye(league):
    seed_projections(league, default=10.0, byes=[(WARREN, 11)], means={BARKLEY: 12.0})
    put_on_bye(league, "PIT", 11)
    r = rec(league)
    (change,) = r["expected"]["changes"]
    assert change["player_out"]["name"] == "Jaylen Warren" and change["player_out"]["game"]["status"] == "bye"
    assert "bye" in [x["code"] for x in change["why"]["reasons"]]


def test_totals_spread_and_win_probability_come_from_the_lineups(league):
    seed_projections(league, default=5.0, means={ALLEN: 30.0})
    r = rec(league)
    mine, theirs = r["current"]["mine"], r["matchup"]["opponent"]
    assert r["matchup"]["opponent_fantasy_team"] == "OrHuji" and r["matchup"]["opponent_lineup_source"] == "set_in_sleeper"
    assert mine["mean"] == pytest.approx(30 + 5 * 11) and theirs["mean"] == pytest.approx(5 * 11)  # OrHuji has an empty slot
    assert r["current"]["spread"] == pytest.approx(30.0)
    sd = math.sqrt(mine["variance"] + theirs["variance"])
    assert r["current"]["win_probability"] == pytest.approx(0.5 * (1 + math.erf(30 / sd / math.sqrt(2))), abs=0.01)


def test_the_high_risk_lineup_takes_the_wider_spread_when_it_has_the_higher_ceiling(league):
    # Nabers 11 with a wide spread against Warren 12 with a narrow one at FLEX; Barkley is out of play
    bench = dict.fromkeys(["10892", "11439", GOFF, BARKLEY], 0.0)
    seed_projections(league, default=20.0, means={WARREN: 12.0, NABERS: 11.0, **bench}, variances={WARREN: 4.0, NABERS: 200.0})
    r = rec(league)
    assert r["expected"]["changes"] == []
    (change,) = r["high_risk"]["changes"]
    assert change["player_in"]["name"] == "Malik Nabers" and change["mean_gain"] == -1.0
    assert r["high_risk"]["mine"]["mean"] < r["expected"]["mine"]["mean"]
    assert r["high_risk"]["mine"]["variance"] > r["expected"]["mine"]["variance"]
    assert "variance" in [x["code"] for x in change["why"]["reasons"]]


def test_a_missing_projection_is_a_warning_not_a_guess(league):
    seed_projections(league, default=10.0, means={WARREN: None})
    assert "no projection for Jaylen Warren" in rec(league)["warnings"]


def test_the_envelope_names_week_sources_and_unprojected_items(league):
    seed_projections(league, unprojected=[("def_st_td", 6.0)])
    r = rec(league)
    assert (r["kind"], r["season"], r["week"]) == ("lineup", 2026, 11)
    assert r["unprojected_scoring_items"] == [{"key": "def_st_td", "value": 6.0}]
    assert r["as_of"]["projections"]["source_label"] == "projections (Sleeper and nflverse blend)"
    assert r["as_of"]["games"]["source_label"] in ("ESPN scoreboard", "nflverse schedule")


def test_a_week_already_played_is_refused(league):
    with pytest.raises(lineup.NoData):
        rec(league, week=9)


def test_the_injury_label_follows_the_facts_precedence_not_only_the_catalog(league):
    league.execute("insert into nfl_injury(player_id, season, week, source, designation, as_of, fetched_at)"
                   " values (?, 2026, 11, 'nflverse', 'Doubtful', '2026-11-19T20:00:00Z', '2026-11-19T20:01:00Z')", (ALLEN,))
    seed_projections(league, default=10.0)
    allen = rec(league)["current"]["lineup"][0]["player"]
    assert allen["name"] == "Josh Allen" and allen["injury_status"] == "Doubtful"  # the catalog has none


def test_final_games_count_the_points_scored_and_games_in_progress_keep_the_projection(league):
    seed_projections(league, default=10.0)  # a 10-point projection with variance 36 for everyone
    set_game_status(league, "BUF", 11, "final")  # Allen (27.56) and Rousseau (22.0) are done
    set_game_status(league, "CIN", 11, "final")  # OrHuji's Chase scored 33.1
    set_game_status(league, "LAC", 11, "in_progress")  # OrHuji's kicker Dicker is playing
    r = rec(league)
    mine, theirs = r["current"]["mine"], r["matchup"]["opponent"]
    assert mine["mean"] == pytest.approx(27.56 + 22.0 + 10 * 10, abs=0.1) and mine["variance"] == pytest.approx(10 * 36)
    assert theirs["mean"] == pytest.approx(33.1 + 10 * 10, abs=0.1) and theirs["variance"] == pytest.approx(10 * 36)  # Dicker: projection
    assert [s["mean"] for s in r["current"]["lineup"][:1]] == [27.6] and r["current"]["lineup"][0]["variance"] == 0.0
    assert r["current"]["spread"] == pytest.approx(mine["mean"] - theirs["mean"], abs=0.1)
    assert any(w.startswith("games in progress") and "Cameron Dicker" in w for w in r["warnings"])


def test_a_decided_week_is_not_a_coin_flip(league):
    playing = {t for r in league.execute("select home, away from nfl_game where week = 11") for t in (r["home"], r["away"])}
    byes = [(r["player_id"], 11) for r in league.execute("select player_id, nfl_team from player where active = 1 and nfl_team is not null")
            if r["nfl_team"] not in playing]  # fmt: skip
    seed_projections(league, default=10.0, byes=byes)
    league.execute("update nfl_game set status = 'final' where week = 11")  # every game is over: week 8 points stand in for week 11
    r = rec(league)
    assert r["current"]["mine"]["variance"] == 0.0 and r["matchup"]["opponent"]["variance"] == 0.0
    assert r["current"]["spread"] > 0 and r["current"]["win_probability"] == 1.0


def test_ir_eligibility_is_read_only_from_the_leagues_allowed_designations(league):
    import json

    move(league, BARKLEY, "reserve")
    league.execute("update player set injury_status = 'PUP' where player_id = ?", (BARKLEY,))
    fmt = json.loads(league.execute("select format from league").fetchone()[0])
    for allowed, flagged in ((["IR", "PUP"], 0), (["IR"], 1)):
        league.execute("update league set format = ?", (json.dumps({**fmt, "ir_slots": 1, "ir_allowed_designations": allowed}),))
        assert len(rec(league)["ir_flags"]) == flagged
