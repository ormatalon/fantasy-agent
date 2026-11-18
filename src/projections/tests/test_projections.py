"""Projection sources, blend, scoring and the artifact, on real captured responses.

Sleeper rows: ingestion/tests/fixtures/sleeper/projections_*_sample.json (raw /projections responses).
nflverse rows: fixtures/stats_player_week_2025_sample.csv (real 2025 box scores of four QB/RB).
ESPN return date: the Jeremiyah Love entry of ingestion/tests/fixtures/espn/injuries_sample.json.
The weekly kickoff schedule after week 9 is synthetic: the real 2026 schedule is not captured.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ingestion.tests.support import FIXTURES, fixture
from projections import artifact, nflverse_data, sleeper_data
from projections.blend import DEFAULT_CV, blend
from projections.scoring import REFERENCE_PPR, score, unprojected_items
from projections.sources import NflverseSource, SleeperSource

HERE = Path(__file__).parent / "fixtures"
MAHOMES, ALLEN, GIBBS = "4046", "4984", "9221"
LEAGUE = fixture("league_or_test_league.json")
SCORING = LEAGUE["scoring_settings"]
POSITIONS = {MAHOMES: "QB", ALLEN: "QB", GIBBS: "RB"}


def captured_projections(season: int, week: int) -> list[dict]:
    """Only some weeks were captured (9 and, for 2026, 18); the others behave as weeks Sleeper has no rows for."""
    name = f"projections_{season}_week{week}_sample.json"
    return sleeper_data._trim(fixture(name)) if (FIXTURES / name).exists() else []


@pytest.fixture
def sleeper_source():
    return SleeperSource(captured_projections)


@pytest.fixture
def sleeper_2025():
    return SleeperSource(lambda season, week: sleeper_data._trim(fixture("projections_2025_week9_sample.json")))


@pytest.fixture
def nflverse_source():
    games = nflverse_data.games_from_csv((HERE / "stats_player_week_2025_sample.csv").read_bytes())
    schedule = {}
    for g in games:
        schedule.setdefault(g["week"], set()).add(g["nfl_team"])
    id_map = nflverse_data.id_map_from_csv((HERE / "roster_weekly_2025_sample.csv").read_bytes())
    return NflverseSource(games, id_map, schedule, n_games=4)


# scoring

def test_reference_scoring_reproduces_sleeper_ppr_for_a_running_back():
    gibbs = next(r for r in fixture("projections_2025_week9_sample.json") if r["player_id"] == GIBBS)
    assert score(gibbs["stats"], REFERENCE_PPR) == pytest.approx(gibbs["stats"]["pts_ppr"], abs=0.05)
    # Sleeper's own pts_ppr for a QB adds about 2 points of QB bonuses that are not stat keys, so QBs are scored by us.
    mahomes = fixture("projections_2025_week9_sample.json")[0]
    assert mahomes["stats"]["pts_ppr"] - score(mahomes["stats"], REFERENCE_PPR) > 2


def test_unprojected_items_lists_league_scoring_no_source_projects(sleeper_source):
    sleeper_source.weekly(2026, [9])
    sleeper_only = {i["key"] for i in unprojected_items(SCORING, sleeper_source.stat_keys)}
    both = {i["key"] for i in unprojected_items(SCORING, sleeper_source.stat_keys | nflverse_data.PROJECTED_KEYS)}
    assert {"def_st_td", "pts_allow_0", "idp_def_td"} <= sleeper_only  # the sample has no IDP or DEF rows
    assert {"def_st_td", "pts_allow_0"} <= both and "idp_def_td" not in both  # nflverse projects defenders' touchdowns
    assert not {"pass_yd", "rec", "fum_lost"} & sleeper_only


# sources and blend

def test_sleeper_source_returns_stats_scored_by_the_blend(sleeper_source):
    rows = sleeper_source.weekly(2026, [9])
    gibbs = next(r for r in rows if r.player_id == GIBBS)
    assert score(gibbs.stats, REFERENCE_PPR) == pytest.approx(25.86, abs=0.05)  # Sleeper's own pts_ppr for him
    assert gibbs.points_variance is None


def test_nflverse_source_means_the_last_games_before_the_week(nflverse_source):
    rows = {(r.player_id, r.week): r for r in nflverse_source.weekly(2025, [9, 10])}
    mahomes = rows[(MAHOMES, 9)]
    assert mahomes.stats["pass_yd"] == pytest.approx((318 + 257 + 286 + 299) / 4)  # weeks 5-8
    assert mahomes.points_variance > 0
    assert (MAHOMES, 10) not in rows  # KC was on bye in week 10: no box score for the NFL team that week


def test_nflverse_source_knows_only_games_before_the_origin(nflverse_source):
    early = nflverse_source.with_options(cutoff=6).weekly(2025, [9])
    assert next(r for r in early if r.player_id == MAHOMES).stats["pass_yd"] == pytest.approx((187 + 224 + 270 + 318) / 4)  # weeks 2-5


def test_blend_any_subset_of_sources_works(sleeper_2025, nflverse_source):
    rows = {"sleeper": sleeper_2025.weekly(2025, [9]), "nflverse": nflverse_source.weekly(2025, [9])}
    both = blend(rows, {"sleeper": 0.9, "nflverse": 0.1}, REFERENCE_PPR, POSITIONS)[(MAHOMES, 9)]
    only_sleeper = blend(rows, {"sleeper": 1.0}, REFERENCE_PPR, POSITIONS)[(MAHOMES, 9)]
    zero_weight = blend(rows, {"sleeper": 1.0, "nflverse": 0.0}, REFERENCE_PPR, POSITIONS)[(MAHOMES, 9)]
    only_nflverse = blend(rows, {"nflverse": 1.0}, REFERENCE_PPR, POSITIONS)[(MAHOMES, 9)]
    assert zero_weight.mean == only_sleeper.mean == pytest.approx(score(rows['sleeper'][0].stats, REFERENCE_PPR))
    assert both.mean == pytest.approx(0.9 * only_sleeper.mean + 0.1 * only_nflverse.mean)
    assert [p["source"] for p in both.sources] == ["sleeper", "nflverse"]
    assert sum(p["weight"] for p in both.sources) == pytest.approx(1.0)


def test_blend_renormalises_when_a_source_is_silent(sleeper_2025, nflverse_source):
    rows = {"sleeper": sleeper_2025.weekly(2025, [9]), "nflverse": nflverse_source.weekly(2025, [9])}
    blended = blend(rows, {"sleeper": 0.9, "nflverse": 0.1}, REFERENCE_PPR, POSITIONS)
    purdy = blended[("8183", 9)]  # Sleeper projects him, nflverse has no week 9 row (SF is absent from the sample schedule)
    assert [(p["source"], p["weight"]) for p in purdy.sources] == [("sleeper", 1.0)]


def test_blend_variance_is_source_variance_plus_between_source_spread(sleeper_2025, nflverse_source):
    b = blend({"sleeper": sleeper_2025.weekly(2025, [9]), "nflverse": nflverse_source.weekly(2025, [9])},
              {"sleeper": 0.5, "nflverse": 0.5}, REFERENCE_PPR, POSITIONS)[(MAHOMES, 9)]
    sleeper, nfl = b.sources
    floor = (DEFAULT_CV["QB"] * sleeper["mean"]) ** 2  # Sleeper has no variance of its own: the position floor
    assert sleeper["variance"] == pytest.approx(floor)
    between = 0.5 * (sleeper["mean"] - b.mean) ** 2 + 0.5 * (nfl["mean"] - b.mean) ** 2
    assert b.variance == pytest.approx(0.5 * sleeper["variance"] + 0.5 * nfl["variance"] + between)


# injury overlay and rest of season

def kickoffs(first_week=9, last_week=18, skip=()):
    start = date(2026, 11, 8)  # week 9 Sunday; later weeks are weekly Sundays (synthetic)
    return {w: f"{start + timedelta(7 * (w - 9))}T18:00:00Z" for w in range(first_week, last_week + 1) if w not in skip}


def weeks_of(mean=10.0, variance=25.0, games=None):
    games = games or kickoffs()
    return {w: artifact.Blended("p", w, mean, variance, [{"source": "sleeper", "weight": 1.0, "mean": mean, "variance": variance}])
            for w in games}


def by_week(rows):
    return {r["week"]: r for r in rows}


def test_healthy_player_keeps_every_projection():
    rows = artifact.player_weeks(weeks_of(), kickoffs(), None, 9)
    assert [r["mean"] for r in rows] == [10.0] * 10 and all(r["zero_reason"] is None for r in rows)


def test_ruled_out_player_is_zero_for_the_next_game_only():
    for status, reason in (("Out", "out"), ("PUP", "pup"), ("Sus", "suspended")):
        rows = by_week(artifact.player_weeks(weeks_of(), kickoffs(), {"designation": status}, 9))
        assert (rows[9]["mean"], rows[9]["zero_reason"], rows[9]["healthy"]) == (0.0, reason, 10.0)
        assert rows[10]["mean"] == 10.0 and rows[10]["zero_reason"] is None


def test_questionable_and_doubtful_keep_their_projection():
    for status in ("Questionable", "Doubtful"):
        rows = artifact.player_weeks(weeks_of(), kickoffs(), {"designation": status}, 9)
        assert all(r["mean"] == 10.0 for r in rows)


def test_ruled_out_after_a_bye_zeroes_the_first_game_not_the_bye_week():
    games = kickoffs(skip=(9,))
    rows = by_week(artifact.player_weeks(weeks_of(games=games), games, {"designation": "Out"}, 9))
    assert rows[9]["zero_reason"] == "bye" and rows[10]["zero_reason"] == "out" and rows[11]["mean"] == 10.0


def test_ir_player_is_zero_until_the_return_week_then_projected():
    injury = {"designation": "IR", "return_date": "2026-12-06"}  # Love's ESPN return date (a Sunday)
    rows = by_week(artifact.player_weeks(weeks_of(), kickoffs(), injury, 9))
    assert [rows[w]["zero_reason"] for w in (9, 10, 11, 12)] == ["ir"] * 4
    assert artifact.return_week("2026-12-06", kickoffs(), 9) == 13  # kickoff on the return date counts
    assert rows[13]["mean"] == 10.0 and rows[18]["mean"] == 10.0
    assert rows[9]["healthy"] == 10.0 and rows[13]["healthy"] == 10.0


def test_ir_return_date_after_week_18_projects_nothing():
    rows = artifact.player_weeks(weeks_of(), kickoffs(), {"designation": "IR", "return_date": "2027-01-20"}, 9)
    assert all(r["mean"] == 0.0 and r["zero_reason"] == "ir" for r in rows)


def test_ir_without_a_return_date_keeps_the_rows_it_is_given():
    rows = artifact.player_weeks(weeks_of(), kickoffs(), {"designation": "IR", "return_date": None}, 9)
    assert all(r["mean"] == 10.0 and r["zero_reason"] is None and r["healthy"] == 10.0 for r in rows)


def test_sleeper_only_takes_sleepers_part_of_a_blended_row():
    both = artifact.Blended("p", 9, 11.0, 30.0, [{"source": "sleeper", "weight": 0.9, "mean": 10.0, "variance": 25.0},
                                                {"source": "nflverse", "weight": 0.1, "mean": 20.0, "variance": 40.0}])
    own = artifact.sleeper_only(both)
    assert (own.mean, own.variance) == (10.0, 25.0) and own.sources[0]["weight"] == 1.0
    assert artifact.sleeper_only(artifact.Blended("p", 9, 20.0, 40.0, [both.sources[1]])) is None


def test_bye_week_is_a_zero_row_that_counts_no_game():
    games = kickoffs(skip=(12,))
    rows = by_week(artifact.player_weeks(weeks_of(games=games), games, None, 9))
    assert (rows[12]["mean"], rows[12]["zero_reason"], rows[12]["games"]) == (0.0, "bye", False)


def test_rest_of_season_row_sums_the_weeks_and_carries_the_return_fields():
    injury = {"designation": "IR", "return_date": "2026-12-06", "return_date_source": "espn", "return_date_as_of": "2026-11-03T18:57Z"}
    games = kickoffs(skip=(15,))
    rows = artifact.player_rows("L", 2026, "p", artifact.player_weeks(weeks_of(games=games), games, injury, 9), injury, games, "t")
    ros = next(r for r in rows if r["horizon"] == "ros")
    assert ros["mean"] == pytest.approx(10.0 * 5)  # weeks 13, 14, 16, 17, 18 (15 is a bye)
    assert ros["variance"] == pytest.approx(25.0 * 5) and ros["n_games"] == 9  # weeks 9-18 minus the bye
    assert ros["mean_if_healthy"] == pytest.approx(10.0 * 9) and ros["zero_reason"] == "ir"
    assert (ros["return_week"], ros["return_date"], ros["return_date_source"]) == (13, "2026-12-06", "espn")
    week9 = next(r for r in rows if r["horizon"] == "week" and r["week"] == 9)
    assert week9["return_week"] == 13 and json.loads(week9["sources"]) == []


def test_a_healthy_ros_row_has_no_zero_reason_and_no_healthy_mean():
    rows = artifact.player_rows("L", 2026, "p", artifact.player_weeks(weeks_of(), kickoffs(), None, 9), None, kickoffs(), "t")
    ros = rows[-1]
    assert ros["horizon"] == "ros" and ros["zero_reason"] is None and ros["mean_if_healthy"] is None
    assert json.loads(ros["sources"])[0]["mean"] == pytest.approx(100.0)


# database build

def seed(conn):
    from storage.db import set_settings

    set_settings(conn, {"nfl_state": {"season": 2026, "week": 9}})
    conn.execute(
        "insert into league(league_id, season, name, status, settings, scoring_settings, roster_positions, format, fetched_at)"
        " values ('L', 2026, 'Or Test League', 'in_season', '{}', ?, '[]', '{}', '2026-11-17T00:00:00Z')", (json.dumps(SCORING),))
    for pid, name, pos, team, status in ((GIBBS, "Jahmyr Gibbs", "RB", "DET", None), (MAHOMES, "Patrick Mahomes", "QB", "KC", "IR"),
                                         (ALLEN, "Josh Allen", "QB", "BUF", "IR")):
        conn.execute("insert into player(player_id, full_name, position, nfl_team, active, injury_status, fetched_at)"
                     " values (?,?,?,?,1,?,'2026-11-17T00:00:00Z')", (pid, name, pos, team, status))
    for team in ("DET", "KC", "BUF"):
        for w, k in kickoffs().items():
            conn.execute("insert into nfl_game(game_id, season, week, home, away, kickoff_utc, status, fetched_at)"
                         " values (?, 2026, ?, ?, 'XXX', ?, 'scheduled', '2026-11-17T00:00:00Z')", (f"{w}_{team}", w, team, k))
    conn.execute("insert into nfl_injury(player_id, season, week, source, designation, return_date, as_of, fetched_at)"
                 " values (?, 2026, 9, 'espn', 'IR', '2026-12-06', '2026-11-03T18:57:00Z', '2026-11-17T00:00:00Z')", (MAHOMES,))


def test_build_and_save_the_artifact(conn, sleeper_source):
    seed(conn)
    rows, meta = artifact.build(conn, "L", 2026, 9, [sleeper_source], {"sleeper": 1.0}, now="2026-11-17T00:00:00Z")
    artifact.save(conn, rows, meta)
    stored = conn.execute("select * from projection where player_id = ? and horizon = 'week' and week in (9, 18) order by week",
                          (MAHOMES,)).fetchall()
    assert [(r["mean"] > 0, r["zero_reason"]) for r in stored] == [(False, "ir"), (True, None)]  # captured weeks: 9 and 18
    assert stored[0]["return_date_source"] == "espn" and stored[0]["return_week"] == 13
    gibbs = conn.execute("select mean, zero_reason from projection where player_id = ? and horizon = 'ros'", (GIBBS,)).fetchone()
    assert gibbs["zero_reason"] is None and gibbs["mean"] > 0
    allen = conn.execute("select mean, zero_reason, sources from projection where player_id = ? and horizon = 'week' and week = 9", (ALLEN,)).fetchone()
    assert allen["mean"] > 0 and allen["zero_reason"] is None  # IR, no return date: Sleeper's own row, kept
    assert [p["source"] for p in json.loads(allen["sources"])] == ["sleeper"]
    unprojected = {i["key"] for i in json.loads(conn.execute("select unprojected_scoring_items from projection_meta").fetchone()[0])}
    assert "def_st_td" in unprojected and "pass_yd" not in unprojected
    assert artifact.ir_return_date_coverage(conn) == {
        "ir_players": 2, "with_return_date": 1, "without_return_date": ["Josh Allen"]}


# history across seasons

def two_seasons(prior_weight=1.0, teams=None):
    games = [*nflverse_data.games_from_csv((HERE / "stats_player_week_2024_sample.csv").read_bytes()),
             *nflverse_data.games_from_csv((HERE / "stats_player_week_2025_sample.csv").read_bytes())]  # fmt: skip
    schedule = {}
    for g in games:
        if g["season"] == 2025:
            schedule.setdefault(g["week"], set()).add(g["nfl_team"])
    id_map = nflverse_data.id_map_from_csv((HERE / "roster_weekly_2025_sample.csv").read_bytes())
    return NflverseSource(games, id_map, schedule, n_games=4, prior_weight=prior_weight, teams=teams)


def test_week_one_uses_last_seasons_games():
    rows = {r.player_id: r for r in two_seasons().weekly(2025, [1])}
    assert rows[MAHOMES].stats["pass_yd"] == pytest.approx((210 + 159 + 260 + 320) / 4)  # 2024 weeks 14-17


def test_prior_season_games_can_count_less():
    row = next(r for r in two_seasons(prior_weight=0.5).weekly(2025, [3]) if r.player_id == MAHOMES)
    assert row.stats["pass_yd"] == pytest.approx((0.5 * 260 + 0.5 * 320 + 258 + 187) / 3)  # 2024 weeks 16-17, 2025 weeks 1-2


def test_cutoff_never_looks_at_games_from_the_target_week_on():
    row = next(r for r in two_seasons().with_options(cutoff=2).weekly(2025, [9]) if r.player_id == MAHOMES)
    assert row.stats["pass_yd"] == pytest.approx((159 + 260 + 320 + 258) / 4)  # up to 2025 week 1 only


def test_a_player_on_a_new_nfl_team_is_projected_only_when_that_team_plays():
    moved = {MAHOMES: {1: "XXX"}}  # the roster file puts him on an NFL team with no game that week
    assert MAHOMES not in {r.player_id for r in two_seasons(teams=moved).weekly(2025, [1])}
    moved = {MAHOMES: {1: "BUF"}}
    assert MAHOMES in {r.player_id for r in two_seasons(teams=moved).weekly(2025, [1])}


def test_an_ir_players_healthy_value_ignores_the_source_that_projects_him_at_zero():
    # Sleeper lists IR players with empty rows (Achane, Hurts on 2026-11-17); nflverse still has his games
    sources = [{"source": "sleeper", "weight": 0.9, "mean": 0.0, "variance": 0.0},
               {"source": "nflverse", "weight": 0.1, "mean": 12.0, "variance": 30.0}]  # fmt: skip
    games = kickoffs()
    blended = {w: artifact.Blended("p", w, 1.2, 3.0, sources) for w in games}
    dated = by_week(artifact.player_weeks(blended, games, {"designation": "IR", "return_date": "2026-12-06"}, 9))
    assert (dated[9]["mean"], dated[9]["healthy"]) == (0.0, 12.0)
    assert dated[13]["mean"] == 12.0 and [s["source"] for s in dated[13]["sources"]] == ["nflverse"]
    undated = by_week(artifact.player_weeks(blended, games, {"designation": "IR", "return_date": None}, 9))
    assert (undated[9]["mean"], undated[9]["healthy"]) == (0.0, 12.0)  # Sleeper's own row (0), healthy kept for the stash


def test_the_ros_row_of_an_ir_player_with_no_return_date_keeps_his_healthy_value():
    # Sleeper lists him at 0, nflverse at 12 a game: his weekly rows are Sleeper's 0, his healthy value is 12 a game
    sources = [{"source": "sleeper", "weight": 0.9, "mean": 0.0, "variance": 0.0},
               {"source": "nflverse", "weight": 0.1, "mean": 12.0, "variance": 30.0}]  # fmt: skip
    games = kickoffs(skip=(12,))
    injury = {"designation": "IR", "return_date": None}
    blended = {w: artifact.Blended("p", w, 1.2, 3.0, sources) for w in games}
    rows = artifact.player_rows("L", 2026, "p", artifact.player_weeks(blended, games, injury, 9), injury, games, "t")
    week, ros = by_week([r for r in rows if r["horizon"] == "week"]), rows[-1]
    assert week[9]["mean_if_healthy"] == 12.0 and ros["mean"] == 0.0 and ros["zero_reason"] is None
    assert ros["mean_if_healthy"] == pytest.approx(12.0 * 9)  # 9 games, the bye adds nothing


def test_a_full_season_overlay_starts_at_this_week_not_week_one():
    games = kickoffs(first_week=1)
    blended = weeks_of(games=games)
    out = by_week(artifact.player_weeks(blended, games, {"designation": "Out"}, 1, overlay_from=9))
    assert out[1]["mean"] == 10.0 and out[8]["mean"] == 10.0 and out[9]["zero_reason"] == "out" and out[10]["mean"] == 10.0
    ir = by_week(artifact.player_weeks(blended, games, {"designation": "IR", "return_date": "2026-12-06"}, 1, overlay_from=9))
    assert [ir[w]["zero_reason"] for w in (1, 8)] == [None, None] and ir[1]["mean"] == 10.0  # games he already played
    assert [ir[w]["zero_reason"] for w in (9, 12)] == ["ir", "ir"] and ir[13]["zero_reason"] is None
