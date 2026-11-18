"""Lineup recommendation (docs/data-contract.md section 9.1).

Starts from the lineup set in Sleeper. Locks both ways, no empty slot while an eligible player exists, no
player from an IR slot, and no change that gains less than KEEP.
"""

import math
import sqlite3

from decisions import artifact_view
from decisions.assign import fill_slots
from decisions.league import NoData, View, r1, reason, why, win_probability

K_SD = 1.0  # high-risk lineup: mean + K_SD * standard deviation per player
STARTED = {"in_progress": "game_started", "complete": "game_final"}


def _mv(view: View, pid: str, week: int, final: dict[str, float]) -> tuple[float, float]:
    """(mean, variance) of a starter: the points he scored when his game is final, else the projection."""
    return (final[pid], 0.0) if pid in final else (view.mean(pid, week), view.variance(pid, week))


def _raw(view: View, lineup: list[str | None], week: int, final: dict[str, float]) -> tuple[float, float]:
    """Total and variance of a lineup (players taken as independent)."""
    parts = [_mv(view, p, week, final) for p in lineup if p]
    return sum(m for m, _ in parts), sum(v for _, v in parts)


def _slot(view: View, slot: str, pid: str | None, week: int, final: dict[str, float]) -> dict:
    mean, var = _mv(view, pid, week, final) if pid else (None, None)
    return {"slot": slot, "player": view.ref(pid, week) if pid else None, "mean": r1(mean), "variance": r1(var)}


def _change_why(view: View, week: int, out: str | None, into: str, high_risk: bool) -> dict:
    m_in, m_out = view.mean(into, week), view.mean(out, week) if out else 0.0
    n_in, n_out = view.ref(into, week)["name"], view.ref(out, week)["name"] if out else "the empty slot"
    reasons = [reason("projection_edge", [into, *([out] if out else [])],
                      {"in_mean": r1(m_in), "out_mean": r1(m_out), "gain": r1(m_in - m_out)},
                      f"{n_in} projects {m_in:.1f}, {n_out} {m_out:.1f}.")]  # fmt: skip
    if out and view.game_status(out, week) == "bye":
        reasons.append(reason("bye", [out], {}, f"{n_out} is on bye."))
    row = view.week_row(out, week) if out else None
    if row and row["zero_reason"] in ("out", "pup", "suspended", "ir"):
        reasons.append(reason("injury", [out], {"zero_reason": row["zero_reason"]}, f"{n_out} is projected 0 ({row['zero_reason']})."))
    if high_risk:
        sd_in, sd_out = math.sqrt(view.variance(into, week)), math.sqrt(view.variance(out, week)) if out else 0.0
        reasons.append(reason("variance", [into, *([out] if out else [])], {"in_sd": r1(sd_in), "out_sd": r1(sd_out)},
                              f"{n_in} has a standard deviation of {sd_in:.1f}, {n_out} {sd_out:.1f}."))  # fmt: skip
    summary = f"{n_in} projects {m_in - m_out:.1f} more points than {n_out} at {{slot}}." if m_in >= m_out else \
        f"{n_in} projects {m_out - m_in:.1f} fewer points than {n_out} at {{slot}} but has more upside."
    return why(summary, *reasons)


