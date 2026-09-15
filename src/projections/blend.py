"""Weighted-average blend across all registered projection sources.

Never hardcodes which sources exist — takes whatever this run produced
(some sources may return nothing for week 1, or for a given player) and
tolerates any subset being active, renormalizing weights over the sources
that actually had data for each player.
"""

from collections import defaultdict
from dataclasses import dataclass

from src.projections.sources.base import SourceProjection


@dataclass
class BlendedProjection:
    player_id: str
    mean: float
    variance: float | None  # None when only one source contributed — no spread to measure
    num_sources: int


def blend(
    projections_by_source: dict[str, list[SourceProjection]],
    weights: dict[str, float],
) -> dict[str, BlendedProjection]:
    by_player: dict[str, list[tuple[float, float]]] = defaultdict(list)  # player_id -> [(points, weight)]

    for source_name, projections in projections_by_source.items():
        w = weights.get(source_name, 0)
        if w <= 0:
            continue
        for p in projections:
            by_player[p.player_id].append((p.points, w))

    result: dict[str, BlendedProjection] = {}
    for player_id, pairs in by_player.items():
        total_w = sum(w for _, w in pairs)
        mean = sum(pts * w for pts, w in pairs) / total_w

        variance = None
        if len(pairs) > 1:
            variance = sum(w * (pts - mean) ** 2 for pts, w in pairs) / total_w

        result[player_id] = BlendedProjection(
            player_id=player_id, mean=mean, variance=variance, num_sources=len(pairs)
        )
    return result
