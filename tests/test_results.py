import sqlite3

from src.decisions.results import summarize_week
from src.ingestion import storage


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    return conn


def seeded_conn(starters, players, players_points, points) -> sqlite3.Connection:
    conn = make_conn()
    storage.save_players(conn, {
        "qb1": {"first_name": "Good", "last_name": "QB", "position": "QB"},
        "qb2": {"first_name": "Better", "last_name": "QB", "position": "QB"},
        "rb1": {"first_name": "Some", "last_name": "RB", "position": "RB"},
    })
    storage.save_matchups(conn, "L1", 3, [{
        "roster_id": 1, "matchup_id": 1, "points": points,
        "starters": starters, "players": players, "players_points": players_points,
    }])
    return conn


def test_detects_points_left_on_bench():
    # Started the worse QB; the better one sat.
    conn = seeded_conn(
        starters=["qb1"], players=["qb1", "qb2"],
        players_points={"qb1": 10.0, "qb2": 25.0}, points=10.0,
    )

    result = summarize_week(conn, "L1", 1, 3, ["QB", "BN"])

    assert result.actual_total == 10.0
    assert result.optimal_total == 25.0
    assert result.points_left_on_bench == 15.0


def test_no_points_left_when_the_right_player_started():
    conn = seeded_conn(
        starters=["qb2"], players=["qb1", "qb2"],
        players_points={"qb1": 10.0, "qb2": 25.0}, points=25.0,
    )

    result = summarize_week(conn, "L1", 1, 3, ["QB", "BN"])

    assert result.points_left_on_bench == 0.0


def test_bench_is_listed_high_to_low():
    conn = seeded_conn(
        starters=["qb1"], players=["qb1", "qb2", "rb1"],
        players_points={"qb1": 10.0, "qb2": 25.0, "rb1": 18.0}, points=10.0,
    )

    result = summarize_week(conn, "L1", 1, 3, ["QB", "BN", "BN"])

    assert [name for name, _pts in result.bench] == ["Better QB", "Some RB"]


def test_returns_none_before_any_points_are_scored():
    conn = seeded_conn(
        starters=["qb1"], players=["qb1"], players_points={}, points=0.0,
    )

    assert summarize_week(conn, "L1", 1, 3, ["QB"]) is None


def test_migration_adds_missing_columns_to_an_existing_db(tmp_path):
    # Simulate a DB created before players_points existed.
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute(
        "CREATE TABLE matchups (league_id TEXT, week INTEGER, roster_id INTEGER, "
        "matchup_id INTEGER, points REAL, starters TEXT, players TEXT, synced_at TEXT, "
        "PRIMARY KEY (league_id, week, roster_id))"
    )
    old.commit()
    old.close()

    conn = storage.get_connection(db)

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(matchups)")}
    assert "players_points" in columns
