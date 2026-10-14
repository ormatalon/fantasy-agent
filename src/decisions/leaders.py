"""Season-to-date leaders: points players ACTUALLY scored so far this season,
league-wide, under this league's scoring rules.

League matchups only hold points for rostered players, so "who are the top
RBs so far" needs box scores for everyone - Sleeper's weekly stats, which
share the projections' stat keys and player ids, so they score the same way
with no id mapping.
"""

import sqlite3
from dataclasses import dataclass

from src.projections.positions import GRANULAR_TO_GROUP
from src.projections.scoring import score_stats
from src.projections.sources.sleeper_src import fetch_week_stats


@dataclass
class Leader:
    player_id: str
    name: str
    position: str | None
    team: str | None
    injury_status: str | None
    total: float
    games: int


def season_to_date(
    conn: sqlite3.Connection,
    season: str,
    through_week: int,
    scoring_settings: dict[str, float],
    position: str = "",
    limit: int = 20,
    weekly_stats: dict[int, list[dict]] | None = None,
) -> list[Leader]:
    """Top scorers from week 1 through `through_week`. `position` matches a
    Sleeper position or its group (e.g. 'DL' matches DE/DT). `weekly_stats`
    ({week: fetch_week_stats rows}) is fetched when not given."""
    totals: dict[str, float] = {}
    games: dict[str, int] = {}
    for week in range(1, through_week + 1):
        rows = weekly_stats[week] if weekly_stats is not None else fetch_week_stats(season, week)
        for e in rows:
            pid = e["player_id"]
            totals[pid] = totals.get(pid, 0.0) + score_stats(e["stats"], scoring_settings)
            games[pid] = games.get(pid, 0) + (1 if e["stats"].get("gp") else 0)

    wanted = position.upper()
    leaders = []
    for pid, total in totals.items():
        p = conn.execute(
            "SELECT first_name, last_name, position, team, injury_status FROM players WHERE player_id = ?",
            (pid,),
        ).fetchone()
        if not p:
            continue
        pos = p["position"] or ""
        if wanted and wanted not in (pos.upper(), (GRANULAR_TO_GROUP.get(pos, pos) or "").upper()):
            continue
        name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) or pid
        leaders.append(Leader(pid, name, p["position"], p["team"], p["injury_status"], total, games[pid]))

    leaders.sort(key=lambda lead: lead.total, reverse=True)
    return leaders[:limit]
