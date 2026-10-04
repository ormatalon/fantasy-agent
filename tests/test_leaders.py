import sqlite3

import pandas as pd

from src.decisions.leaders import season_to_date
from src.ingestion import storage

STATS = pd.DataFrame([
    # player_id is nflverse's gsis id; week 4 is not complete yet.
    {"player_id": "G-RB1", "position": "RB", "week": 1, "fantasy_points": 10.0, "receptions": 2},
    {"player_id": "G-RB1", "position": "RB", "week": 2, "fantasy_points": 20.0, "receptions": 3},
    {"player_id": "G-RB1", "position": "RB", "week": 4, "fantasy_points": 99.0, "receptions": 0},
    {"player_id": "G-RB2", "position": "RB", "week": 1, "fantasy_points": 25.0, "receptions": 0},
    {"player_id": "G-WR1", "position": "WR", "week": 1, "fantasy_points": 50.0, "receptions": 10},
    {"player_id": "G-NONE", "position": "RB", "week": 1, "fantasy_points": 80.0, "receptions": 0},
])


def seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    storage.save_players(conn, {
        "rb1": {"first_name": "Run", "last_name": "One", "position": "RB", "team": "DET", "gsis_id": "G-RB1"},
        "rb2": {"first_name": "Run", "last_name": "Two", "position": "RB", "team": "ATL", "gsis_id": "G-RB2",
                "injury_status": "Out"},
        "wr1": {"first_name": "Wide", "last_name": "One", "position": "WR", "team": "MIA", "gsis_id": "G-WR1"},
    })
    return conn


def test_totals_use_league_scoring_and_only_completed_weeks():
    leaders = season_to_date(seeded_conn(), "2026", 3, {"rec": 1.0}, position="RB", stats=STATS)

    # Run One: 10 + 20 + 5 receptions = 35 over 2 games; week 4 excluded.
    assert [(lead.name, lead.total, lead.games) for lead in leaders] == [
        ("Run One", 35.0, 2),
        ("Run Two", 25.0, 1),
    ]
    assert leaders[1].injury_status == "Out"


def test_players_missing_from_the_catalog_are_skipped_and_position_filters():
    leaders = season_to_date(seeded_conn(), "2026", 3, {"rec": 0.0}, stats=STATS)
    # No PPR: WR 50, Run One 30, Run Two 25; the uncatalogued 80-pt RB is dropped.
    assert [lead.player_id for lead in leaders] == ["wr1", "rb1", "rb2"]
