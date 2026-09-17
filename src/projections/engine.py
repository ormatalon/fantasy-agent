"""Orchestrates sources -> blend -> matchup adjustment -> injury adjustment
into one artifact: adjusted mean + variance per player.

`build_projection_table` produces the weekly artifact;
`build_season_projection_table` the full-season one. Both are consumed by
the decision modules (Stage 3+) — nothing downstream should need to touch a
projection source or adjustment directly.
"""

import json
import sqlite3
from dataclasses import dataclass

import config
from src.ingestion.id_crosswalk import build_crosswalk
from src.projections import injury, matchup
from src.projections.blend import blend
from src.projections.sources import ALL_SOURCES
from src.projections.sources.sleeper_src import fetch_season_projections

# Variance floor (as a std-dev) when only one source projected a player, so
# "no measurable disagreement" doesn't get misread as "no uncertainty".
POSITION_STD_FLOOR: dict[str, float] = {
    "QB": 5.0, "RB": 4.0, "WR": 4.0, "TE": 3.0, "K": 2.0, "DEF": 3.0,
}
STD_FLOOR_DEFAULT = 3.0
STD_FLOOR_FRACTION = 0.25  # coefficient-of-variation floor: at least 25% of the mean


@dataclass
class AdjustedProjection:
    player_id: str
    name: str
    position: str | None
    team: str | None
    mean: float
    variance: float
    matchup_multiplier: float
    injury_multiplier: float
    num_sources: int


def _variance_floor(mean: float, position: str | None) -> float:
    std_floor = max(STD_FLOOR_FRACTION * mean, POSITION_STD_FLOOR.get(position, STD_FLOOR_DEFAULT))
    return std_floor**2


def build_projection_table(conn: sqlite3.Connection, season: str, week: int) -> list[AdjustedProjection]:
    league_row = conn.execute("SELECT scoring_settings FROM leagues LIMIT 1").fetchone()
    if not league_row:
        raise RuntimeError("No league synced yet — run `sync` first.")
    scoring_settings = json.loads(league_row["scoring_settings"])

    crosswalk = build_crosswalk(conn)

    projections_by_source = {}
    for source in ALL_SOURCES:
        weight = config.SOURCE_WEIGHTS.get(source.name, 0)
        if weight <= 0:
            continue
        projections_by_source[source.name] = source.fetch(season, week, scoring_settings, crosswalk)

    blended = blend(projections_by_source, config.SOURCE_WEIGHTS)

    points_allowed, team_opponents = {}, {}
    if config.MATCHUP_ADJUSTMENT_ENABLED:
        points_allowed = matchup.compute_points_allowed_multipliers(season, week, config.MATCHUP_TRAILING_WEEKS)
        team_opponents = matchup.get_team_opponents(season, week)
    backup_bumps = injury.compute_backup_bumps(conn)

    results = []
    for player_id, bp in blended.items():
        player = conn.execute(
            "SELECT first_name, last_name, position, team, injury_status FROM players WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if not player:
            continue

        name = " ".join(p for p in [player["first_name"], player["last_name"]] if p) or player_id
        position, team = player["position"], player["team"]

        mm = 1.0
        if config.MATCHUP_ADJUSTMENT_ENABLED and team and position:
            mm = matchup.get_multiplier(points_allowed, team_opponents, team, position)
        im = backup_bumps.get(player_id) or injury.get_designation_multiplier(player["injury_status"])

        total_multiplier = mm * im
        mean = bp.mean * total_multiplier
        variance = (bp.variance if bp.variance is not None else _variance_floor(bp.mean, position)) * total_multiplier**2

        results.append(
            AdjustedProjection(
                player_id=player_id,
                name=name,
                position=position,
                team=team,
                mean=mean,
                variance=variance,
                matchup_multiplier=mm,
                injury_multiplier=im,
                num_sources=bp.num_sources,
            )
        )

    results.sort(key=lambda r: r.mean, reverse=True)
    return results


def build_season_projection_table(conn: sqlite3.Connection, season: str) -> list[AdjustedProjection]:
    """Full-season projected totals per player, same artifact shape as the
    weekly table so decision modules can consume either.

    Two deliberate differences from the weekly build:
    - **No matchup adjustment.** It's a per-opponent multiplier; there is no
      single opponent across a season.
    - **Only season-long injury designations apply.** Discounting a season
      total because someone is Questionable *this week* would be wrong; an
      IR/PUP designation plainly should count. See injury.SEASON_LONG_DESIGNATIONS.

    Currently single-source (Sleeper) — nflverse is historical box scores and
    has no forward-looking season projection — so `num_sources` is 1 and the
    variance floor carries the uncertainty estimate.
    """
    league_row = conn.execute("SELECT scoring_settings FROM leagues LIMIT 1").fetchone()
    if not league_row:
        raise RuntimeError("No league synced yet - run `sync` first.")
    scoring_settings = json.loads(league_row["scoring_settings"])

    projections_by_source = {"sleeper": fetch_season_projections(season, scoring_settings)}
    blended = blend(projections_by_source, {"sleeper": 1.0})

    results = []
    for player_id, bp in blended.items():
        player = conn.execute(
            "SELECT first_name, last_name, position, team, injury_status FROM players WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if not player:
            continue

        name = " ".join(p for p in [player["first_name"], player["last_name"]] if p) or player_id
        position, team = player["position"], player["team"]
        im = injury.get_season_designation_multiplier(player["injury_status"])

        variance = bp.variance if bp.variance is not None else _variance_floor(bp.mean, position)

        results.append(
            AdjustedProjection(
                player_id=player_id,
                name=name,
                position=position,
                team=team,
                mean=bp.mean * im,
                variance=variance * im**2,
                matchup_multiplier=1.0,
                injury_multiplier=im,
                num_sources=bp.num_sources,
            )
        )

    results.sort(key=lambda r: r.mean, reverse=True)
    return results
