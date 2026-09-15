"""nflverse-derived projections: a trailing recent-performance average.

Not a "true" projection service — a simple, transparent baseline built from
actual recent box scores via nflverse, useful as one blended input.
Requires current-season history, so it contributes nothing in week 1 (the
blend tolerates a source returning no data for a player/week).
"""

import sys

from src.ingestion.id_crosswalk import Crosswalk
from src.ingestion.nflverse_client import import_weekly_stats
from src.projections.positions import SKILL_POSITIONS
from src.projections.sources.base import ProjectionSource, SourceProjection

TRAILING_WEEKS = 4


class NflverseSource(ProjectionSource):
    name = "nflverse"

    def fetch(
        self, season: str, week: int, scoring_settings: dict[str, float], crosswalk: Crosswalk
    ) -> list[SourceProjection]:
        season_int, week_int = int(season), int(week)
        if week_int <= 1:
            return []

        try:
            df = import_weekly_stats(years=[season_int])
        except Exception as e:
            print(f"nflverse: no weekly data available for {season_int} yet ({e}); skipping.", file=sys.stderr)
            return []

        df = df[
            (df["week"] < week_int)
            & (df["week"] >= max(1, week_int - TRAILING_WEEKS))
            & (df["position"].isin(SKILL_POSITIONS))
        ]
        if df.empty:
            return []

        rec_bonus = scoring_settings.get("rec", 0)
        df = df.copy()
        df["league_points"] = df["fantasy_points"].fillna(0) + df["receptions"].fillna(0) * rec_bonus

        out = []
        for gsis_id, points in df.groupby("player_id")["league_points"].mean().items():
            sleeper_id = crosswalk.from_gsis(gsis_id)
            if sleeper_id:
                out.append(SourceProjection(player_id=sleeper_id, points=float(points)))
        return out
