"""The per-turn context block (docs/data-contract.md section 12): facts only, rebuilt every model turn."""

import json
import sqlite3
from datetime import datetime

from ingestion import sync
from ingestion.nfl import service
from storage.db import get_json
from . import data
from .render import format_lines, ordinal, roster_lines, unprojected, when

# Standing gaps, stated as facts so the model says them without a prompt rule.
# Remove an item when its source lands (inactives: once ESPN didNotPlay is verified before a kickoff).
GAPS = [
    "inactives are known only after ESPN marks them for a game (not verified before kickoff); until then not available",
    "practice status is the latest in the weekly report, no per-day history",
    "guillotine leagues cannot be detected (Sleeper has no setting for it)",
    "pending waiver claims and open trade offers not available in this version",
]
# NFL fact sources shown on the DATA line: (source, refresh_log scope, words)
NFL_DATA = [
    ("espn", "schedule", "ESPN scoreboard"), ("nflverse", "injury", "official NFL injury report via nflverse"),
    ("espn", "injury", "ESPN injury list"), ("nflverse", "depth", "nflverse depth chart"),
    ("nflverse", "usage", "nflverse snap counts"), ("sleeper_stats", "stats", "Sleeper weekly stats"),
]


def build_context(conn: sqlite3.Connection, league_id: str, since: str | None, now: datetime | None = None) -> str:
    """since: when the chat's previous question ended (ISO UTC); RECENT CHANGES lists my roster moves from then on."""
    now = (now or datetime.now()).astimezone()
    league = data.league_row(conn, league_id)
    fmt = json.loads(league["format"])
    week, last = data.weeks(conn)
    lg = data.League(conn, league_id)
    teams = data.fantasy_teams(conn, league_id)
    me = next(t for t in teams if t["roster_id"] == lg.me)

    lines = [
        f"TODAY: {now:%A %Y-%m-%d}, {now:%H:%M} local time ({now.tzname()})",
        f"SEASON: {fmt['season']}. THIS WEEK: {week} (next to be played). LAST COMPLETED WEEK: {last}",
        "GAME WINDOW: " + ("open (an NFL game is in progress or kicks off within 3 hours)"
                           if service.in_game_window(conn, now) else "closed (no NFL game within 3 hours)"),
    ]
    others = []
    for item in get_json(conn, "leagues", []):
        if item["league_id"] == league_id:
            continue
        note = "" if item["selectable"] else f" (not playable: {item['not_selectable_reason'].replace('_', ' ')})"
        others.append(item["name"] + note)
    lines.append(f"LEAGUE: {league['name']}" + (f". MY OTHER LEAGUES: {'; '.join(others)}" if others else ""))
    lines += format_lines(fmt, lg.me, week)
    lines.append(
        f"MY FANTASY TEAM: {me['name']}, record {data.record(me)}, {ordinal(teams.index(me) + 1)} of {len(teams)} "
        "fantasy teams"
    )
    meta = conn.execute("select unprojected_scoring_items from projection_meta where league_id = ?",
                        (league_id,)).fetchone()
    if meta is None:
        lines.append("PROJECTIONS: not built yet for this league, so which scoring rules they cover is not known")
    else:
        lines.append("PROJECTIONS: " + (unprojected(json.loads(meta[0]))
                                        or "every league scoring rule is included in the projections"))
    opp = data.opponent(conn, league_id, week, lg.me) if week else None
    lines.append(
        f"THIS WEEK'S OPPONENT: {opp['name']} (fantasy team), record {data.record(opp)}" if opp
        else "THIS WEEK'S OPPONENT: none stored for this week"
    )
    lines.append(f"MY ROSTER as set in Sleeper (authoritative, as of {when(league['fetched_at'])}):")
    lines += ["  " + line for line in roster_lines(lg, lg.me)]

    diffs = conn.execute(
        "select * from roster_diff where league_id = ? and roster_id = ? and detected_at >= ? order by id",
        (league_id, lg.me, since or ""),
    ).fetchall()
    if diffs:
        def names(ids):
            return ", ".join((data.player(conn, i) or {"full_name": i})["full_name"] for i in json.loads(ids)) or "nobody"

        changes = [f"{when(d['detected_at'])} added {names(d['added'])}; dropped {names(d['dropped'])}" for d in diffs]
        lines.append("RECENT CHANGES TO MY ROSTER: " + " | ".join(changes))

    sources = [
        ("Sleeper rosters", league["fetched_at"]),
        ("Sleeper players catalog", data.catalog_as_of(conn)),
        ("Sleeper matchups", data.refresh_ok_at(conn, "sleeper", "matchups")),
        ("Sleeper transactions", data.refresh_ok_at(conn, "sleeper", "transactions")),
        *[(words, data.refresh_ok_at(conn, source, scope)) for source, scope, words in NFL_DATA],
    ]
    lines.append("DATA: " + "; ".join(f"{label} {when(ts)}" for label, ts in sources if ts))
    lines.append("GAPS: " + "; ".join(GAPS))
    failed = sync.failures(conn)
    if failed:
        lines.append(
            "REFRESH FAILED: "
            + " ".join(f"{f['message']} Using data from {when(f['since'])}." for f in failed)
        )
    return "\n".join(lines)
