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


@dataclass
class SleeperLineup:
    """A roster as it is set in Sleeper right now (not as it should be)."""

    starters: list[tuple[str, str | None]]  # (slot, player_id); None = empty slot
    bench: list[str]
    ir: list[str]


def sleeper_lineup(players: list[str], starters: list[str], reserve: list[str], roster_positions: list[str]) -> SleeperLineup:
    """Sleeper's `starters` lists one player per starting slot, in the order
    of the league's `roster_positions` (bench/IR/taxi slots excluded); "0"
    marks an empty slot. `players` includes starters and IR players."""
    slots = [s for s in roster_positions if s not in NON_STARTING_SLOTS]
    # A starters list longer than the slots (settings changed mid-season)
    # still shows every starter rather than silently dropping one.
    slots += ["?"] * (len(starters) - len(slots))
    started = [(slot, pid if pid and pid != "0" else None) for slot, pid in zip(slots, starters)]
    started += [(slot, None) for slot in slots[len(starters):]]
    placed = set(starters) | set(reserve)
    return SleeperLineup(
        starters=started,
        bench=[pid for pid in players if pid not in placed],
        ir=[pid for pid in reserve if pid],
    )


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
    current_starters: list[str] | None = None,
    locked: set[str] = frozenset(),
) -> list[LineupSlot]:
    """`player_meta` maps player_id -> {"name": str, "position": str}.

    `current_starters` is the lineup set in Sleeper (one id per starting
    slot, in order). Given it, ties go to keeping each player where he is,
    so swapping two RBs between the RB slots never shows up as a change.
    `locked` players (game already started) can't move: a locked starter
    stays in his slot and a locked bench player can't come in."""
    starting_slots = [s for s in roster_positions if s not in NON_STARTING_SLOTS]
    slot_indices = list(enumerate(starting_slots))
    current = current_starters or []
    fixed = {idx: current[idx] for idx, _slot in slot_indices if idx < len(current) and current[idx] in locked}

    prob = pulp.LpProblem("lineup", pulp.LpMaximize)

    x: dict[tuple[str, int], pulp.LpVariable] = {}
    for pid in roster_player_ids:
        meta = player_meta.get(pid)
        if not meta or (pid in locked and pid not in fixed.values()):
            continue
        pos = meta["position"]
        for idx, slot in slot_indices:
            if idx in fixed:
                eligible_here = fixed[idx] == pid
            else:
                eligible_here = pid not in fixed.values() and pos in ROSTER_SLOT_ELIGIBILITY.get(slot, set())
            if eligible_here:
                x[(pid, idx)] = pulp.LpVariable(f"x_{pid}_{idx}", cat="Binary")
    for idx, pid in fixed.items():
        if (pid, idx) in x:
            prob += x[(pid, idx)] == 1

    proj_points = {pid: (projections[pid].mean if pid in projections else 0.0) for pid in roster_player_ids}

    # The tiny per-assignment bonus breaks ties toward *filling* a slot. Without
    # it, a player projected at 0.0 contributes nothing, so the solver is
    # indifferent between starting him and leaving the slot empty - which
    # reports "(empty)" even when an eligible player exists. Small enough never
    # to outrank a real points difference.
    FILL_BONUS = 1e-6
    # Same idea, one notch up: between equal lineups, prefer the one already
    # set, so only real changes are proposed.
    KEEP_BONUS = 1e-4

    def bonus(pid: str, idx: int) -> float:
        return FILL_BONUS + (KEEP_BONUS if idx < len(current) and current[idx] == pid else 0.0)

    prob += pulp.lpSum((proj_points[pid] + bonus(pid, idx)) * var for (pid, idx), var in x.items())

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