def recommend_lineup(conn: sqlite3.Connection, league_id: str, week: int | None = None) -> dict:
    warnings = artifact_view.ensure(conn, league_id)
    v = View(conn, league_id, week)
    if v.me is None:
        raise NoData("my fantasy team is not stored for this league")
    week = v.week
    if week < v.this_week or week > 18:
        raise NoData(f"projections cover weeks {v.this_week} to 18; week {week} is not one of them")
    current = [v.rosters[v.me]["starter"].get(i) for i in range(len(v.slots))]
    candidates = v.active(v.me)

    locked_slots, locked = set(), []
    for i, pid in enumerate(current):
        if pid and not v.position(pid):
            locked_slots.add(i)  # not in the players catalog: cannot be placed, so he stays
        elif pid and v.game_status(pid, week) in STARTED:
            locked_slots.add(i)
            locked.append({"player": v.ref(pid, week), "side": "starter", "reason": STARTED[v.game_status(pid, week)]})
    for pid in candidates:
        if pid not in current and v.game_status(pid, week) in STARTED:
            locked.append({"player": v.ref(pid, week), "side": "bench", "reason": STARTED[v.game_status(pid, week)]})
    movable = [p for p in candidates if v.game_status(p, week) not in STARTED and v.position(p)]
    positions = {p: v.position(p) for p in movable}

    opponent_id = v.opponent_id(week)
    opp_lineup = [v.rosters[opponent_id]["starter"].get(i) for i in range(len(v.slots))] if opponent_id else []
    # Games already played count what was scored; games in progress keep the pre-game projection (no per-minute
    # remaining projection exists); games not started keep the projection.
    final, in_progress = {}, []
    scored = {r["player_id"]: r["points"] for r in conn.execute(
        "select player_id, points from matchup_player where league_id = ? and week = ? and roster_id in (?, ?)",
        (league_id, week, v.me, opponent_id or v.me))}
    for pid in [*current, *opp_lineup]:
        status = v.game_status(pid, week) if pid else None
        if status == "complete" and pid in scored:
            final[pid] = scored[pid] or 0.0
        elif status == "in_progress":
            in_progress.append(v.ref(pid, week)["name"])
    if in_progress:
        warnings.append("games in progress, so their pre-game projections are still used: " + ", ".join(dict.fromkeys(in_progress)))
    opp = _raw(v, opp_lineup, week, final) if opponent_id else None

    def plan(mode: str, lineup: list[str | None]) -> dict:
        mean, var = _raw(v, lineup, week, final)
        out = {"mode": mode, "lineup": [_slot(v, s, p, week, final) for s, p in zip(v.slots, lineup)], "changes": [],
               "mine": {"mean": r1(mean), "variance": r1(var)}}  # fmt: skip
        if opp:
            out["spread"] = r1(mean - opp[0])
            out["win_probability"] = round(win_probability(mean - opp[0], var + opp[1]), 2)
        else:
            out["spread"] = out["win_probability"] = None
        # Moves against the lineup set in Sleeper: who comes in, who goes out (best incoming against the weakest
        # outgoing), so each pair's gain is real and the gains add up to the plan's. Starters who only change
        # slots are listed apart.
        before, after = {p for p in current if p}, {p for p in lineup if p}
        incoming = sorted(after - before, key=lambda p: -v.mean(p, week))
        outgoing = sorted(before - after, key=lambda p: v.mean(p, week))
        for i, into in enumerate(incoming):
            was = outgoing[i] if i < len(outgoing) else None
            slot = v.slots[lineup.index(into)]
            change = {"slot": slot, "player_out": v.ref(was, week) if was else None, "player_in": v.ref(into, week),
                      "mean_gain": r1(v.mean(into, week) - (v.mean(was, week) if was else 0.0))}  # fmt: skip
            change["why"] = _change_why(v, week, was, into, mode == "high_risk")
            change["why"]["summary"] = change["why"]["summary"].format(slot=slot)
            out["changes"].append(change)
        out["slot_changes"] = [
            {"player": v.ref(p, week), "from_slot": v.slots[current.index(p)], "to_slot": v.slots[lineup.index(p)]}
            for p in current if p in after and v.slots[current.index(p)] != v.slots[lineup.index(p)]
        ]  # fmt: skip
        return out

    def solve(score) -> list[str | None]:
        return fill_slots(v.slots, current, positions, {p: score(p) for p in movable}, locked_slots)

    expected = solve(lambda p: v.mean(p, week))
    risky = solve(lambda p: v.mean(p, week) + K_SD * math.sqrt(v.variance(p, week)))
    plans = {"current": plan("expected", current), "expected": plan("expected", expected), "high_risk": plan("high_risk", risky)}
    plans["current"]["why"] = why("Your lineup as set in Sleeper.")

    ex = plans["expected"]
    gain = r1(ex["mine"]["mean"] - plans["current"]["mine"]["mean"])
    ex["why"] = why("No change raises the projected total." if not ex["changes"]
                    else f"{len(ex['changes'])} change{'s' if len(ex['changes']) > 1 else ''} raise the projected total by {gain:.1f}.")
    hr = plans["high_risk"]
    if opp:
        underdog = plans["current"]["win_probability"] < 0.5
        hr_why = [reason("underdog" if underdog else "variance", [], {"win_probability": plans["current"]["win_probability"], "spread": plans["current"]["spread"]},
                         f"Win probability with your lineup as set is {plans['current']['win_probability']:.2f}.")]  # fmt: skip
    else:
        hr_why = []
    hr["why"] = why("Same lineup as the expected-points one; no higher-variance option helps." if risky == expected else
                    f"Higher-variance lineup: projected {hr['mine']['mean']:.1f}, variance {hr['mine']['variance']:.1f}, "
                    f"win probability {hr['win_probability']}.", *hr_why)  # fmt: skip

    # flags
    ir_flags = []
    for pid in v.rosters[v.me]["reserve"]:
        status = v.injury_status(pid)
        if status not in v.fmt["ir_allowed_designations"]:
            ir_flags.append({"player": v.ref(pid, week), "reason": f"IR slot but designation is {status or 'none'}, which this league does not allow on IR"})
    empty = [s for s, p in zip(v.slots, expected) if p is None]
    for pid in dict.fromkeys(p for p in [*current, *expected] if p and not v.week_row(p, week)):
        warnings.append(f"no projection for {v.ref(pid, week)['name']}")

    moves = [f"{c['player_in']['name']} over {c['player_out']['name'] if c['player_out'] else 'the empty slot'} at {c['slot']}" for c in ex["changes"]]
    shifts = [f"{c['player']['name']} from {c['from_slot']} to {c['to_slot']}" for c in ex["slot_changes"]]
    summary = "Start " + "; start ".join(moves) if moves else "Keep the lineup set in Sleeper"
    summary += "".join(f"; move {s}" for s in shifts)
    summary += f"; the expected-points lineup projects {ex['mine']['mean']:.1f}" + (f" to {opp[0]:.1f}." if opp else ".")
    result = v.envelope("lineup", summary, warnings, week)
    matchup = {"opponent_fantasy_team": v.names[opponent_id], "opponent_fantasy_team_id": opponent_id,
               "opponent_lineup_source": "set_in_sleeper", "opponent": {"mean": r1(opp[0]), "variance": r1(opp[1])}} if opp else None  # fmt: skip
    if not opp:
        result["warnings"].append(f"no opponent stored for week {week} (bye or not yet scheduled)")
    return {**result, **plans, "matchup": matchup, "locked": locked, "ir_flags": ir_flags, "empty_slots": empty}
