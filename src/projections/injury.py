"""Injury/news overlay: discount players by designation, bump the backup
when a starter is ruled out, using Sleeper's own injury_status and
depth_chart_position/depth_chart_order fields (no separate news feed yet).
"""

import sqlite3

# Multiplier applied to a player's own projection based on designation.
# Values match Sleeper's actual injury_status strings (confirmed against a
# live pull of the players catalog): Out, PUP, NA, DNR, Sus, COV, IR,
# Questionable. "Doubtful" is a real Sleeper designation too, just not seen
# in that particular snapshot.
DESIGNATION_DISCOUNT: dict[str, float] = {
    "Questionable": 0.90,
    "Doubtful": 0.50,
    "DNR": 0.75,
    "Out": 0.0,
    "IR": 0.0,
    "PUP": 0.0,
    "Sus": 0.0,
    "COV": 0.0,
    "NA": 0.0,
}

# When a starter (depth_chart_order == 1) is fully out, bump the next player
# at the same position/team by this much.
BACKUP_BUMP = 1.20
RULED_OUT_DESIGNATIONS = {"Out", "IR", "PUP", "Sus", "COV"}

# Designations that keep a player out for an extended stretch rather than a
# single game. Only these should touch a SEASON-long projection: a player
# listed Questionable this week has no business losing 10% of his season
# outlook, but one on IR plainly does.
SEASON_LONG_DESIGNATIONS = {"IR", "PUP", "Sus", "NA"}


def get_season_designation_multiplier(injury_status: str | None) -> float:
    """Injury multiplier appropriate to a season-long projection (see
    SEASON_LONG_DESIGNATIONS). Week-specific tags are ignored here."""
    if injury_status in SEASON_LONG_DESIGNATIONS:
        return DESIGNATION_DISCOUNT.get(injury_status, 1.0)
    return 1.0


def get_designation_multiplier(injury_status: str | None) -> float:
    if not injury_status:
        return 1.0
    return DESIGNATION_DISCOUNT.get(injury_status, 1.0)


def compute_backup_bumps(conn: sqlite3.Connection) -> dict[str, float]:
    """Returns {player_id: multiplier} for backups stepping in behind a
    ruled-out starter at the same team + depth_chart_position."""
    rows = conn.execute(
        "SELECT player_id, team, depth_chart_position, depth_chart_order, injury_status "
        "FROM players WHERE depth_chart_position IS NOT NULL AND depth_chart_order IS NOT NULL"
    ).fetchall()

    by_slot: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in rows:
        by_slot.setdefault((r["team"], r["depth_chart_position"]), []).append(r)

    bumps: dict[str, float] = {}
    for slot_players in by_slot.values():
        slot_players.sort(key=lambda r: r["depth_chart_order"])
        starter = slot_players[0]
        if starter["injury_status"] in RULED_OUT_DESIGNATIONS and len(slot_players) > 1:
            backup = slot_players[1]
            bumps[backup["player_id"]] = BACKUP_BUMP
    return bumps
