"""Blend source rows into one mean and variance per player-week (docs/data-contract.md section 6)."""

from collections import defaultdict
from dataclasses import dataclass

from projections.scoring import REFERENCE_PPR, score
from projections.sources import SourceRow

# Chosen on the 2024 backtest only (uv run python -m evaluation.backtest --sweep, --fit-cv); 2025 is out of sample.
DEFAULT_WEIGHTS = {"sleeper": 0.9, "nflverse": 0.1}
# Forecast error as a share of the projected mean (RMS), per position, fitted by evaluation.backtest.
DEFAULT_CV = {"QB": 0.44, "RB": 0.6, "WR": 0.69, "TE": 0.75, "default": 0.61}


@dataclass
class Blended:
    player_id: str
    week: int
    mean: float
    variance: float
    sources: list[dict]  # [{"source", "weight", "mean", "variance"}], weights renormalised


def _league_variance(row: SourceRow, points: float, floor_cv: float) -> float:
    """Source variance in league points: its own (reference PPR points, rescaled) or the position floor."""
    if row.points_variance is None:
        return (floor_cv * points) ** 2
    ref = score(row.stats, REFERENCE_PPR)
    ratio = points / ref if ref > 0 else 1.0
    return row.points_variance * ratio**2


def blend(
    rows_by_source: dict[str, list[SourceRow]],
    weights: dict[str, float],
    scoring: dict[str, float],
    positions: dict[str, str],
    cv: dict[str, float] = DEFAULT_CV,
) -> dict[tuple[str, int], Blended]:
    """Any subset of sources works: a source with weight 0, no rows or no row for a player-week is dropped
    for that player-week and the remaining weights are renormalised."""
    by_key = defaultdict(list)
    for name, rows in rows_by_source.items():
        for row in rows:
            if weights.get(name, 0) > 0:
                by_key[(row.player_id, row.week)].append((name, row))
    out = {}
    for (player_id, week), items in by_key.items():
        total = sum(weights[name] for name, _ in items)
        floor_cv = cv.get(positions.get(player_id, ""), cv["default"])
        parts = []
        for name, row in items:
            points = score(row.stats, scoring)
            parts.append(
                {"source": name, "weight": weights[name] / total, "mean": points,
                 "variance": _league_variance(row, points, floor_cv)}
            )  # fmt: skip
        m = sum(p["weight"] * p["mean"] for p in parts)
        var = sum(p["weight"] * (p["variance"] + (p["mean"] - m) ** 2) for p in parts)
        out[(player_id, week)] = Blended(player_id, week, m, var, parts)
    return out
