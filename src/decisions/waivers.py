"""Waiver targets on two horizons (docs/data-contract.md section 9.2)."""

import sqlite3

from decisions import artifact_view
from decisions.league import NoData, View, r1, reason, why
from decisions.value import STARTED, free_agent_pool, replacement

FAAB_PCT_PER_GAME_VORP = 0.04  # of the league budget for each point a game over replacement level


def budget(v: View) -> dict:
    if v.fmt["waiver_type"] != "faab":
        return {"type": v.fmt["waiver_type"], "total": None, "remaining": None, "min_bid": None}
    return {"type": "faab", "total": v.fmt["faab_budget"], "remaining": v.fmt["faab_remaining"].get(str(v.me), v.fmt["faab_budget"]),
            "min_bid": v.settings.get("waiver_bid_min", 0)}  # fmt: skip


def faab_guide(b: dict, vorp: float, games: int) -> int | None:
    """Dollars to bid: points a game over replacement x 4% of the budget, inside the minimum bid and what is left."""
    if b["type"] != "faab":
        return None
    guide = round(vorp / max(games, 1) * FAAB_PCT_PER_GAME_VORP * b["total"])
    return max(b["min_bid"], min(guide, b["remaining"]))


def _horizon_list(v: View, horizon: str, position: str | None, limit: int, b: dict) -> dict:
    repl = {}
    candidates, excluded = [], []
    for pid in free_agent_pool(v, position):
        pos = v.position(pid)
        repl[pos] = repl.get(pos) or replacement(v, pos, horizon)
        row = v.ros_row(pid)
        status = v.game_status(pid) if horizon == "week" else None
        if status == "bye":  # no weekly number on a bye: judge him by his typical game
            mean, games = (row["mean"] / row["n_games"] if row["n_games"] else 0.0), 1
        else:
            mean, games = (v.mean(pid, v.week), 1) if horizon == "week" else (row["mean"], row["n_games"])
        vorp = mean - repl[pos]["points"]
        if vorp <= 0:
            continue
        if status == "bye" or status in STARTED:
            excluded.append((vorp, {"player": v.ref(pid), "reason": "bye" if status == "bye" else "game_started"}))
            continue
        reasons = [reason("replacement_level", [pid], {"vorp": r1(vorp), "replacement_points": r1(repl[pos]["points"]), "rank_n": repl[pos]["rank_n"]},
                          f"{vorp:.1f} points over the {pos} replacement level ({repl[pos]['points']:.1f}).")]  # fmt: skip
        if (inj := v.injury_status(pid)):
            reasons.append(reason("injury", [pid], {"status": inj}, f"Listed {inj}."))
        name = v.ref(pid)["name"]
        candidates.append((vorp, {"player": v.ref(pid), "mean": r1(mean), "vorp": r1(vorp),
                                  "replacement": {**repl[pos], "points": r1(repl[pos]["points"])},
                                  "faab_guide": faab_guide(b, vorp, games),
                                  "why": why(f"{name} is {vorp:.1f} over the {pos} replacement level.", *reasons)}))  # fmt: skip
    candidates = [c for _, c in sorted(candidates, key=lambda x: -x[0])[:limit]]
    return {"horizon": horizon,
            "candidates": [{"rank": i + 1, **c} for i, c in enumerate(candidates)],
            "excluded": [x for _, x in sorted(excluded, key=lambda x: -x[0])[:limit]] if horizon == "week" else []}  # fmt: skip


def get_waiver_targets(conn: sqlite3.Connection, league_id: str, horizon: str = "both", position: str | None = None,
                       limit: int = 10) -> dict:  # fmt: skip
    warnings = artifact_view.ensure(conn, league_id)
    v = View(conn, league_id)
    if v.me is None:
        raise NoData("my fantasy team is not stored for this league")
    b = budget(v)
    lists = [_horizon_list(v, h, position, limit, b) for h in (["week", "ros"] if horizon == "both" else [horizon])]
    names = {lst["horizon"]: lst["candidates"][0]["player"]["name"] if lst["candidates"] else None for lst in lists}
    labels = {"week": "this week", "ros": "rest of season"}
    parts = [f"{labels[h]}: {n}" for h, n in names.items() if n]
    summary = ("Top pickup " + "; ".join(parts) + ".") if parts else "No free agent is projected above replacement level."
    return {**v.envelope("waivers", summary, warnings), "lists": lists, "budget": b}
