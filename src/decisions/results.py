"""What actually happened: per-player scored points for a completed week,
and how much of it was left sitting on the bench.

The "best possible lineup" is computed by handing the Stage 3 lineup
optimizer actual scored points instead of projections — same constrained
assignment problem, hindsight inputs. The gap between that and what was
actually started is the cost of the start/sit decisions.
"""

import json
import sqlite3
from dataclasses import dataclass, field

from src.decisions.lineup import LineupSlot, optimize_lineup
from src.ingestion import storage
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
    opponent_name: str | None = None
    opponent_total: float | None = None
    opponent_top: list[tuple[str, float]] = field(default_factory=list)

    @property
    def outcome(self) -> str | None:
        """'win' / 'loss' / 'tie' against this week's opponent, None on a bye."""
        if self.opponent_total is None:
            return None
        if self.actual_total > self.opponent_total:
            return "win"
        return "loss" if self.actual_total < self.opponent_total else "tie"


def last_completed_week(current_week: int) -> int:
    """Sleeper's `week` flips to the next week once the previous one's games
    are done, so the week before the current one is the last finished one."""
    return max(1, current_week - 1)


def _name(conn: sqlite3.Connection, player_id: str) -> str:
    p = conn.execute("SELECT first_name, last_name FROM players WHERE player_id = ?", (player_id,)).fetchone()
    return (" ".join(x for x in [p["first_name"], p["last_name"]] if x) if p else "") or player_id


def _opponent(conn: sqlite3.Connection, league_id: str, roster_id: int, week: int):
    opp = storage.get_opponent_roster(conn, league_id, week, roster_id)
    if not opp:
        return None, None, []
    roster = storage.get_roster(conn, league_id, opp["roster_id"])
    name = storage.fantasy_team_label(conn, league_id, roster["owner_id"]) if roster else f"roster {opp['roster_id']}"
    points = json.loads(opp["players_points"] or "{}")
    starters = json.loads(opp["starters"] or "[]")
    top = sorted(((_name(conn, pid), points.get(pid, 0.0)) for pid in starters), key=lambda t: t[1], reverse=True)
    total = opp["points"] if opp["points"] is not None else sum(points.get(pid, 0.0) for pid in starters)
    return name, float(total), top[:3]


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

    opponent_name, opponent_total, opponent_top = _opponent(conn, league_id, roster_id, week)

    return WeekResult(
        opponent_name=opponent_name,
        opponent_total=opponent_total,
        opponent_top=opponent_top,
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
