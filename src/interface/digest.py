"""Assembles the weekly digest: lineup, waiver board, news, last week's result.

Deterministic on purpose. This is what a scheduled Sunday-morning job sends,
so it must not depend on a flaky free-tier model being up. The agent can
narrate the same underlying data on demand via `ask`; the digest is the
version that has to work unattended.
"""

import json
import sqlite3

from src.decisions.lineup import optimize_lineup
from src.decisions.results import summarize_week
from src.decisions.waivers import suggest_waivers_by_position
from src.ingestion import storage
from src.projections.engine import build_projection_table


def _section(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


# Every starter has *some* news every week, so surfacing all of it produces a
# "watch list" containing the whole lineup, which is no list at all. Only news
# that might change a start/sit decision earns a line. Keyword matching is a
# blunt instrument, deliberately biased toward false positives - missing a
# ruled-out starter costs far more than one extra line in an email.
_ACTIONABLE_NEWS = (
    # Stems, not whole words: "practic" catches practice/practicing/practiced.
    "injur", "practic", "questionable", "doubtful", "ruled out", " out ",
    "ir ", "injured reserve", "role", "snap", "starter", "start ", "return",
    "suspend", "limited", "dnp", "backup", "committee", "workload",
)


def _is_actionable(title: str) -> bool:
    lowered = f" {title.lower()} "
    return any(marker in lowered for marker in _ACTIONABLE_NEWS)


def _watch_list(conn, slots, projections, news_limit: int) -> list[str]:
    flagged = []
    for slot in slots:
        if not slot.player_id:
            continue
        proj = projections.get(slot.player_id)
        if proj and proj.injury_multiplier != 1.0:
            flagged.append(f"  {slot.name}: injury adjustment x{proj.injury_multiplier:.2f}")
        for item in storage.get_player_news(conn, slot.player_id, limit=news_limit):
            if _is_actionable(item["title"]):
                flagged.append(f"  {slot.name}: {item['title']}")
    return flagged


def build_digest(
    conn: sqlite3.Connection,
    league_id: str,
    user_id: str,
    season: str,
    week: int,
    waiver_limit: int = 3,
    news_limit: int = 1,
) -> str:
    roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
    if roster_id is None:
        return "Could not find your roster in this league."

    league_row = conn.execute(
        "SELECT roster_positions FROM leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    roster_positions = json.loads(league_row["roster_positions"])
    roster_row = storage.get_roster(conn, league_id, roster_id)
    player_ids = json.loads(roster_row["players"])

    parts = [f"Fantasy digest - {storage.team_label(conn, league_id, user_id)}, week {week} ({season})"]

    opponent = storage.get_opponent_roster(conn, league_id, week, roster_id)
    if opponent:
        opp_roster = storage.get_roster(conn, league_id, opponent["roster_id"])
        if opp_roster:
            parts.append(f"Opponent: {storage.team_label(conn, league_id, opp_roster['owner_id'])}")

    table = build_projection_table(conn, season, week)
    projections = {row.player_id: row for row in table}

    player_meta = {}
    for pid in player_ids:
        p = conn.execute(
            "SELECT first_name, last_name, position FROM players WHERE player_id = ?", (pid,)
        ).fetchone()
        if p:
            name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) or pid
            player_meta[pid] = {"name": name, "position": p["position"]}

    # --- recommended lineup ---
    slots = optimize_lineup(roster_positions, player_ids, projections, player_meta)
    parts.append(_section("Recommended lineup"))
    for slot in slots:
        parts.append(f"  {slot.slot:<12}{slot.name:<24}{slot.mean:>6.1f}")
    parts.append(f"  {'':<12}{'TOTAL':<24}{sum(s.mean for s in slots):>6.1f}")

    flagged = _watch_list(conn, slots, projections, news_limit)
    if flagged:
        parts.append(_section("Watch list (starters needing a decision)"))
        parts.extend(flagged)

    # --- waiver board ---
    grouped = suggest_waivers_by_position(conn, league_id, roster_positions, table, waiver_limit)
    if grouped:
        parts.append(_section("Waiver board"))
        for group, suggestions in grouped.items():
            parts.append(f"  {group}:")
            for s in suggestions:
                parts.append(
                    f"    {s.proj.name:<24}{s.proj.mean:>6.1f}  VOR {s.vor:+.1f}  bid ~{s.bid_pct}%"
                )

    # --- how last week actually went ---
    if week > 1:
        last = summarize_week(conn, league_id, roster_id, week - 1, roster_positions)
        if last:
            parts.append(_section(f"Week {last.week} result"))
            parts.append(f"  Scored {last.actual_total:.1f}, best possible {last.optimal_total:.1f}")
            parts.append(f"  Points left on bench: {last.points_left_on_bench:.1f}")

    return "\n".join(parts)
