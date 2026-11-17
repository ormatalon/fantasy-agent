"""Sleeper -> SQLite: league discovery, league state refresh, roster diffs, players catalog.

Refresh rules are docs/data-contract.md section 5. Failures keep the stored rows
and come back as a list of {"code": "refresh_failed", ...} objects.
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from storage.db import get_json, now_iso, set_settings, tx
from . import sleeper
from .league_format import league_format, league_info
from .sleeper import SleeperError

CATALOG_MAX_AGE = timedelta(hours=20)
refresh_lock = threading.Lock()


class UsernameNotFound(Exception):
    pass


def discover(username: str) -> dict:
    """The user's leagues for the current season, as LeagueInfo items."""
    user = sleeper.get_user(username)
    if not user:
        raise UsernameNotFound(username)
    season = sleeper.get_state()["league_season"]
    leagues = sleeper.get_user_leagues(user["user_id"], season)
    return {
        "username": user["display_name"],
        "user_id": user["user_id"],
        "season": int(season),
        "leagues": [league_info(league) for league in leagues],
    }


# refresh_log

def _since(conn: sqlite3.Connection, source: str, scope: str) -> str | None:
    row = conn.execute(
        "select last_ok_at from refresh_log where source = ? and scope = ?", (source, scope)
    ).fetchone()
    return row["last_ok_at"] if row else None


def _log(conn: sqlite3.Connection, source: str, scope: str, error: str | None) -> None:
    ts = now_iso()
    conn.execute(
        "insert into refresh_log(source, scope, last_attempt_at, last_ok_at, last_error) "
        "values (?, ?, ?, ?, ?) on conflict(source, scope) do update set "
        "last_attempt_at = excluded.last_attempt_at, last_error = excluded.last_error, "
        "last_ok_at = coalesce(excluded.last_ok_at, last_ok_at)",
        (source, scope, ts, None if error else ts, error),
    )


def _run(conn: sqlite3.Connection, source: str, scope: str, fn) -> dict | None:
    """Run one refresh step. On failure log it and return the failure object."""
    since = _since(conn, source, scope)
    try:
        fn()
    except SleeperError as e:
        _log(conn, source, scope, str(e))
        return {
            "code": "refresh_failed",
            "source": source,
            "message": "Could not refresh from Sleeper.",
            "since": since,
        }
    _log(conn, source, scope, None)
    return None


def stored_age(conn: sqlite3.Connection, source: str, scope: str) -> timedelta | None:
    ok = _since(conn, source, scope)
    if ok is None:
        return None
    return datetime.now(UTC) - datetime.fromisoformat(ok)


def failures(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "select source, scope, last_ok_at, last_error from refresh_log where last_error is not null"
    ).fetchall()
    return [
        {
            "code": "refresh_failed",
            "source": r["source"],
            "message": "Could not refresh from Sleeper.",
            "since": r["last_ok_at"],
        }
        for r in rows
    ]


# players catalog

