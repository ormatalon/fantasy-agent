"""Matchup adjustment: scale a player's projection by how generous their
upcoming opponent's defense has been to that position, relative to league
average.

Both points-allowed-by-position and the schedule (who plays whom) come from
nflverse via nfl_data_py — no external source needed for this module.
"""

import sys

import nfl_data_py as nfl

NEUTRAL_MULTIPLIER = 1.0
# Guardrails so one small sample (bye-week-shortened window, etc.) can't
# blow up a projection.
MIN_MULTIPLIER = 0.6
MAX_MULTIPLIER = 1.6


def compute_points_allowed_multipliers(
    season: str, week: int, trailing_weeks: int
) -> dict[tuple[str, str], float]:
    """Returns {(defense_team, offense_position): multiplier vs league average}."""
    season_int, week_int = int(season), int(week)
    try:
        df = nfl.import_weekly_data(years=[season_int])
    except Exception as e:
        print(f"matchup: no weekly data available for {season_int} yet ({e}); using neutral multipliers.", file=sys.stderr)
        return {}
    df = df[(df["week"] < week_int) & (df["week"] >= max(1, week_int - trailing_weeks))]
    if df.empty:
        return {}

    allowed = df.groupby(["opponent_team", "position"])["fantasy_points_ppr"].mean()
    league_avg = df.groupby("position")["fantasy_points_ppr"].mean()

    multipliers: dict[tuple[str, str], float] = {}
    for (team, position), value in allowed.items():
        avg = league_avg.get(position)
        if not avg:
            continue
        multiplier = value / avg
        multipliers[(team, position)] = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))
    return multipliers


def get_team_opponents(season: str, week: int) -> dict[str, str]:
    """Returns {team: opponent_team} for every team playing in that week."""
    try:
        schedules = nfl.import_schedules(years=[int(season)])
    except Exception as e:
        print(f"matchup: no schedule available for {season} yet ({e}); using neutral multipliers.", file=sys.stderr)
        return {}
    games = schedules[schedules["week"] == int(week)]

    opponents: dict[str, str] = {}
    for _, game in games.iterrows():
        opponents[game["home_team"]] = game["away_team"]
        opponents[game["away_team"]] = game["home_team"]
    return opponents


def get_multiplier(
    multipliers: dict[tuple[str, str], float], team_opponents: dict[str, str], team: str, position: str
) -> float:
    opponent = team_opponents.get(team)
    if not opponent:
        return NEUTRAL_MULTIPLIER
    return multipliers.get((opponent, position), NEUTRAL_MULTIPLIER)
