import sqlite3

import pandas as pd

from src.ingestion import storage
from src.ingestion.id_crosswalk import _normalize, build_crosswalk, map_nflverse_ids


def seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    storage.save_players(conn, {
        "vet": {"first_name": "Derrick", "last_name": "Henry", "position": "RB", "team": "BAL", "gsis_id": "G-1"},
        "kw": {"first_name": "Kenneth", "last_name": "Walker", "position": "RB", "team": "KC"},
        "ds": {"first_name": "D'Andre", "last_name": "Swift", "position": "RB", "team": "CHI"},
        "rams": {"first_name": "Puka", "last_name": "Nacua", "position": "WR", "team": "LAR"},
        "ja_qb": {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"},
        "ja_lb": {"first_name": "Josh", "last_name": "Allen", "position": "LB", "team": "JAX"},
        "retired": {"first_name": "Solo", "last_name": "Name", "position": "RB", "team": None},
        "active": {"first_name": "Solo", "last_name": "Name", "position": "WR", "team": "SEA"},
    })
    return conn


def test_normalize_strips_suffixes_and_punctuation():
    assert _normalize("Kenneth Walker III") == _normalize("Kenneth Walker")
    assert _normalize("D'Andre Swift") == "dandreswift"
    assert _normalize("A.J. Brown") == _normalize("AJ Brown")


def test_gsis_first_then_name_fallbacks():
    cw = build_crosswalk(seeded_conn())
    assert cw.resolve("G-1", "Wrong Name", None, None) == "vet"
    # Missing gsis_id and a team change since the catalog was built: still unique by name+position.
    assert cw.resolve("G-X", "Kenneth Walker III", "SEA", "RB") == "kw"
    # nflverse "LA" is Sleeper "LAR".
    assert cw.resolve("G-Y", "Puka Nacua", "LA", "WR") == "rams"


def test_shared_names_are_split_by_position_and_never_guessed():
    cw = build_crosswalk(seeded_conn())
    assert cw.resolve(None, "Josh Allen", None, "QB") == "ja_qb"
    assert cw.resolve(None, "Josh Allen", None, "K") is None


def test_retired_namesakes_do_not_block_an_active_match():
    cw = build_crosswalk(seeded_conn())
    assert cw.resolve(None, "Solo Name", "SEA", "TE") == "active"


def test_map_nflverse_ids_uses_each_players_latest_row():
    df = pd.DataFrame([
        {"player_id": "G-X", "player_display_name": "Kenneth Walker III", "recent_team": "SEA", "position": "RB", "week": 1},
        {"player_id": "G-X", "player_display_name": "Kenneth Walker III", "recent_team": "KC", "position": "RB", "week": 3},
        {"player_id": "G-Z", "player_display_name": "Nobody Known", "recent_team": "KC", "position": "RB", "week": 3},
    ])
    assert map_nflverse_ids(build_crosswalk(seeded_conn()), df) == {"G-X": "kw"}
