import sqlite3

from src.ingestion import storage


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    return conn


def test_opponent_roster_found_via_shared_matchup_id():
    conn = make_conn()
    storage.save_matchups(
        conn,
        "league1",
        1,
        [
            {"roster_id": 1, "matchup_id": 5, "points": 100.0, "starters": [], "players": []},
            {"roster_id": 2, "matchup_id": 5, "points": 90.0, "starters": [], "players": []},
            {"roster_id": 3, "matchup_id": 6, "points": 80.0, "starters": [], "players": []},
        ],
    )

    opponent = storage.get_opponent_roster(conn, "league1", 1, 1)

    assert opponent is not None
    assert opponent["roster_id"] == 2


def test_opponent_roster_none_when_no_matchup():
    conn = make_conn()

    opponent = storage.get_opponent_roster(conn, "league1", 1, 99)

    assert opponent is None


def test_player_name_falls_back_for_unknown_id():
    conn = make_conn()

    assert storage.player_name(conn, "999") == "Unknown player (999)"


def test_player_name_formats_position_and_team():
    conn = make_conn()
    storage.save_players(
        conn,
        {"123": {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"}},
    )

    assert storage.player_name(conn, "123") == "Josh Allen (QB/BUF)"