def refresh_players(conn: sqlite3.Connection) -> dict | None:
    age = stored_age(conn, "sleeper", "catalog")
    if age is not None and age < CATALOG_MAX_AGE:
        return None

    def fetch():
        catalog = sleeper.get_players_catalog()
        ts = now_iso()
        rows = [
            (
                pid,
                p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])),
                p["position"],
                json.dumps(p.get("fantasy_positions")),
                p.get("team"),
                p.get("status"),
                int(bool(p.get("active"))),
                p.get("injury_status"),
                p.get("injury_body_part"),
                p.get("practice_participation"),
                p.get("depth_chart_position"),
                p.get("depth_chart_order"),
                p.get("search_rank"),
                p.get("gsis_id"),
                str(p["espn_id"]) if p.get("espn_id") else None,
                ts,
            )
            for pid, p in catalog.items()
            if p.get("position")
        ]
        with tx(conn):
            conn.execute("delete from player")
            conn.executemany("insert into player values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    return _run(conn, "sleeper", "catalog", fetch)


# league state

def _write_league(conn, league: dict, users: list[dict], rosters: list[dict]) -> None:
    ts = now_iso()
    league_id = league["league_id"]
    names = {
        u["user_id"]: (u.get("metadata") or {}).get("team_name") or u["display_name"] for u in users
    }
    old = {}
    for r in conn.execute("select roster_id, player_id from roster_player where league_id = ?", (league_id,)):
        old.setdefault(r["roster_id"], set()).add(r["player_id"])

    teams = []
    for r in rosters:
        s = r["settings"]
        teams.append(
            (
                league_id,
                r["roster_id"],
                r["owner_id"],
                names.get(r["owner_id"]) or f"Fantasy team {r['roster_id']}",
                s.get("wins"),
                s.get("losses"),
                s.get("ties"),
                s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100,
                s.get("waiver_budget_used"),
                ts,
            )
        )
    fmt = league_format(league, [{"roster_id": t[1], "waiver_budget_used": t[8]} for t in teams])

    with tx(conn):
        conn.execute(
            "insert or replace into league values (?,?,?,?,?,?,?,?,?)",
            (
                league_id,
                int(league["season"]),
                league["name"],
                league["status"],
                json.dumps(league["settings"]),
                json.dumps(league["scoring_settings"]),
                json.dumps(league["roster_positions"]),
                json.dumps(fmt),
                ts,
            ),
        )
        conn.execute("delete from fantasy_team where league_id = ?", (league_id,))
        conn.executemany("insert into fantasy_team values (?,?,?,?,?,?,?,?,?,?)", teams)
        conn.execute("delete from roster_player where league_id = ?", (league_id,))
        for r in rosters:
            starters = r.get("starters") or []
            reserve, taxi = set(r.get("reserve") or []), set(r.get("taxi") or [])
            ordered = list(dict.fromkeys(r.get("players") or []))  # Sleeper's order is the bench order
            players = set(ordered)
            for pid in ordered:
                if pid in starters:
                    role, slot = "starter", starters.index(pid)
                elif pid in reserve:
                    role, slot = "reserve", None
                elif pid in taxi:
                    role, slot = "taxi", None
                else:
                    role, slot = "bench", None
                conn.execute(
                    "insert into roster_player values (?,?,?,?,?)", (league_id, r["roster_id"], pid, role, slot)
                )
            before = old.get(r["roster_id"])
            if before is not None and before != players:
                conn.execute(
                    "insert into roster_diff(league_id, roster_id, detected_at, added, dropped) values (?,?,?,?,?)",
                    (
                        league_id,
                        r["roster_id"],
                        ts,
                        json.dumps(sorted(players - before)),
                        json.dumps(sorted(before - players)),
                    ),
                )


def _write_matchups(conn, league_id: str, week: int, matchups: list[dict]) -> None:
    ts = now_iso()
    with tx(conn):
        conn.execute("delete from matchup where league_id = ? and week = ?", (league_id, week))
        conn.execute("delete from matchup_player where league_id = ? and week = ?", (league_id, week))
        for m in matchups:
            conn.execute(
                "insert into matchup values (?,?,?,?,?,?)",
                (league_id, week, m["roster_id"], m["matchup_id"], m["points"], ts),
            )
            starters = m.get("starters") or []
            for pid, points in (m.get("players_points") or {}).items():
                conn.execute(
                    "insert into matchup_player values (?,?,?,?,?,?,?)",
                    (
                        league_id,
                        week,
                        m["roster_id"],
                        pid,
                        points,
                        int(pid in starters),
                        starters.index(pid) if pid in starters else None,
                    ),
                )


def _write_transactions(conn, league_id: str, transactions: list[dict]) -> None:
    with tx(conn):
        for t in transactions:
            conn.execute(
                "insert or replace into transaction_log values (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    league_id,
                    t["transaction_id"],
                    t.get("leg"),
                    t["type"],
                    t["status"],
                    t.get("created"),
                    json.dumps(t.get("adds")),
                    json.dumps(t.get("drops")),
                    json.dumps(t.get("roster_ids")),
                    json.dumps(t.get("waiver_budget")),
                    json.dumps(t.get("draft_picks")),
                ),
            )


def refresh_league(conn: sqlite3.Connection, league_id: str) -> list[dict]:
    """Re-pull this league's state. Returns the failures (empty when all went well)."""
    with refresh_lock:
        first = conn.execute("select 1 from fantasy_team where league_id = ?", (league_id,)).fetchone() is None
        failed = []

        def state():
            s = sleeper.get_state()
            set_settings(conn, {"nfl_state": {"season": int(s["league_season"]), "week": s["week"]}})

        failed.append(_run(conn, "sleeper", "state", state))

        def league_and_rosters():
            _write_league(
                conn,
                sleeper.get_league(league_id),
                sleeper.get_league_users(league_id),
                sleeper.get_league_rosters(league_id),
            )

        failed.append(_run(conn, "sleeper", "league", league_and_rosters))

        week = get_json(conn, "nfl_state", {}).get("week")
        if week:
            weeks = list(range(1, week + 1)) if first else [max(1, week - 1), week]

            def matchups():
                for w in weeks:
                    _write_matchups(conn, league_id, w, sleeper.get_matchups(league_id, w))

            def transactions():
                for w in weeks:
                    _write_transactions(conn, league_id, sleeper.get_transactions(league_id, w))

            failed.append(_run(conn, "sleeper", "matchups", matchups))
            failed.append(_run(conn, "sleeper", "transactions", transactions))
        return [f for f in failed if f]


def refresh(conn: sqlite3.Connection, league_id: str) -> list[dict]:
    """Players catalog (about daily) plus the league. Raises SleeperError when nothing is stored."""
    failed = [f for f in (refresh_players(conn), *refresh_league(conn, league_id)) if f]
    if failed and not conn.execute("select 1 from league where league_id = ?", (league_id,)).fetchone():
        raise SleeperError("nothing stored and Sleeper could not be reached")
    return failed


def ensure_fresh(conn: sqlite3.Connection, league_id: str, max_age: timedelta) -> list[dict]:
    age = stored_age(conn, "sleeper", "league")
    if age is not None and age < max_age:
        return []
    return refresh(conn, league_id)
