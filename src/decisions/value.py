"""Value over replacement level: shared by waivers and the injured stash (PRD 4.4)."""

from decisions.league import ACCEPTS, View

STARTED = ("in_progress", "complete")


def slot_demand(slots: list[str]) -> dict[str, float]:
    """Starting slots each position fills: a dedicated slot counts 1, a flex slot is split over its positions."""
    demand: dict[str, float] = {}
    for slot in slots:
        for pos in ACCEPTS[slot]:
            demand[pos] = demand.get(pos, 0.0) + 1 / len(ACCEPTS[slot])
    return demand


def replacement(view: View, position: str, horizon: str) -> dict:
    """Level of the Nth-best rostered player at the position, N = fantasy teams x slot demand (positions with no
    slot demand have none). Rostered players projected 0 are not counted: they would pull the level to 0."""
    n = max(1, round(view.fmt["num_fantasy_teams"] * slot_demand(view.slots)[position]))
    means = sorted(
        (m for pid in view.held if view.position(pid) == position and view.held[pid][1] in ("starter", "bench")
         and (m := view.horizon_mean(pid, horizon)) > 0),
        reverse=True,
    )
    points = means[n - 1] if len(means) >= n else (means[-1] if means else 0.0)
    return {"pool": position, "rank_n": n, "points": points}


def free_agent_pool(view: View, position: str | None) -> list[str]:
    """Free agents at a position with slot demand (all such positions when none is given), with a projection."""
    demand = slot_demand(view.slots)
    return [
        pid for pid in view.free_agents()
        if (pos := view.position(pid)) in demand and (position is None or pos == position) and view.ros_row(pid)
    ]
