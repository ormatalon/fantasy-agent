"""Backtests projections against actual scored points for past weeks.

Ground truth AND matchup context (each player's team/opponent/position at
the time) both come from nflverse's own weekly box scores for that
historical week — so there's no look-ahead: only data strictly before the
target week feeds the predictions being tested.

Three tiers are compared, from naive to fully adjusted, to prove each layer
earns its keep rather than assuming it does:
  - sleeper_only: a single raw source, unblended, unadjusted (the baseline
    a user gets with no system at all).
  - blend: the weighted multi-source blend, no matchup adjustment.
  - blend_matchup: blend + the matchup adjustment.

The injury/depth-chart overlay is NOT included here: our players table
only holds *current* injury_status/depth_chart state, so applying it to a
past week would leak information that wasn't available at the time. It's
verified separately via unit tests + a live spot-check (see PLAN.md Stage 1
DoD) until a historical injury feed exists.
"""

import json
import sqlite3

import config
from src.evaluation.metrics import AccuracyReport, compute_metrics
from src.ingestion.id_crosswalk import build_crosswalk
from src.ingestion.nflverse_client import import_weekly_stats
from src.projections import matchup
from src.projections.blend import blend
from src.projections.positions import SKILL_POSITIONS
from src.projections.sources.nflverse_src import NflverseSource
from src.projections.sources.sleeper_src import SleeperSource


def _actual_points_for_week(season: str, week: int, scoring_settings: dict[str, float]):
    df = import_weekly_stats(years=[int(season)])
    df = df[(df["week"] == int(week)) & (df["position"].isin(SKILL_POSITIONS))]
    rec_bonus = scoring_settings.get("rec", 0)
    df = df.copy()
    df["actual_points"] = df["fantasy_points"].fillna(0) + df["receptions"].fillna(0) * rec_bonus
    return df


def run_backtest(conn: sqlite3.Connection, season: str, weeks: list[int]) -> dict[str, AccuracyReport]:
    league_row = conn.execute("SELECT scoring_settings FROM leagues LIMIT 1").fetchone()
    if not league_row:
        raise RuntimeError("No league synced yet - run `sync` first.")
    scoring_settings = json.loads(league_row["scoring_settings"])
    crosswalk = build_crosswalk(conn)

    errors: dict[str, list[float]] = {"sleeper_only": [], "blend": [], "blend_matchup": []}

    for week in weeks:
        if week <= 1:
            continue  # nflverse source + matchup both need trailing history

        actual_df = _actual_points_for_week(season, week, scoring_settings)
        if actual_df.empty:
            continue

        sleeper_proj = SleeperSource().fetch(season, week, scoring_settings, crosswalk)
        nflverse_proj = NflverseSource().fetch(season, week, scoring_settings, crosswalk)
        blended = blend({"sleeper": sleeper_proj, "nflverse": nflverse_proj}, config.SOURCE_WEIGHTS)
        points_allowed = matchup.compute_points_allowed_multipliers(season, week, config.MATCHUP_TRAILING_WEEKS)
        # Point-in-time team/opponent for this week, straight from the same
        # actual box scores used as ground truth (no separate schedule call).
        team_opponents = dict(zip(actual_df["recent_team"], actual_df["opponent_team"]))

        sleeper_by_id = {p.player_id: p.points for p in sleeper_proj}

        for _, row in actual_df.iterrows():
            sleeper_id = crosswalk.from_gsis(row["player_id"])
            if not sleeper_id:
                continue
            actual_points = row["actual_points"]

            if sleeper_id in sleeper_by_id:
                errors["sleeper_only"].append(sleeper_by_id[sleeper_id] - actual_points)

            bp = blended.get(sleeper_id)
            if not bp:
                continue
            errors["blend"].append(bp.mean - actual_points)

            mm = matchup.get_multiplier(points_allowed, team_opponents, row["recent_team"], row["position"])
            errors["blend_matchup"].append(bp.mean * mm - actual_points)

    return {tier: compute_metrics(e) for tier, e in errors.items()}
