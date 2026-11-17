"""Stats to points under a league's scoring (docs/data-contract.md section 7)."""

# Sleeper's default full-PPR scoring. Backtest ground truth for QB/RB/WR/TE (the crosswalk stays out of it).
REFERENCE_PPR = {
    "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -1.0, "pass_2pt": 2.0,
    "rush_yd": 0.1, "rush_td": 6.0, "rush_2pt": 2.0,
    "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rec_2pt": 2.0,
    "fum_lost": -2.0, "st_td": 6.0, "fum_rec_td": 6.0,
}  # fmt: skip


def score(stats: dict[str, float], scoring: dict[str, float]) -> float:
    return sum(v * scoring.get(k, 0.0) for k, v in stats.items())


def unprojected_items(scoring: dict[str, float], projected_keys: set[str]) -> list[dict]:
    """League scoring items with a non-zero value that no active source projects."""
    return [{"key": k, "value": v} for k, v in sorted(scoring.items()) if v and k not in projected_keys]
