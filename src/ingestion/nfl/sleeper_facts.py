"""NFL facts that come from Sleeper: player news and weekly stats (targets, carries).

Calls go through ingestion.sleeper, the only module that talks to Sleeper.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from storage.db import now_iso, tx

from .. import sleeper
from ..sync import _log, stored_age

NEWS_MAX_AGE = timedelta(minutes=30)


def refresh_news(conn: sqlite3.Connection, player_id: str) -> bool:
    """Pull the player's latest news unless pulled in the last 30 minutes. False when Sleeper failed."""
    age = stored_age(conn, "sleeper", f"news:{player_id}")
    if age is not None and age < NEWS_MAX_AGE:
        return True
    try:
        items = sleeper.get_player_news(player_id)
    except sleeper.SleeperError as e:
        _log(conn, "sleeper", f"news:{player_id}", str(e))
        return False
    ts = now_iso()
    with tx(conn):
        for n in items:
            m = n.get("metadata") or {}
            conn.execute(
                "insert or replace into news values (?,?,?,?,?,?,?,?,?)",
                (n["source"], n["source_key"], n["player_id"],
                 datetime.fromtimestamp(n["published"] / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 m.get("title"), m.get("description"), m.get("analysis"), m.get("url"), ts),
            )
    _log(conn, "sleeper", f"news:{player_id}", None)
    return True


def refresh_stats(conn: sqlite3.Connection, season: int, weeks: list[int]) -> None:
    """Actual stats per player and week; targets and carries also go in nfl_usage (source sleeper)."""
    ts = now_iso()
    for week in weeks:
        rows = sleeper.get_stats(season, week)
        with tx(conn):
            conn.execute("delete from nfl_usage where source = 'sleeper' and season = ? and week = ?", (season, week))
            for r in rows:
                stats = r.get("stats") or {}
                if not stats:
                    continue
                conn.execute("insert or replace into player_week_stat values (?,?,?,?,?)",
                             (season, week, r["player_id"], json.dumps(stats), ts))
                if "rec_tgt" in stats or "rush_att" in stats:
                    conn.execute(
                        "insert into nfl_usage values (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["player_id"], season, week, "sleeper", None, None, None,
                         int(stats.get("rec_tgt", 0)), int(stats.get("rush_att", 0)), None, ts, ts),
                    )
