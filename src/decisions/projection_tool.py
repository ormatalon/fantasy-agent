"""Rows of the projection artifact for players or a position (docs/data-contract.md sections 7 and 11, tool 8)."""

import json
import sqlite3

from decisions import artifact_view
from decisions.league import NoData, View, group, r1
from projections import artifact

LABEL = {"week": "Weekly", "ros": "Rest-of-season", "full_season": "Full-season"}


def _row(rank: int, v: View, pid: str, r: dict, week: int) -> dict:
    return {
        "rank": rank, "player": v.ref(pid, week), "mean": r1(r["mean"]), "variance": r1(r["variance"]), "n_games": r["n_games"],
        "zero_reason": r["zero_reason"], "sources": json.loads(r["sources"]), "return_week": r["return_week"],
        "return_date": r["return_date"], "return_date_source": r["return_date_source"], "return_date_as_of": r["return_date_as_of"],
    }  # fmt: skip


def get_projections(conn: sqlite3.Connection, league_id: str, player_ids: list[str] | None = None, position: str | None = None,
                    horizon: str = "week", week: int | None = None, limit: int = 10) -> dict:  # fmt: skip
    """horizon: week (default; `week` picks one, default this week), ros (from this week through 18) or full_season
    (from week 1, computed now and not stored)."""
    warnings = artifact_view.ensure(conn, league_id)
    v = View(conn, league_id)
    pos = group(position.upper()) if position else None
    if horizon == "full_season":
        rows, _ = artifact.build(conn, league_id, v.season, 1, artifact_view.default_sources(conn, v.season),
                                 artifact_view.stored_weights(conn), overlay_from=v.this_week)
        found = {r["player_id"]: r for r in rows if r["horizon"] == "ros"}
        from_week = 1
    elif horizon == "week":
        from_week = week or v.week
        if from_week < v.this_week or from_week > 18:
            raise NoData(f"projections cover weeks {v.this_week} to 18; week {from_week} is not one of them")
        found = {pid: r for (pid, w), r in v.week_rows().items() if w == from_week}
    else:
        from_week = v.this_week
        found = {pid: r for pid, r in v.ros_rows().items()}
    ids = player_ids if player_ids is not None else [p for p in found if not pos or v.position(p) == pos]
    missing = [v.ref(p)["name"] for p in ids if p not in found]
    warnings += [f"no projection for {n}" for n in missing]
    ranked = sorted((p for p in ids if p in found), key=lambda p: -found[p]["mean"])[:limit]
    rows = [_row(i, v, p, found[p], v.week if horizon == "week" else from_week) for i, p in enumerate(ranked, 1)]
    scope = f"for {len(rows)} player{'s' if len(rows) != 1 else ''}" if player_ids is not None else f"top {len(rows)} {pos or 'players'}"
    summary = f"{LABEL[horizon]} projections {scope}" + (f" for week {from_week}." if horizon == "week" else f" from week {from_week}.")
    return {**v.envelope("projections", summary, warnings, from_week), "horizon": horizon, "rows": rows}
