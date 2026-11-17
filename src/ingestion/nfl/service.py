"""NFL facts: refresh from the sources on the freshness schedule, and read them back with source and age.

docs/data-contract.md sections 2 to 5. Choosing between sources happens here, on read.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from storage.db import get_json, get_setting, now_iso, set_settings, tx

from .. import projection_pulls, sleeper
from ..sync import _log, failures, stored_age
from . import sleeper_facts, teams
from .base import SourceError, iso
from .crosswalk import FANTASY_POSITIONS, Crosswalk
from .espn import EspnSource
from .nflverse import NflverseSource

# (source, kind, seconds between pulls normally, seconds in the game window; None = not pulled then)
STEPS = [
    ("nflverse", "schedule", 7200, 7200),
    ("espn", "schedule", 1800, 120),
    ("nflverse", "roster", 21600, 21600),
    ("nflverse", "injury", 1800, 600),
    ("nflverse", "depth", 7200, 1800),
    ("nflverse", "usage", 21600, 21600),
    ("espn", "injury", 1800, 120),
    ("espn", "inactive", None, 120),
    ("sleeper", "stats", 21600, 1800),
    ("sleeper", "projections", 21600, 1800),       # this week: every 30 min in the game window, so a pull exists shortly before each kickoff
    ("sleeper", "projections_ros", 86400, 86400),  # later weeks and the season total
]
ESPN_BACKOFF = timedelta(minutes=10)

# minutes after which a fact is called stale: (normal, game window)
STALE = {
    ("nflverse", "injury"): (120, 120), ("espn", "injury"): (120, 2), ("sleeper", "injury"): (2160, 2160),
    ("nflverse", "roster"): (2160, 2160), ("espn", "roster"): (120, 120), ("sleeper", "roster"): (2160, 2160),
    ("nflverse", "depth"): (240, 240), ("sleeper", "depth"): (2160, 2160),
    ("nflverse", "usage"): (2160, 2160), ("sleeper", "usage"): (360, 360),
    ("espn", "inactive"): (2, 2), ("sleeper", "news"): (120, 120),
}
LABEL = {
    ("nflverse", "injury"): "official NFL injury report via nflverse", ("espn", "injury"): "ESPN injury list",
    ("sleeper", "injury"): "Sleeper players catalog", ("nflverse", "roster"): "nflverse weekly roster",
    ("espn", "roster"): "ESPN NFL team roster", ("sleeper", "roster"): "Sleeper players catalog",
    ("nflverse", "depth"): "nflverse depth chart", ("sleeper", "depth"): "Sleeper depth order",
    ("nflverse", "usage"): "nflverse snap counts", ("sleeper", "usage"): "Sleeper weekly stats",
    ("espn", "inactive"): "ESPN game roster", ("sleeper", "news"): "Sleeper news (RotoWire)",
}
GAME_STATUS = {"scheduled": "not_started", "in_progress": "in_progress", "final": "complete"}


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---- season, week, games

def season(conn: sqlite3.Connection) -> int:
    state = get_json(conn, "nfl_state")
    if not state:
        s = sleeper.get_state()
        state = {"season": int(s["league_season"]), "week": s["week"]}
        set_settings(conn, {"nfl_state": state})
    return state["season"]


def this_week(conn: sqlite3.Connection) -> int | None:
    """The next week to be played: the lowest week with a game not final (section 5)."""
    row = conn.execute("select min(week) from nfl_game where season = ? and status != 'final'", (season(conn),)).fetchone()
    return row[0] or get_json(conn, "nfl_state", {}).get("week")


def in_game_window(conn: sqlite3.Connection, now: datetime) -> bool:
    """3 hours before a kickoff until the game is final (a game still not final 8 hours after kickoff is ignored)."""
    row = conn.execute(
        "select 1 from nfl_game where status != 'final' and kickoff_utc between ? and ?",
        (iso(now - timedelta(hours=8)), iso(now + timedelta(hours=3))),
    ).fetchone()
    return row is not None


def game_ref(conn: sqlite3.Connection, nfl_team: str | None, week: int) -> dict | None:
    """GameRef (data-contract section 9) for an NFL team in a week; None when the NFL team is unknown or has no game."""
    if not nfl_team:
        return None
    g = conn.execute(
        "select * from nfl_game where season = ? and week = ? and (home = ? or away = ?)",
        (season(conn), week, nfl_team, nfl_team),
    ).fetchone()
    if g:
        home = g["home"] == nfl_team
        return {"status": GAME_STATUS[g["status"]], "opponent_nfl_team": g["away"] if home else g["home"],
                "home": home, "kickoff": g["kickoff_utc"], "status_as_of": g["as_of"]}
    t = conn.execute("select bye_week from nfl_team where abbr = ?", (nfl_team,)).fetchone()
    if t and t["bye_week"] == week:
        return {"status": "bye", "opponent_nfl_team": None, "home": None, "kickoff": None, "status_as_of": now_iso()}
    return None


# ---- refresh

def _insert(conn, table: str, rows: list[dict], verb: str = "insert or replace") -> None:
    if rows:
        cols = list(rows[0])
        conn.executemany(
            f"{verb} into {table}({','.join(cols)}) values ({','.join(':' + c for c in cols)})", rows
        )


def _upsert_nflverse_games(conn, rows: list[dict]) -> None:
    conn.executemany(
        """insert into nfl_game(game_id, season, week, home, away, kickoff_utc, status, home_score, away_score,
             sleeper_game_id, espn_event_id, source, as_of, fetched_at)
           values (:game_id, :season, :week, :home, :away, :kickoff_utc, :status, :home_score, :away_score,
             :sleeper_game_id, :espn_event_id, :source, :as_of, :fetched_at)
           on conflict(game_id) do update set week = excluded.week,
             kickoff_utc = case when nfl_game.source = 'espn' then nfl_game.kickoff_utc else excluded.kickoff_utc end,
             status = case when excluded.status = 'final' then 'final' else nfl_game.status end,
             home_score = coalesce(excluded.home_score, nfl_game.home_score),
             away_score = coalesce(excluded.away_score, nfl_game.away_score),
             espn_event_id = coalesce(excluded.espn_event_id, nfl_game.espn_event_id),
             source = case when nfl_game.source = 'espn' and excluded.status != 'final' then nfl_game.source else excluded.source end,
             as_of = case when nfl_game.source = 'espn' and excluded.status != 'final' then nfl_game.as_of else excluded.as_of end,
             fetched_at = excluded.fetched_at""",
        rows,
    )
    # bye week: the one week 1-18 in which an NFL team has no game
    played = {}
    for r in conn.execute("select week, home, away from nfl_game"):
        played.setdefault(r["home"], set()).add(r["week"])
        played.setdefault(r["away"], set()).add(r["week"])
    for abbr, weeks in played.items():
        free = [w for w in range(1, 19) if w not in weeks]
        if len(free) == 1:  # a partial schedule has several free weeks: leave the bye as it was
            conn.execute("update nfl_team set bye_week = ? where abbr = ?", (free[0], abbr))


def _upsert_espn_games(conn, rows: list[dict]) -> None:
    ts = now_iso()
    for r in rows:
        cur = conn.execute(
            """update nfl_game set kickoff_utc = :kickoff_utc, status = :status, home_score = :home_score,
                 away_score = :away_score, espn_event_id = :espn_event_id, source = 'espn', as_of = :as_of,
                 fetched_at = :as_of where season = :season and week = :week and home = :home and away = :away""",
            r,
        )
        if not cur.rowcount:
            conn.execute(
                "insert into nfl_game(game_id, season, week, home, away, kickoff_utc, status, home_score, away_score,"
                " espn_event_id, source, as_of, fetched_at) values (?,?,?,?,?,?,?,?,?,?,'espn',?,?)",
                (f"{r['season']}_{r['week']:02d}_{r['away']}_{r['home']}", r["season"], r["week"], r["home"], r["away"],
                 r["kickoff_utc"], r["status"], r["home_score"], r["away_score"], r["espn_event_id"], r["as_of"], ts),
            )


def _store(conn, source: str, kind: str, result, season_: int, games: list[dict]) -> None:
    rows = result.rows
    with tx(conn):
        if (source, kind) == ("nflverse", "schedule"):
            _upsert_nflverse_games(conn, rows)
        elif (source, kind) == ("espn", "schedule"):
            _upsert_espn_games(conn, rows)
            for abbr, week in result.extra["byes"].items():
                conn.execute("update nfl_team set bye_week = ? where abbr = ?", (week, abbr))
        elif kind == "roster":
            conn.execute("delete from nfl_roster where source = ?", (source,))
            _insert(conn, "nfl_roster", rows)
        elif kind == "depth":
            conn.execute("delete from nfl_depth where source = ?", (source,))
            _insert(conn, "nfl_depth", rows)
        elif kind == "injury":
            conn.execute("delete from nfl_injury where source = ?", (source,))
            _insert(conn, "nfl_injury", rows)
        elif kind == "usage":
            conn.execute("delete from nfl_usage where source = ?", (source,))
            _insert(conn, "nfl_usage", rows)
        elif kind == "inactive":
            for g in games:
                conn.execute("delete from nfl_inactive where game_id = ? and source = ?", (g["game_id"], source))
            _insert(conn, "nfl_inactive", rows)


def _due(conn, source: str, kind: str, seconds: int, now: datetime) -> bool:
    row = conn.execute("select last_attempt_at from refresh_log where source = ? and scope = ?", (source, kind)).fetchone()
    return row is None or _dt(row["last_attempt_at"]) + timedelta(seconds=seconds) <= now


def seed_teams(conn: sqlite3.Connection) -> None:
    with tx(conn):
        conn.executemany(
            "insert into nfl_team(abbr, name, espn_id, espn_abbr, nflverse_code) values (:abbr, :name, :espn_id, :espn_abbr, :nflverse_code)"
            " on conflict(abbr) do nothing",
            teams.TEAMS,
        )


def refresh(conn: sqlite3.Connection, now: datetime | None = None, force: bool = False) -> list[dict]:
    """Pull every source that is due. Returns the failures (stored rows are kept when a source fails)."""
    now = now or datetime.now(UTC)
    if not conn.execute("select 1 from player limit 1").fetchone():
        return []  # the Sleeper catalog comes first: the crosswalk maps onto it
    s = season(conn)
    seed_teams(conn)
    xwalk = Crosswalk(conn)
    etags = get_json(conn, "nflverse_etags", {})
    sources = {"nflverse": NflverseSource(xwalk, etags), "espn": EspnSource(xwalk)}
    window = in_game_window(conn, now)
    backoff = get_setting(conn, "espn_backoff_until")

    for source, kind, normal, in_window in STEPS:
        seconds = in_window if window else normal
        if seconds is None or (source == "espn" and backoff and backoff > iso(now)):
            continue
        if not force and not _due(conn, source, kind, seconds, now):
            continue
        week = this_week(conn)
        if kind.startswith("projections") and not week:
            continue
        games = []
        if kind == "inactive":
            games = [dict(g) for g in conn.execute(
                "select * from nfl_game where espn_event_id is not null and kickoff_utc between ? and ?",
                (iso(now - timedelta(hours=3)), iso(now + timedelta(hours=3))))]
            if not games:
                continue
        try:
            if source == "sleeper":
                if kind == "stats":
                    sleeper_facts.refresh_stats(conn, s, _stat_weeks(conn, s, week))
                elif kind == "projections":
                    projection_pulls.pull(conn, s, [week])
                else:
                    projection_pulls.pull(conn, s, list(range(week + 1, 19)), include_season=True)
                _log(conn, source, kind, None)
                continue
            result = sources[source].fetch(kind, s, week, games=games) if source == "espn" else sources[source].fetch(kind, s, week)
        except (SourceError, sleeper.SleeperError) as e:
            _log(conn, source, kind, str(e))
            if getattr(e, "status", None) in (403, 429):
                set_settings(conn, {"espn_backoff_until": iso(now + ESPN_BACKOFF)})
                backoff = iso(now + ESPN_BACKOFF)
            continue
        if result is None:  # unchanged asset
            _log(conn, source, kind, None)
            continue
        _store(conn, source, kind, result, s, games)
        _log(conn, source, kind, None)
        coverage = {"mapped": result.total - result.unmapped, "total": result.total, "gaps": result.gaps}
        conn.execute("update refresh_log set coverage = ? where source = ? and scope = ?", (json.dumps(coverage), source, kind))
        if (source, kind) == ("nflverse", "roster"):
            xwalk.save(conn)
            conn.execute(
                "insert into refresh_log(source, scope, last_attempt_at, last_ok_at, coverage) values ('nflverse', 'crosswalk', ?, ?, ?)"
                " on conflict(source, scope) do update set last_attempt_at = excluded.last_attempt_at,"
                " last_ok_at = excluded.last_ok_at, coverage = excluded.coverage",
                (now_iso(), now_iso(), json.dumps(xwalk.coverage(conn))),
            )
    set_settings(conn, {"nflverse_etags": etags})
    return [f for f in failures(conn) if f["source"] != "sleeper"]


def _stat_weeks(conn, season_: int, week: int | None) -> list[int]:
    have = conn.execute("select 1 from player_week_stat where season = ? limit 1", (season_,)).fetchone()
    last = (week or 1) - 1
    return list(range(1, last + 1)) if not have else [w for w in (last - 1, last) if w >= 1]


def team_roster_refresh(conn: sqlite3.Connection, abbr: str, now: datetime | None = None) -> None:
    """ESPN's roster of one NFL team, pulled when asked (at most every 30 minutes)."""
    now = now or datetime.now(UTC)
    backoff = get_setting(conn, "espn_backoff_until")
    if (backoff and backoff > iso(now)) or not _due(conn, "espn", f"roster:{abbr}", 1800, now):
        return
    try:
        result = EspnSource(Crosswalk(conn)).fetch("roster", season(conn), 0, team=abbr)
    except SourceError as e:
        _log(conn, "espn", f"roster:{abbr}", str(e))
        if e.status in (403, 429):
            set_settings(conn, {"espn_backoff_until": iso(now + ESPN_BACKOFF)})
        return
    with tx(conn):
        conn.execute("delete from nfl_roster where source = 'espn' and nfl_team = ?", (abbr,))
        _insert(conn, "nfl_roster", result.rows)
    _log(conn, "espn", f"roster:{abbr}", None)
    conn.execute("update refresh_log set coverage = ? where source = 'espn' and scope = ?",
                 (json.dumps({"mapped": result.total - result.unmapped, "total": result.total}), f"roster:{abbr}"))


