"""Value-over-replacement baseline: shared by waivers and the draft assistant.

Replacement level per position is the projection of the Nth-best rostered
player at that position, where N = num_teams * demand_share and
demand_share credits a position-specific starting slot fully (1.0) and
splits a flex-type slot evenly across the positions eligible for it (e.g.
FLEX = RB/WR/TE each get 1/3 credit per FLEX slot). This is a standard,
if approximate, way to estimate "replacement level" without needing a
full auction-value model.
"""

from src.projections.engine import AdjustedProjection
from src.projections.positions import GRANULAR_TO_GROUP, ROSTER_SLOT_ELIGIBILITY

NON_STARTING_SLOTS = {"BN", "IR", "TAXI"}


def compute_replacement_levels(
    roster_positions: list[str], num_teams: int, projections_by_position: dict[str, list[float]]
) -> dict[str, float]:
    """`projections_by_position` is keyed by granular position (e.g. "CB");
    internally pooled into replacement-value groups (e.g. "DB") via
    GRANULAR_TO_GROUP before computing baselines — see positions.py.
    """
    starting_slots = [s for s in roster_positions if s not in NON_STARTING_SLOTS]

    demand: dict[str, float] = {}
    for slot in starting_slots:
        eligible = ROSTER_SLOT_ELIGIBILITY.get(slot, set())
        if not eligible:
            continue
        groups = {GRANULAR_TO_GROUP.get(pos, pos) for pos in eligible}
        share = 1.0 / len(groups)
        for group in groups:
            demand[group] = demand.get(group, 0.0) + share

    pooled: dict[str, list[float]] = {}
    for pos, values in projections_by_position.items():
        group = GRANULAR_TO_GROUP.get(pos, pos)
        pooled.setdefault(group, []).extend(values)

    levels: dict[str, float] = {}
    for group, per_team_demand in demand.items():
        rank = max(1, round(per_team_demand * num_teams))
        values = sorted(pooled.get(group, []), reverse=True)
        if not values:
            levels[group] = 0.0
        elif rank <= len(values):
            levels[group] = values[rank - 1]
        else:
            levels[group] = values[-1]
    return levels


def rank_by_vorp(
    candidates: list[AdjustedProjection], replacement_levels: dict[str, float]
) -> list[tuple[AdjustedProjection, float]]:
    """Players whose position group has no roster-slot demand in this
    league (e.g. team DEF in an IDP league with no DEF slot) are excluded,
    not scored against a 0.0 baseline — that would make their whole
    projection look like "value" for a position nobody can actually start.
    """
    ranked = []
    for proj in candidates:
        group = GRANULAR_TO_GROUP.get(proj.position, proj.position)
        if group in replacement_levels:
            ranked.append((proj, proj.mean - replacement_levels[group]))
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked
