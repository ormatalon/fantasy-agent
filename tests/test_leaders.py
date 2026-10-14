import sqlite3

from src.decisions.leaders import season_to_date
from src.ingestion import storage


def row(pid, gp=1, **stats):
    return {"player_id": pid, "stats": {"gp": gp, **stats}}


# Sleeper's weekly stats; week 4 is not complete yet.
WEEKS = {
    1: [row("rb1", rush_yd=100, rec=2), row("rb2", rush_yd=250), row("wr1", rec_yd=500, rec=10),
        row("lb1", idp_tkl=10), row("none", rush_yd=800)],
    2: [row("rb1", rush_yd=200, rec=3), row("rb2", gp=0)],
    3: [],
    4: [row("rb1", rush_yd=990)],
}
SCORING = {"rush_yd": 0.1, "rec_yd": 0.1, "rec": 1.0, "idp_tkl": 1.0}


def seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    storage.save_players(conn, {
        "rb1": {"first_name": "Run", "last_name": "One", "position": "RB", "team": "DET"},
        "rb2": {"first_name": "Run", "last_name": "Two", "position": "RB", "team": "ATL", "injury_status": "Out"},
        "wr1": {"first_name": "Wide", "last_name": "One", "position": "WR", "team": "MIA"},
        "lb1": {"first_name": "Line", "last_name": "Backer", "position": "OLB", "team": "SEA"},
    })
    return conn


def test_totals_use_league_scoring_and_only_completed_weeks():
    leaders = season_to_date(seeded_conn(), "2026", 3, SCORING, position="RB", weekly_stats=WEEKS)

    # Run One: 10 + 20 yards pts + 5 receptions = 35 over 2 games; week 4 excluded.
    # Run Two was inactive in week 2 (gp 0), so that week isn't a game.
    assert [(lead.name, round(lead.total, 1), lead.games) for lead in leaders] == [
        ("Run One", 35.0, 2),
        ("Run Two", 25.0, 1),
    ]
    assert leaders[1].injury_status == "Out"


def test_players_missing_from_the_catalog_are_skipped_and_idp_is_scored():
    leaders = season_to_date(seeded_conn(), "2026", 3, SCORING, weekly_stats=WEEKS)
    # WR 60, Run One 35, Run Two 25, LB 10; the uncatalogued 80-pt RB is dropped.
    assert [lead.player_id for lead in leaders] == ["wr1", "rb1", "rb2", "lb1"]


def test_position_group_matches_granular_idp_positions():
    leaders = season_to_date(seeded_conn(), "2026", 3, SCORING, position="LB", weekly_stats=WEEKS)
    assert [lead.player_id for lead in leaders] == ["lb1"]