def coverage(conn: sqlite3.Connection) -> dict:
    """Crosswalk and per-fetch coverage as stored by the last refresh."""
    out = {}
    for r in conn.execute("select source, scope, coverage from refresh_log where coverage is not null"):
        out[f"{r['source']}:{r['scope']}"] = json.loads(r["coverage"])
    return out


# ---- reading facts

def _fact(kind, player_id, nfl_team, week, source, as_of, fetched_at, value, now, window=False, also=None) -> dict:
    age = max(0, int((now - _dt(as_of)).total_seconds() // 60)) if as_of else None
    limit = STALE.get((source, kind), (120, 120))[window]
    return {
        "kind": kind, "player_id": player_id, "nfl_team": nfl_team, "week": week, "source": source,
        "source_label": LABEL.get((source, kind), f"{source} {kind}"), "as_of": as_of, "fetched_at": fetched_at,
        "age_minutes": age, "age_text": _age_text(age), "stale": age is None or age > limit,
        "also": also or [], "value": value,
    }


def _age_text(minutes: int | None) -> str:
    if minutes is None:
        return "age unknown"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


def _player(conn, player_id: str):
    return conn.execute("select * from player where player_id = ?", (player_id,)).fetchone()


def injury_fact(conn, player_id: str, now: datetime | None = None) -> dict | None:
    """Designation by precedence: official report (nflverse, this week's) > ESPN > Sleeper catalog.

    None when no source lists a designation or practice status. return_date is the newest one any source has.
    """
    now = now or datetime.now(UTC)
    s, week = season(conn), this_week(conn)
    window = in_game_window(conn, now)
    cands = []  # (source, designation, practice_status, body_part, detail, as_of, fetched_at, return_date)
    for r in conn.execute("select * from nfl_injury where player_id = ? and season = ? and source = 'nflverse' and week = ?", (player_id, s, week)):
        cands.append(("nflverse", r["designation"], r["practice_status"], r["body_part"], r["detail"], r["as_of"], r["fetched_at"], None))
    for r in conn.execute("select * from nfl_injury where player_id = ? and source = 'espn'", (player_id,)):
        cands.append(("espn", r["designation"], None, r["body_part"], r["detail"], r["as_of"], r["fetched_at"], r["return_date"]))
    p = _player(conn, player_id)
    if p and (p["injury_status"] or p["practice_participation"]):
        cands.append(("sleeper", p["injury_status"], p["practice_participation"], p["injury_body_part"], None, p["fetched_at"], p["fetched_at"], None))
    if not cands:
        return None
    first = cands[0]
    also = [{"source": c[0], "value": c[1], "as_of": c[5]} for c in cands[1:] if c[1] != first[1]]
    dated = [c for c in cands if c[7]]
    best = max(dated, key=lambda c: c[5]) if dated else None
    value = {"designation": first[1], "practice_status": first[2], "body_part": first[3], "detail": first[4],
             "return_date": best[7] if best else None, "return_date_source": best[0] if best else None,
             "return_date_as_of": best[5] if best else None}
    return _fact("injury", player_id, p["nfl_team"] if p else None, week if first[0] == "nflverse" else None,
                 first[0], first[5], first[6], value, now, window, also)


def roster_fact(conn, player_id: str, now: datetime | None = None) -> dict | None:
    """The player's NFL team by precedence: ESPN (when fresh) > nflverse > Sleeper catalog."""
    now = now or datetime.now(UTC)
    cands = []
    for r in conn.execute("select * from nfl_roster where player_id = ?", (player_id,)):
        cands.append((r["source"], r["nfl_team"], r["roster_status"], r["as_of"], r["fetched_at"]))
    p = _player(conn, player_id)
    if p and p["nfl_team"] and p["active"]:
        cands.append(("sleeper", p["nfl_team"], p["status"], p["fetched_at"], p["fetched_at"]))
    order = {"espn": 0, "nflverse": 1, "sleeper": 2}
    cands.sort(key=lambda c: order[c[0]])
    if cands and cands[0][0] == "espn" and _fact("roster", player_id, cands[0][1], None, "espn", cands[0][3], cands[0][4], {}, now)["stale"]:
        cands.pop(0)
    if not cands:
        return None
    first = cands[0]
    also = [{"source": c[0], "value": c[1], "as_of": c[3]} for c in cands[1:] if c[1] != first[1]]
    return _fact("roster", player_id, first[1], None, first[0], first[3], first[4], {"roster_status": first[2]}, now, False, also)


def usage_facts(conn, player_id: str, weeks: int = 3, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    rows = conn.execute("select * from nfl_usage where player_id = ? and season = ? order by week desc, source", (player_id, season(conn))).fetchall()
    keep = sorted({r["week"] for r in rows}, reverse=True)[:weeks]
    return [
        _fact("usage", player_id, None, r["week"], r["source"], r["as_of"], r["fetched_at"],
              {k: r[k] for k in ("offense_pct", "defense_pct", "st_pct", "targets", "carries", "target_share")}, now)
        for r in rows if r["week"] in keep
    ]


def news_facts(conn, player_id: str, limit: int = 5, now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """Latest news, newest first. A failed pull is returned as a gap, with the stored items."""
    now = now or datetime.now(UTC)
    gaps = [] if sleeper_facts.refresh_news(conn, player_id) else ["news: Sleeper could not be reached; showing stored items"]
    rows = conn.execute("select * from news where player_id = ? order by published_at desc limit ?", (player_id, limit)).fetchall()
    facts = [
        _fact("news", player_id, None, None, "sleeper" if r["source"] == "rotowire" else r["source"], r["published_at"], r["fetched_at"],
              {"title": r["title"], "description": r["description"], "analysis": r["analysis"], "url": r["url"],
               "published_at": r["published_at"]}, now)
        for r in rows
    ]
    return facts, gaps


def inactive_fact(conn, player_id: str, week: int, now: datetime | None = None) -> tuple[dict | None, list[str]]:
    """Game-day inactive for this week's game. A gap when ESPN has not listed any inactives for that game."""
    now = now or datetime.now(UTC)
    p = _player(conn, player_id)
    g = conn.execute(
        "select * from nfl_game where season = ? and week = ? and (home = ? or away = ?)",
        (season(conn), week, p["nfl_team"] if p else None, p["nfl_team"] if p else None),
    ).fetchone()
    if not g:
        return None, []
    listed = conn.execute("select * from nfl_inactive where game_id = ?", (g["game_id"],)).fetchall()
    if not listed:
        return None, [f"inactives: not available for {g['game_id']} (not announced yet or no usable source)"]
    mine = next((r for r in listed if r["player_id"] == player_id), None)
    return _fact("inactive", player_id, p["nfl_team"], week, "espn", listed[0]["as_of"], listed[0]["fetched_at"],
                 {"game_id": g["game_id"], "inactive": mine is not None}, now), []


def fact_set(conn, facts: list[dict], gaps: list[str]) -> dict:
    """NflFactSet (data-contract section 4)."""
    as_ofs = [f["as_of"] for f in facts if f["as_of"]]
    return {"facts": facts, "sources_used": sorted({f["source"] for f in facts}),
            "oldest_as_of": min(as_ofs) if as_ofs else None, "gaps": gaps, "refresh_failed": failures(conn)}


def _gaps(conn, week: int | None) -> list[str]:
    gaps = []
    if week and not conn.execute("select 1 from nfl_injury where source = 'nflverse' and week = ? limit 1", (week,)).fetchone():
        gaps.append(f"no official injury report for week {week} yet (reports start Wednesday); using ESPN and Sleeper")
    return gaps


def player_facts(conn, player_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    week = this_week(conn)
    p = _player(conn, player_id)
    roster = roster_fact(conn, player_id, now)
    injury = injury_fact(conn, player_id, now)
    usage = usage_facts(conn, player_id, 3, now)
    inactive, gaps = inactive_fact(conn, player_id, week, now)
    news, news_gaps = news_facts(conn, player_id, 5, now)
    depth = _depth_labels(conn, roster["nfl_team"] if roster else None, now).get(player_id)
    facts = [f for f in (roster, injury, inactive, depth) if f] + usage + news
    return {
        "player": dict(p) if p else None,
        "roster": roster, "injury": injury, "depth": depth, "usage": usage, "inactive": inactive, "news": news,
        "game": game_ref(conn, roster["nfl_team"], week) if roster else None,
        "set": fact_set(conn, facts, gaps + news_gaps + _gaps(conn, week)),
    }


def _depth_labels(conn, nfl_team: str | None, now: datetime) -> dict[str, dict]:
    """player_id -> depth fact for one NFL team: nflverse depth chart, else Sleeper's depth order."""
    if not nfl_team:
        return {}
    out = {}
    # a player can be listed several times (WR2 and KR1): his position's row first, special teams last
    rows = conn.execute("select * from nfl_depth where source = 'nflverse' and nfl_team = ? order by unit = 'special', rank", (nfl_team,))
    for r in rows:
        out.setdefault(r["player_id"], _fact("depth", r["player_id"], nfl_team, None, "nflverse", r["as_of"], r["fetched_at"],
                                            {"unit": r["unit"], "slot_label": r["slot_label"], "rank": r["rank"], "basis": "depth_chart"}, now))
    if out:
        return out
    for r in conn.execute("select * from player where nfl_team = ? and active = 1 and depth_chart_order is not null", (nfl_team,)):
        label = f"{r['depth_chart_position']}{r['depth_chart_order']}"
        out[r["player_id"]] = _fact("depth", r["player_id"], nfl_team, None, "sleeper", r["fetched_at"], r["fetched_at"],
                                    {"unit": None, "slot_label": label, "rank": r["depth_chart_order"], "basis": "sleeper_depth_order"}, now)
    return out


def team_roster(conn, nfl_team: str, position: str | None = None, now: datetime | None = None) -> dict:
    """An NFL team's current players with depth label, injury and latest usage. Fantasy and IDP positions only."""
    now = now or datetime.now(UTC)
    team_roster_refresh(conn, nfl_team, now)
    ids = {r[0] for r in conn.execute(
        "select player_id from nfl_roster where nfl_team = ? union select player_id from player where nfl_team = ? and active = 1",
        (nfl_team, nfl_team))}
    depth = _depth_labels(conn, nfl_team, now)
    players, facts = [], []
    for pid in sorted(ids):
        p = _player(conn, pid)
        roster = roster_fact(conn, pid, now)
        if not p or not roster or roster["nfl_team"] != nfl_team or p["position"] not in FANTASY_POSITIONS | {"DEF"}:
            continue  # a newer source says he is on another NFL team, or not a fantasy position
        if position and p["position"] != position.upper():
            continue
        injury = injury_fact(conn, pid, now)
        players.append({"player_id": pid, "name": p["full_name"], "position": p["position"], "roster": roster,
                        "depth": depth.get(pid), "injury": injury, "usage": usage_facts(conn, pid, 1, now)})
        facts += [f for f in (roster, depth.get(pid), injury) if f] + players[-1]["usage"]
    players.sort(key=lambda x: (x["position"], x["depth"]["value"]["rank"] if x["depth"] else 99, x["name"] or ""))
    return {"nfl_team": nfl_team, "players": players, "set": fact_set(conn, facts, _gaps(conn, this_week(conn)))}
