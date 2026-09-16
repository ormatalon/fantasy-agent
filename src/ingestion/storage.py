"""SQLite-backed local storage for league-wide state.

Holds everyone's rosters, not just mine, plus matchups, transactions, the
players catalog, and league settings — all snapshotted with a synced_at
timestamp so later syncs simply overwrite the latest state.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS leagues (
    league_id TEXT PRIMARY KEY,
    season TEXT,
    name TEXT,
    scoring_settings TEXT,
    roster_positions TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS league_users (
    league_id TEXT,
    user_id TEXT,
    display_name TEXT,
    team_name TEXT,
    synced_at TEXT,
    PRIMARY KEY (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS rosters (
    league_id TEXT,
    roster_id INTEGER,
    owner_id TEXT,
    players TEXT,
    starters TEXT,
    synced_at TEXT,
    PRIMARY KEY (league_id, roster_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    position TEXT,
    team TEXT,
    status TEXT,
    injury_status TEXT,
    depth_chart_position TEXT,
    depth_chart_order INTEGER,
    gsis_id TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS matchups (
    league_id TEXT,
    week INTEGER,
    roster_id INTEGER,
    matchup_id INTEGER,
    points REAL,
    starters TEXT,
    players TEXT,
    synced_at TEXT,
    PRIMARY KEY (league_id, week, roster_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    league_id TEXT,
    week INTEGER,
    type TEXT,
    status TEXT,
    adds TEXT,
    drops TEXT,
    roster_ids TEXT,
    created INTEGER,
    synced_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# --- app state (which league is active, etc.) ---

def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# --- writes ---

def save_league(conn: sqlite3.Connection, league: dict) -> None:
    conn.execute(
        "INSERT INTO leagues (league_id, season, name, scoring_settings, roster_positions, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(league_id) DO UPDATE SET season=excluded.season, name=excluded.name, "
        "scoring_settings=excluded.scoring_settings, roster_positions=excluded.roster_positions, "
        "synced_at=excluded.synced_at",
        (
            league["league_id"],
            league.get("season"),
            league.get("name"),
            json.dumps(league.get("scoring_settings", {})),
            json.dumps(league.get("roster_positions", [])),
            now_iso(),
        ),
    )
    conn.commit()


def save_league_users(conn: sqlite3.Connection, league_id: str, users: list[dict]) -> None:
    ts = now_iso()
    rows = [
        (
            league_id,
            u["user_id"],
            u.get("display_name"),
            (u.get("metadata") or {}).get("team_name") or u.get("display_name"),
            ts,
        )
        for u in users
    ]
    conn.executemany(
        "INSERT INTO league_users (league_id, user_id, display_name, team_name, synced_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(league_id, user_id) DO UPDATE SET display_name=excluded.display_name, "
        "team_name=excluded.team_name, synced_at=excluded.synced_at",
        rows,
    )
    conn.commit()


def save_rosters(conn: sqlite3.Connection, league_id: str, rosters: list[dict]) -> None:
    ts = now_iso()
    rows = [
        (
            league_id,
            r["roster_id"],
            r.get("owner_id"),
            json.dumps(r.get("players") or []),
            json.dumps(r.get("starters") or []),
            ts,
        )
        for r in rosters
    ]
    conn.executemany(
        "INSERT INTO rosters (league_id, roster_id, owner_id, players, starters, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(league_id, roster_id) DO UPDATE SET owner_id=excluded.owner_id, "
        "players=excluded.players, starters=excluded.starters, synced_at=excluded.synced_at",
        rows,
    )
    conn.commit()


def save_players(conn: sqlite3.Connection, players: dict) -> None:
    ts = now_iso()
    rows = [
        (
            pid,
            p.get("first_name"),
            p.get("last_name"),
            p.get("position"),
            p.get("team"),
            p.get("status"),
            p.get("injury_status"),
            p.get("depth_chart_position"),
            p.get("depth_chart_order"),
            p.get("gsis_id"),
            ts,
        )
        for pid, p in players.items()
    ]
    conn.executemany(
        "INSERT INTO players (player_id, first_name, last_name, position, team, status, "
        "injury_status, depth_chart_position, depth_chart_order, gsis_id, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(player_id) DO UPDATE SET first_name=excluded.first_name, "
        "last_name=excluded.last_name, position=excluded.position, team=excluded.team, "
        "status=excluded.status, injury_status=excluded.injury_status, "
        "depth_chart_position=excluded.depth_chart_position, "
        "depth_chart_order=excluded.depth_chart_order, gsis_id=excluded.gsis_id, "
        "synced_at=excluded.synced_at",
        rows,
    )
    conn.commit()


def players_last_synced(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(synced_at) AS ts FROM players").fetchone()
    return row["ts"] if row else None


def save_matchups(conn: sqlite3.Connection, league_id: str, week: int, matchups: list[dict]) -> None:
    ts = now_iso()
    rows = [
        (
            league_id,
            week,
            m["roster_id"],
            m.get("matchup_id"),
            m.get("points"),
            json.dumps(m.get("starters") or []),
            json.dumps(m.get("players") or []),
            ts,
        )
        for m in matchups
    ]
    conn.executemany(
        "INSERT INTO matchups (league_id, week, roster_id, matchup_id, points, starters, players, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(league_id, week, roster_id) DO UPDATE SET matchup_id=excluded.matchup_id, "
        "points=excluded.points, starters=excluded.starters, players=excluded.players, "
        "synced_at=excluded.synced_at",
        rows,
    )
    conn.commit()


def save_transactions(conn: sqlite3.Connection, league_id: str, week: int, transactions: list[dict]) -> None:
    ts = now_iso()
    rows = [
        (
            t["transaction_id"],
            league_id,
            week,
            t.get("type"),
            t.get("status"),
            json.dumps(t.get("adds") or {}),
            json.dumps(t.get("drops") or {}),
            json.dumps(t.get("roster_ids") or []),
            t.get("created"),
            ts,
        )
        for t in transactions
    ]
    conn.executemany(
        "INSERT INTO transactions (transaction_id, league_id, week, type, status, adds, drops, "
        "roster_ids, created, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(transaction_id) DO UPDATE SET status=excluded.status, adds=excluded.adds, "
        "drops=excluded.drops, roster_ids=excluded.roster_ids, synced_at=excluded.synced_at",
        rows,
    )
    conn.commit()


# --- reads ---

def player_name(conn: sqlite3.Connection, player_id: str) -> str:
    row = conn.execute(
        "SELECT first_name, last_name, position, team FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if not row:
        return f"Unknown player ({player_id})"
    name = " ".join(p for p in [row["first_name"], row["last_name"]] if p)
    tag = "/".join(p for p in [row["position"], row["team"]] if p)
    return f"{name} ({tag})" if tag else name


def find_player_id_by_name(conn: sqlite3.Connection, query: str) -> str | None:
    """Fuzzy-match a player name (case-insensitive substring). Returns the
    first match; ambiguous queries should be narrowed by the caller."""
    like = f"%{query.lower()}%"
    row = conn.execute(
        "SELECT player_id FROM players WHERE LOWER(first_name || ' ' || last_name) LIKE ? LIMIT 1",
        (like,),
    ).fetchone()
    return row["player_id"] if row else None


def get_roster(conn: sqlite3.Connection, league_id: str, roster_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM rosters WHERE league_id = ? AND roster_id = ?",
        (league_id, roster_id),
    ).fetchone()


def get_roster_id_for_user(conn: sqlite3.Connection, league_id: str, user_id: str) -> int | None:
    row = conn.execute(
        "SELECT roster_id FROM rosters WHERE league_id = ? AND owner_id = ?",
        (league_id, user_id),
    ).fetchone()
    return row["roster_id"] if row else None


def find_roster_by_team_query(conn: sqlite3.Connection, league_id: str, query: str) -> sqlite3.Row | None:
    """Fuzzy-match a team/display name to a roster (case-insensitive substring)."""
    like = f"%{query.lower()}%"
    row = conn.execute(
        "SELECT r.* FROM rosters r JOIN league_users u "
        "ON r.league_id = u.league_id AND r.owner_id = u.user_id "
        "WHERE r.league_id = ? AND (LOWER(u.team_name) LIKE ? OR LOWER(u.display_name) LIKE ?)",
        (league_id, like, like),
    ).fetchone()
    return row


def team_label(conn: sqlite3.Connection, league_id: str, owner_id: str) -> str:
    row = conn.execute(
        "SELECT display_name, team_name FROM league_users WHERE league_id = ? AND user_id = ?",
        (league_id, owner_id),
    ).fetchone()
    if not row:
        return f"Unknown owner ({owner_id})"
    return row["team_name"] or row["display_name"] or owner_id


def get_opponent_roster(conn: sqlite3.Connection, league_id: str, week: int, roster_id: int) -> sqlite3.Row | None:
    mine = conn.execute(
        "SELECT matchup_id FROM matchups WHERE league_id = ? AND week = ? AND roster_id = ?",
        (league_id, week, roster_id),
    ).fetchone()
    if not mine or mine["matchup_id"] is None:
        return None
    return conn.execute(
        "SELECT * FROM matchups WHERE league_id = ? AND week = ? AND matchup_id = ? AND roster_id != ?",
        (league_id, week, mine["matchup_id"], roster_id),
    ).fetchone()


def get_recent_transactions(conn: sqlite3.Connection, league_id: str, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM transactions WHERE league_id = ? ORDER BY created DESC LIMIT ?",
        (league_id, limit),
    ).fetchall()
