"""Orchestrates sources -> blend -> matchup adjustment -> injury adjustment
into one artifact: adjusted mean + variance per player, for one week.

This is the seam every decision module (Stage 3+) consumes — nothing
downstream should need to touch a projection source or adjustment directly.
"""

import json
import sqlite3
from dataclasses import dataclass

import config
from src.ingestion.id_crosswalk import build_crosswalk
from src.projections import injury, matchup
from src.projections.blend import blend
from src.projections.sources import ALL_SOURCES

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

        mm = matchup.get_multiplier(points_allowed, team_opponents, team, position) if team and position else 1.0
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
