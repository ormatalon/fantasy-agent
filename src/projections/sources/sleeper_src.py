"""Sleeper's weekly player projections, scored under the league's own rules.

Endpoint is public but lives outside /v1 (undocumented, confirmed working
for past and upcoming seasons): /projections/nfl/<season>/<week>
"""

import httpx

from src.ingestion.id_crosswalk import Crosswalk
from src.projections.positions import SKILL_POSITIONS
from src.projections.scoring import score_stats
from src.projections.sources.base import ProjectionSource, SourceProjection

PROJECTIONS_BASE = "https://api.sleeper.app/projections/nfl"
POSITIONS = SKILL_POSITIONS + ["DEF"]


class SleeperSource(ProjectionSource):
    name = "sleeper"

    def fetch(
        self, season: str, week: int, scoring_settings: dict[str, float], crosswalk: Crosswalk
    ) -> list[SourceProjection]:
        params = [("season_type", "regular")] + [("position[]", p) for p in POSITIONS]
        resp = httpx.get(f"{PROJECTIONS_BASE}/{season}/{week}", params=params, timeout=15.0)
        resp.raise_for_status()
        entries = resp.json() or []

        out = []
        for entry in entries:
            stats = entry.get("stats") or {}
            if not stats:
                continue
            player = entry.get("player") or {}
            if player.get("position") not in POSITIONS:
                continue
            points = score_stats(stats, scoring_settings)
            out.append(SourceProjection(player_id=entry["player_id"], points=points))
        return out
