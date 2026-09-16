"""Waiver/FAAB recommendations: rank available (non-rostered) players by
value-over-replacement, size a suggested FAAB bid from that VOR.
"""

import json
import sqlite3
from dataclasses import dataclass

from src.decisions.vorp import compute_replacement_levels, rank_by_vorp
from src.projections.engine import AdjustedProjection
from src.projections.positions import GRANULAR_TO_GROUP

# Rough guide only, not a bidding strategy: each point of VOR maps to this
# many percentage points of a 100-unit FAAB budget, capped at 100.
FAAB_PCT_PER_VOR_POINT = 4


@dataclass
class WaiverSuggestion:
    proj: AdjustedProjection
    vor: float
    bid_pct: int
    why: str


def get_rostered_player_ids(conn: sqlite3.Connection, league_id: str) -> set[str]:
    rows = conn.execute("SELECT players FROM rosters WHERE league_id = ?", (league_id,)).fetchall()
    rostered: set[str] = set()
    for row in rows:
        rostered.update(json.loads(row["players"]))
    return rostered


def _rank_available(
    conn: sqlite3.Connection, league_id: str, roster_positions: list[str], table: list[AdjustedProjection]
) -> tuple[list[tuple[AdjustedProjection, float]], dict[str, float]]:
    num_teams_row = conn.execute("SELECT COUNT(*) AS n FROM rosters WHERE league_id = ?", (league_id,)).fetchone()
    num_teams = num_teams_row["n"] or 1

    rostered_ids = get_rostered_player_ids(conn, league_id)

    projections_by_position: dict[str, list[float]] = {}
    for proj in table:
        if proj.player_id in rostered_ids and proj.position:
            projections_by_position.setdefault(proj.position, []).append(proj.mean)

    replacement_levels = compute_replacement_levels(roster_positions, num_teams, projections_by_position)

    available = [p for p in table if p.player_id not in rostered_ids]
    return rank_by_vorp(available, replacement_levels), replacement_levels


def _to_suggestion(proj: AdjustedProjection, vor: float, replacement_levels: dict[str, float]) -> WaiverSuggestion:
    bid_pct = max(0, min(100, round(vor * FAAB_PCT_PER_VOR_POINT)))
    group = GRANULAR_TO_GROUP.get(proj.position, proj.position)
    baseline = replacement_levels.get(group, 0.0)
    why = f"{proj.mean:.1f} proj pts vs. {baseline:.1f} replacement level at {group} = {vor:+.1f} VOR"
    return WaiverSuggestion(proj=proj, vor=vor, bid_pct=bid_pct, why=why)


def suggest_waivers(
    conn: sqlite3.Connection,
    league_id: str,
    roster_positions: list[str],
    table: list[AdjustedProjection],
    limit: int = 20,
) -> list[WaiverSuggestion]:
    """Single list ranked purely by VOR across all positions — note this
    naturally skews toward whichever position currently has the thinnest
    replacement level, not "the best pickup at each position." For a
    balanced view, use suggest_waivers_by_position instead.
    """
    ranked, replacement_levels = _rank_available(conn, league_id, roster_positions, table)
    return [_to_suggestion(proj, vor, replacement_levels) for proj, vor in ranked[:limit]]


def suggest_waivers_by_position(
    conn: sqlite3.Connection,
    league_id: str,
    roster_positions: list[str],
    table: list[AdjustedProjection],
    limit_per_position: int = 5,
) -> dict[str, list[WaiverSuggestion]]:
    """Top `limit_per_position` available players at EACH position group
    (still ranked by VOR within the group), so a thin position (e.g. an
    IDP league's DL pool) doesn't crowd every other position off the list.
    Groups are ordered by their own best pickup's VOR, most-impactful first.
    """
    ranked, replacement_levels = _rank_available(conn, league_id, roster_positions, table)

    grouped: dict[str, list[WaiverSuggestion]] = {}
    for proj, vor in ranked:
        group = GRANULAR_TO_GROUP.get(proj.position, proj.position)
        bucket = grouped.setdefault(group, [])
        if len(bucket) < limit_per_position:
            bucket.append(_to_suggestion(proj, vor, replacement_levels))

    return dict(sorted(grouped.items(), key=lambda kv: kv[1][0].vor, reverse=True))
