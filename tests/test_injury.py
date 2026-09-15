import sqlite3

from src.ingestion import storage
from src.projections import injury


def test_designation_multiplier_out_zeroes_projection():
    assert injury.get_designation_multiplier("Out") == 0.0


def test_designation_multiplier_questionable_small_discount():
    assert 0.0 < injury.get_designation_multiplier("Questionable") < 1.0


def test_designation_multiplier_healthy_is_neutral():
    assert injury.get_designation_multiplier(None) == 1.0


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    return conn


def test_backup_bumped_when_starter_ruled_out():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO players (player_id, first_name, last_name, position, team, "
        "injury_status, depth_chart_position, depth_chart_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("1", "Starter", "RB", "RB", "DAL", "Out", "RB", 1),
            ("2", "Backup", "RB", "RB", "DAL", None, "RB", 2),
        ],
    )
    conn.commit()

    bumps = injury.compute_backup_bumps(conn)

    assert bumps == {"2": injury.BACKUP_BUMP}


def test_no_bump_when_starter_healthy():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO players (player_id, first_name, last_name, position, team, "
        "injury_status, depth_chart_position, depth_chart_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("1", "Starter", "RB", "RB", "DAL", None, "RB", 1),
            ("2", "Backup", "RB", "RB", "DAL", None, "RB", 2),
        ],
    )
    conn.commit()

    assert injury.compute_backup_bumps(conn) == {}
