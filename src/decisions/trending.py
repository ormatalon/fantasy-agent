"""Most added or dropped players across Sleeper: context only, never a projection input."""

import sqlite3

from decisions.league import NoData, View
from ingestion import sleeper
from storage.db import now_iso


def get_trending_players(conn: sqlite3.Connection, league_id: str, type: str = "add", hours: int = 24, limit: int = 10) -> dict:
    v = View(conn, league_id)
    try:
        pulled = sleeper.get_trending(type, hours, limit)
    except sleeper.SleeperError as e:
        raise NoData(f"Sleeper trending could not be reached: {e}") from e
    at = now_iso()
    known = [t for t in pulled if v.player(t["player_id"])]
    missing = [f"{t['player_id']} ({t['count']})" for t in pulled if not v.player(t["player_id"])]
    warnings = [f"{len(missing)} trending players are not in the players catalog and are left out (Sleeper id, count): " + ", ".join(missing)] if missing else []
    rows = [{"rank": i, "player": v.ref(t["player_id"]), "count": t["count"]} for i, t in enumerate(known, 1)]
    verb = "added" if type == "add" else "dropped"
    if rows:
        summary = f"Most {verb} across Sleeper in the last {hours} hours: {rows[0]['player']['name']} (context only, not a projection)."
    elif pulled:
        summary = f"Sleeper returned {len(pulled)} trending players that are not in the local player list, so none can be shown."
    else:
        summary = f"Sleeper returned no trending {type} data for the last {hours} hours."
    result = v.envelope("trending", summary, warnings)
    result["as_of"] = {"trending": {"as_of": at, "source_label": "Sleeper trending"}}
    result["unprojected_scoring_items"] = []  # trending is not a projection
    return {**result, "type": type, "hours": hours, "rows": rows}
