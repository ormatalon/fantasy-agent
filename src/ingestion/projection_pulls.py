"""Keep every Sleeper projection pull as it happens (table projection_snapshot, migration 0003)."""

import json
import sqlite3
from datetime import UTC, datetime

from storage.db import now_iso, tx

from . import sleeper


def pull(conn: sqlite3.Connection, season: int, weeks: list[int], include_season: bool = False) -> int:
    """Fetch these weeks (and the season total, stored as week 0), store what changed. Returns rows stored."""
    stored = 0
    for week in [*weeks, *([0] if include_season else [])]:
        rows = sleeper.get_projections(season, week or None)
        latest = {
            r["player_id"]: r["stats"]
            for r in conn.execute(
                "select player_id, stats from projection_snapshot s where season = ? and week = ? and pulled_at ="
                " (select max(pulled_at) from projection_snapshot where season = s.season and week = s.week"
                " and player_id = s.player_id)", (season, week))
        }
        pulled_at = now_iso()
        with tx(conn):
            for r in rows:
                stats = {k: v for k, v in (r.get("stats") or {}).items() if v}
                text = json.dumps(stats, sort_keys=True)
                if not stats or latest.get(r["player_id"]) == text:
                    continue
                modified = r.get("last_modified")
                conn.execute(
                    "insert or ignore into projection_snapshot values (?,?,?,?,?,?)",
                    (season, week, r["player_id"], pulled_at, text,
                     datetime.fromtimestamp(modified / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if modified else None),
                )
                stored += 1
    return stored
