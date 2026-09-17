"""Sleeper's player projections, scored under the league's own rules.

Endpoints are public but live outside /v1 (undocumented, confirmed working
for past and upcoming seasons):
  weekly: /projections/nfl/<season>/<week>
  season: /projections/nfl/<season>          (no week segment)

Both return the same stat-key namespace (`rush_yd`, `rec`, `pass_td`, ...),
so `score_stats` applies the league's own scoring to either unchanged.
"""

import httpx

from src.ingestion.id_crosswalk import Crosswalk
from src.projections.positions import PROJECTED_POSITIONS
from src.projections.scoring import score_stats
from src.projections.sources.base import ProjectionSource, SourceProjection

PROJECTIONS_BASE = "https://api.sleeper.app/projections/nfl"
# What we ask the API for — it expects the broad IDP categories (DL/LB/DB),
# not the granular positions it actually returns under them (e.g. CB, S).
QUERY_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB"]
# What we keep — the API is known to leak extra noise positions (FB, P, ...)
# beyond what was requested, so filter client-side against the full granular
# allowlist rather than trusting the query alone.
ACCEPT_POSITIONS = set(PROJECTED_POSITIONS) | {"DEF"}


def _get(url: str, timeout: float = 30.0) -> list[dict]:
    params = [("season_type", "regular")] + [("position[]", p) for p in QUERY_POSITIONS]
    resp = httpx.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json() or []


def _to_projections(entries: list[dict], scoring_settings: dict[str, float]) -> list[SourceProjection]:
    out = []
    for entry in entries:
        stats = entry.get("stats") or {}
        if not stats:
            continue
        player = entry.get("player") or {}
        if player.get("position") not in ACCEPT_POSITIONS:
            continue
        out.append(
            SourceProjection(player_id=entry["player_id"], points=score_stats(stats, scoring_settings))
        )
    return out


class SleeperSource(ProjectionSource):
    name = "sleeper"

    def fetch(
        self, season: str, week: int, scoring_settings: dict[str, float], crosswalk: Crosswalk
    ) -> list[SourceProjection]:
        return _to_projections(_get(f"{PROJECTIONS_BASE}/{season}/{week}", timeout=15.0), scoring_settings)


def fetch_season_projections(season: str, scoring_settings: dict[str, float]) -> list[SourceProjection]:
    """Full-season projected totals, scored under the league's rules.

    Not a `ProjectionSource` — that interface is weekly by contract (its
    `fetch` takes a week). Kept as a plain function rather than contorting
    the interface to carry a "no week" case.
    """
    return _to_projections(_get(f"{PROJECTIONS_BASE}/{season}"), scoring_settings)
