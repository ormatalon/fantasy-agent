"""Waiver/FAAB recommendations: rank available (non-rostered) players by
value-over-replacement, size a suggested FAAB bid from that VOR.
"""

import json
import sqlite3
from dataclasses import dataclass

from src.decisions.vorp import compute_replacement_levels, rank_by_vorp
from src.ingestion import schedule
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
    conn: sqlite3.Connection,
    league_id: str,
    roster_positions: list[str],
    table: list[AdjustedProjection],
    exclude_ids: set[str] = frozenset(),
) -> tuple[list[tuple[AdjustedProjection, float]], dict[str, float]]:
    num_teams_row = conn.execute("SELECT COUNT(*) AS n FROM rosters WHERE league_id = ?", (league_id,)).fetchone()
    num_teams = num_teams_row["n"] or 1

    rostered_ids = get_rostered_player_ids(conn, league_id)

    # Rostered players projecting 0 (Out/IR, bye) don't set the bar a pickup
    # has to clear. Left in, a thin position's baseline became an injured
    # player's 0.0, and every free agent there looked like pure value.
    projections_by_position: dict[str, list[float]] = {}
    for proj in table:
        if proj.player_id in rostered_ids and proj.position and proj.mean > 0:
            projections_by_position.setdefault(proj.position, []).append(proj.mean)

    replacement_levels = compute_replacement_levels(roster_positions, num_teams, projections_by_position)

    # Replacement levels still come from every rostered player; exclusions
    # (e.g. games already kicked off) only remove candidates.
    available = [p for p in table if p.player_id not in rostered_ids and p.player_id not in exclude_ids]
    return rank_by_vorp(available, replacement_levels), replacement_levels


def _to_suggestion(
    proj: AdjustedProjection, vor: float, replacement_levels: dict[str, float], weeks: float = 1
) -> WaiverSuggestion:
    # The bid guide is calibrated on one week's VOR; a multi-week table is
    # sized on its per-week equivalent, or every stash would bid 100%.
    bid_pct = max(0, min(100, round(vor / max(weeks, 1) * FAAB_PCT_PER_VOR_POINT)))
    group = GRANULAR_TO_GROUP.get(proj.position, proj.position)
    baseline = replacement_levels.get(group, 0.0)
    why = f"{proj.mean:.1f} proj pts vs. {baseline:.1f} replacement level at {group} = {vor:+.1f} VOR"
    return WaiverSuggestion(proj=proj, vor=vor, bid_pct=bid_pct, why=why)


def unavailable_this_week(
    table: list[AdjustedProjection], kickoffs: dict, now=None
) -> dict[str, str]:
    """{player_id: 'played' | 'bye'} for players who can't score this week."""
    out = {}
    for p in table:
        status = schedule.game_status(p.team, kickoffs, now)
        if status in (schedule.PLAYED, schedule.BYE):
            out[p.player_id] = status
    return out


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
    exclude_ids: set[str] = frozenset(),
    weeks: float = 1,
) -> dict[str, list[WaiverSuggestion]]:
    """Top `limit_per_position` available players at EACH position group
    (still ranked by VOR within the group), so a thin position (e.g. an
    IDP league's DL pool) doesn't crowd every other position off the list.
    Groups are ordered by their own best pickup's VOR, most-impactful first.
    `exclude_ids` drops candidates, e.g. players whose game already kicked off.
    `weeks` is how many weeks `table` covers (for a rest-of-season table).
    """
    ranked, replacement_levels = _rank_available(conn, league_id, roster_positions, table, exclude_ids)

    grouped: dict[str, list[WaiverSuggestion]] = {}
    for proj, vor in ranked:
        group = GRANULAR_TO_GROUP.get(proj.position, proj.position)
        bucket = grouped.setdefault(group, [])
        if len(bucket) < limit_per_position:
            bucket.append(_to_suggestion(proj, vor, replacement_levels, weeks))

    return dict(sorted(grouped.items(), key=lambda kv: kv[1][0].vor, reverse=True))
