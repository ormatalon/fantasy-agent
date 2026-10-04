"""SQLite-backed local storage for league-wide state.

Holds everyone's rosters, not just mine, plus matchups, transactions, the
players catalog, and league settings — all snapshotted with a synced_at
timestamp so later syncs simply overwrite the latest state.
"""

import json
import re
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
    players_points TEXT,
    synced_at TEXT,
    PRIMARY KEY (league_id, week, roster_id)
);

CREATE TABLE IF NOT EXISTS player_news (
    news_id TEXT PRIMARY KEY,
    player_id TEXT,
    published INTEGER,
    source TEXT,
    title TEXT,
    description TEXT,
    analysis TEXT,
    url TEXT,
    synced_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_player_news_player ON player_news (player_id, published DESC);

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
    # check_same_thread=False: LangGraph runs tool nodes on worker threads, so
    # the agent's tools touch this connection from a different thread than
    # created it. Python's sqlite3 is in serialized threading mode by default,
    # and our access is effectively one-at-a-time, so this is safe here.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after a table already existed in the wild. CREATE TABLE IF
# NOT EXISTS won't add them to an existing local DB, and making people delete
# their database to pick up a new field is a bad trade.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("matchups", "players_points", "TEXT"),
    ("players", "injury_status", "TEXT"),
    ("players", "depth_chart_position", "TEXT"),
    ("players", "depth_chart_order", "INTEGER"),
    ("players", "gsis_id", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _ADDED_COLUMNS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()


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


# --- remembered user preferences (the agent's long-term memory) ---

def get_preferences(conn: sqlite3.Connection) -> list[str]:
    return json.loads(get_state(conn, "preferences") or "[]")


def add_preference(conn: sqlite3.Connection, note: str) -> list[str]:
    prefs = get_preferences(conn)
    if note not in prefs:
        prefs.append(note)
        set_state(conn, "preferences", json.dumps(prefs))
    return prefs


def remove_preference(conn: sqlite3.Connection, number: int) -> str | None:
    """Remove the 1-based `number`th preference; returns it, or None."""
    prefs = get_preferences(conn)
    if not 1 <= number <= len(prefs):
        return None
    removed = prefs.pop(number - 1)
    set_state(conn, "preferences", json.dumps(prefs))
    return removed


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
            json.dumps(m.get("players_points") or {}),
            ts,
        )
        for m in matchups
    ]
    conn.executemany(
        "INSERT INTO matchups (league_id, week, roster_id, matchup_id, points, starters, players, "
        "players_points, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(league_id, week, roster_id) DO UPDATE SET matchup_id=excluded.matchup_id, "
        "points=excluded.points, starters=excluded.starters, players=excluded.players, "
        "players_points=excluded.players_points, synced_at=excluded.synced_at",
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


def get_injury_status(conn: sqlite3.Connection, player_id: str | None) -> str | None:
    if not player_id:
        return None
    row = conn.execute("SELECT injury_status FROM players WHERE player_id = ?", (player_id,)).fetchone()
    return row["injury_status"] if row else None


def save_player_news(conn: sqlite3.Connection, items) -> None:
    """`items` are ingestion.news.NewsItem records."""
    ts = now_iso()
    rows = [
        (i.news_id, i.player_id, i.published, i.source, i.title, i.description, i.analysis, i.url, ts)
        for i in items
    ]
    conn.executemany(
        "INSERT INTO player_news (news_id, player_id, published, source, title, description, "
        "analysis, url, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(news_id) DO UPDATE SET title=excluded.title, description=excluded.description, "
        "analysis=excluded.analysis, url=excluded.url, synced_at=excluded.synced_at",
        rows,
    )
    conn.commit()


def get_player_news(conn: sqlite3.Connection, player_id: str, limit: int = 3) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM player_news WHERE player_id = ? ORDER BY published DESC LIMIT ?",
        (player_id, limit),
    ).fetchall()


def _tags(conn: sqlite3.Connection, player_id: str) -> set[str]:
    row = conn.execute("SELECT position, team FROM players WHERE player_id = ?", (player_id,)).fetchone()
    return {t.upper() for t in (row["position"], row["team"]) if t} if row else set()


def _full_name(conn: sqlite3.Connection, player_id: str) -> str:
    row = conn.execute("SELECT first_name, last_name FROM players WHERE player_id = ?", (player_id,)).fetchone()
    return " ".join(p for p in [row["first_name"], row["last_name"]] if p) if row else ""


def find_players_by_name(
    conn: sqlite3.Connection, query: str, league_id: str | None = None, limit: int = 5
) -> list[str]:
    """Candidate player ids for a name, best first: exact full-name matches
    before substring ones, then rostered in `league_id`, then players on an
    NFL team (the catalog also holds retired players and free agents)."""
    q = query.strip().lower()
    if not q:
        return []
    rows = conn.execute(
        "SELECT player_id, LOWER(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS full_name, "
        "team FROM players WHERE LOWER(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) LIKE ?",
        (f"%{q}%",),
    ).fetchall()
    rostered: set[str] = set()
    if league_id:
        for r in conn.execute("SELECT players FROM rosters WHERE league_id = ?", (league_id,)):
            rostered.update(json.loads(r["players"]))

    def rank(r) -> tuple:
        return (r["full_name"] != q, r["player_id"] not in rostered, not r["team"], r["full_name"])

    return [r["player_id"] for r in sorted(rows, key=rank)[:limit]]


def resolve_player(
    conn: sqlite3.Connection, query: str, league_id: str | None = None
) -> tuple[str | None, list[str]]:
    """(player_id, candidates). player_id is set only when the name is
    unambiguous: a single match, or a single exact full-name match. Otherwise
    the caller must ask which one was meant - never guess.

    Accepts the "Name (POS/TEAM)" form that `player_name` prints, so a
    candidate list can be answered with an exact pick."""
    tags: set[str] = set()
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", query)
    if m:
        query, tags = m.group(1), {t.strip().upper() for t in m.group(2).split("/") if t.strip()}
    candidates = find_players_by_name(conn, query, league_id, limit=25 if tags else 5)
    if tags:
        candidates = [pid for pid in candidates if tags <= _tags(conn, pid)][:5]
    if len(candidates) == 1:
        return candidates[0], candidates
    q = query.strip().lower()
    exact = [pid for pid in candidates if _full_name(conn, pid).lower() == q]
    if len(exact) == 1:
        return exact[0], candidates
    if len(exact) > 1:
        return None, exact
    return None, candidates


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


def list_leagues(conn: sqlite3.Connection, season: str | None = None) -> list[sqlite3.Row]:
    if season:
        return conn.execute(
            "SELECT league_id, name, season FROM leagues WHERE season = ? ORDER BY name", (season,)
        ).fetchall()
    return conn.execute("SELECT league_id, name, season FROM leagues ORDER BY season DESC, name").fetchall()


def find_league(conn: sqlite3.Connection, query: str, season: str | None = None) -> sqlite3.Row | None:
    """Match a league by exact id, else by case-insensitive name substring.
    None when nothing or more than one league matches."""
    leagues = list_leagues(conn, season)
    by_id = [lg for lg in leagues if lg["league_id"] == query]
    if by_id:
        return by_id[0]
    q = query.strip().lower()
    matches = [lg for lg in leagues if q in (lg["name"] or "").lower()]
    return matches[0] if len(matches) == 1 else None


def get_recent_transactions(conn: sqlite3.Connection, league_id: str, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM transactions WHERE league_id = ? ORDER BY created DESC LIMIT ?",
        (league_id, limit),
    ).fetchall()
