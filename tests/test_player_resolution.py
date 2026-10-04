import sqlite3

from src.ingestion import storage


def seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    storage.save_players(conn, {
        "qb": {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"},
        "lb": {"first_name": "Josh", "last_name": "Allen", "position": "LB", "team": "JAX"},
        "wr1": {"first_name": "Mike", "last_name": "Williams", "position": "WR", "team": "PIT"},
        "wr2": {"first_name": "Garrett", "last_name": "Williams", "position": "CB", "team": "ARI"},
        "old": {"first_name": "Ricky", "last_name": "Williams", "position": "RB", "team": None},
        "one": {"first_name": "Puka", "last_name": "Nacua", "position": "WR", "team": "LAR"},
    })
    storage.save_rosters(conn, "L1", [{"roster_id": 1, "owner_id": "u1", "players": ["wr2"], "starters": []}])
    return conn


def test_unique_partial_name_resolves():
    pid, _ = storage.resolve_player(seeded_conn(), "nacua")
    assert pid == "one"


def test_ambiguous_surname_returns_candidates_not_a_guess():
    pid, candidates = storage.resolve_player(seeded_conn(), "Williams", league_id="L1")
    assert pid is None
    assert set(candidates) == {"wr1", "wr2", "old"}
    # Rostered in my league first, retired/teamless last.
    assert candidates[0] == "wr2"
    assert candidates[-1] == "old"


def test_two_players_with_the_same_full_name_stay_ambiguous():
    pid, candidates = storage.resolve_player(seeded_conn(), "Josh Allen")
    assert pid is None
    assert set(candidates) == {"qb", "lb"}


def test_the_listed_name_and_tag_form_disambiguates():
    conn = seeded_conn()
    # Exactly the form player_name() prints in a candidate list.
    assert storage.resolve_player(conn, storage.player_name(conn, "lb"))[0] == "lb"
    assert storage.resolve_player(conn, "Josh Allen (QB/BUF)")[0] == "qb"


def test_exact_full_name_beats_substring_matches():
    conn = seeded_conn()
    storage.save_players(conn, {"wr3": {"first_name": "Mike", "last_name": "Williamson", "position": "TE", "team": "SEA"}})
    assert storage.resolve_player(conn, "Mike Williams")[0] == "wr1"


def test_no_match():
    assert storage.resolve_player(seeded_conn(), "Nobody Here") == (None, [])
