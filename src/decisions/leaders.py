"""Season-to-date leaders: points players ACTUALLY scored so far this season,
league-wide, under this league's scoring rules.

League matchups only hold points for rostered players, so "who are the top
RBs so far" needs box scores for everyone - nflverse's weekly stats, scored
the same way the nflverse projection source scores them.
"""

import sqlite3
from dataclasses import dataclass

import pandas as pd

from src.ingestion.id_crosswalk import build_crosswalk, map_nflverse_ids
from src.ingestion.nflverse_client import import_weekly_stats
from src.projections.positions import GRANULAR_TO_GROUP, PROJECTED_POSITIONS
from src.projections.sources.nflverse_src import with_league_points


@dataclass
class Leader:
    player_id: str
    name: str
    position: str | None
    team: str | None
    injury_status: str | None
    total: float
    games: int


def season_to_date(
    conn: sqlite3.Connection,
    season: str,
    through_week: int,
    scoring_settings: dict[str, float],
    position: str = "",
    limit: int = 20,
    stats: pd.DataFrame | None = None,
) -> list[Leader]:
    """Top scorers from week 1 through `through_week`. `position` matches a
    Sleeper position or its group (e.g. 'DL' matches DE/DT)."""
    df = stats if stats is not None else import_weekly_stats([int(season)])
    df = df[(df["week"] <= through_week) & (df["position"].isin(PROJECTED_POSITIONS))]
    if df.empty:
        return []
    df = with_league_points(df, scoring_settings)
    totals = df.groupby("player_id").agg(total=("league_points", "sum"), games=("week", "nunique"))

    to_sleeper = map_nflverse_ids(build_crosswalk(conn), df)
    wanted = position.upper()
    leaders = []
    for gsis_id, row in totals.iterrows():
        sleeper_id = to_sleeper.get(gsis_id)
        if not sleeper_id:
            continue
        p = conn.execute(
            "SELECT first_name, last_name, position, team, injury_status FROM players WHERE player_id = ?",
            (sleeper_id,),
        ).fetchone()
        if not p:
            continue
        pos = p["position"] or ""
        if wanted and wanted not in (pos.upper(), (GRANULAR_TO_GROUP.get(pos, pos) or "").upper()):
            continue
        name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) or sleeper_id
        leaders.append(Leader(sleeper_id, name, p["position"], p["team"], p["injury_status"],
                              float(row["total"]), int(row["games"])))

    leaders.sort(key=lambda lead: lead.total, reverse=True)
    return leaders[:limit]
