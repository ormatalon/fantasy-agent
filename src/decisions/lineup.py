"""Constrained start/sit optimizer: assigns a roster to starting slots to
maximize total projected points, respecting position eligibility.

A thin consumer of Stage 1's projection engine — all it does is solve the
assignment problem; the math (projections) lives upstream.
"""

from dataclasses import dataclass

import pulp

from src.projections.engine import AdjustedProjection
from src.projections.positions import ROSTER_SLOT_ELIGIBILITY

NON_STARTING_SLOTS = {"BN", "IR", "TAXI"}


@dataclass
class LineupSlot:
    slot: str
    player_id: str | None
    name: str
    position: str | None
    mean: float
    why: str


def _explain(proj: AdjustedProjection | None) -> str:
    if proj is None:
        return "no current projection available"
    parts = [f"{proj.mean:.1f} projected pts"]
    if proj.num_sources > 1:
        parts.append(f"blended from {proj.num_sources} sources")
    else:
        parts.append("single source")
    if proj.injury_multiplier != 1.0:
        parts.append(f"injury adjustment x{proj.injury_multiplier:.2f}")
    if proj.matchup_multiplier != 1.0:
        parts.append(f"matchup adjustment x{proj.matchup_multiplier:.2f}")
    return ", ".join(parts)


def optimize_lineup(
    roster_positions: list[str],
    roster_player_ids: list[str],
    projections: dict[str, AdjustedProjection],
    player_meta: dict[str, dict],
) -> list[LineupSlot]:
    """`player_meta` maps player_id -> {"name": str, "position": str}."""
    starting_slots = [s for s in roster_positions if s not in NON_STARTING_SLOTS]
    slot_indices = list(enumerate(starting_slots))

    prob = pulp.LpProblem("lineup", pulp.LpMaximize)

    x: dict[tuple[str, int], pulp.LpVariable] = {}
    for pid in roster_player_ids:
        meta = player_meta.get(pid)
        if not meta:
            continue
        pos = meta["position"]
        for idx, slot in slot_indices:
            eligible = ROSTER_SLOT_ELIGIBILITY.get(slot, set())
            if pos in eligible:
                x[(pid, idx)] = pulp.LpVariable(f"x_{pid}_{idx}", cat="Binary")

    proj_points = {pid: (projections[pid].mean if pid in projections else 0.0) for pid in roster_player_ids}

    prob += pulp.lpSum(proj_points[pid] * var for (pid, _idx), var in x.items())

    for idx, _slot in slot_indices:
        vars_for_slot = [var for (_pid, i), var in x.items() if i == idx]
        if vars_for_slot:
            prob += pulp.lpSum(vars_for_slot) <= 1

    for pid in roster_player_ids:
        vars_for_player = [var for (p, _i), var in x.items() if p == pid]
        if vars_for_player:
            prob += pulp.lpSum(vars_for_player) <= 1

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    assignment: dict[int, str] = {}
    for (pid, idx), var in x.items():
        if var.value() == 1:
            assignment[idx] = pid

    result = []
    for idx, slot in slot_indices:
        pid = assignment.get(idx)
        if pid:
            meta = player_meta[pid]
            proj = projections.get(pid)
            mean = proj.mean if proj else 0.0
            result.append(
                LineupSlot(slot=slot, player_id=pid, name=meta["name"], position=meta["position"], mean=mean, why=_explain(proj))
            )
        else:
            result.append(LineupSlot(slot=slot, player_id=None, name="(empty)", position=None, mean=0.0, why="no eligible player available"))
    return result
