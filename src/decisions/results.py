"""What actually happened: per-player scored points for a completed week,
and how much of it was left sitting on the bench.

The "best possible lineup" is computed by handing the Stage 3 lineup
optimizer actual scored points instead of projections — same constrained
assignment problem, hindsight inputs. The gap between that and what was
actually started is the cost of the start/sit decisions.
"""

import json
import sqlite3
from dataclasses import dataclass

from src.decisions.lineup import LineupSlot, optimize_lineup
from src.projections.engine import AdjustedProjection


@dataclass
class WeekResult:
    week: int
    actual_total: float
    optimal_total: float
    points_left_on_bench: float
    started: list[tuple[str, float]]
    bench: list[tuple[str, float]]
    optimal_lineup: list[LineupSlot]


def _as_projection(player_id: str, name: str, position: str | None, points: float) -> AdjustedProjection:
    """Wrap an actual score in the projection shape so the optimizer can take it."""
    return AdjustedProjection(
        player_id=player_id, name=name, position=position, team=None,
        mean=points, variance=0.0, matchup_multiplier=1.0, injury_multiplier=1.0, num_sources=1,
    )


def summarize_week(
    conn: sqlite3.Connection, league_id: str, roster_id: int, week: int, roster_positions: list[str]
) -> WeekResult | None:
    row = conn.execute(
        "SELECT points, starters, players, players_points FROM matchups "
        "WHERE league_id = ? AND week = ? AND roster_id = ?",
        (league_id, week, roster_id),
    ).fetchone()
    if not row:
        return None

    players_points: dict[str, float] = json.loads(row["players_points"] or "{}")
    if not players_points:
        return None

    starters = json.loads(row["starters"] or "[]")
    all_players = json.loads(row["players"] or "[]")

    meta: dict[str, dict] = {}
    for pid in all_players:
        p = conn.execute(
            "SELECT first_name, last_name, position FROM players WHERE player_id = ?", (pid,)
        ).fetchone()
        name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) if p else pid
        meta[pid] = {"name": name or pid, "position": p["position"] if p else None}

    scored = {
        pid: _as_projection(pid, meta[pid]["name"], meta[pid]["position"], players_points.get(pid, 0.0))
        for pid in all_players
        if pid in meta
    }

    optimal = optimize_lineup(roster_positions, all_players, scored, meta)
    optimal_total = sum(s.mean for s in optimal)

    actual_total = row["points"] if row["points"] is not None else sum(
        players_points.get(pid, 0.0) for pid in starters
    )

    return WeekResult(
        week=week,
        actual_total=float(actual_total),
        optimal_total=optimal_total,
        points_left_on_bench=max(0.0, optimal_total - float(actual_total)),
        started=[(meta[p]["name"], players_points.get(p, 0.0)) for p in starters if p in meta],
        bench=sorted(
            [(meta[p]["name"], players_points.get(p, 0.0)) for p in all_players if p not in starters and p in meta],
            key=lambda t: t[1],
            reverse=True,
        ),
        optimal_lineup=optimal,
    )
