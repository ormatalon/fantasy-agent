import sqlite3

from src.decisions.waivers import suggest_waivers_by_position
from src.ingestion import storage
from src.projections.engine import AdjustedProjection


def proj(pid: str, mean: float) -> AdjustedProjection:
    return AdjustedProjection(pid, pid, "TE", "KC", mean, 4.0, 1.0, 1.0, 1)


def league_with_rostered(*rostered: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    storage.save_rosters(conn, "L1", [
        {"roster_id": i, "owner_id": f"u{i}", "players": [pid], "starters": [pid]}
        for i, pid in enumerate(rostered, start=1)
    ])
    return conn


def test_injured_rostered_players_do_not_drag_replacement_level_to_zero():
    # 3 teams x 1 TE slot -> replacement is the 3rd-best rostered TE. The 3rd
    # is injured (0.0); the bar a pickup must clear is the healthy 8.0.
    conn = league_with_rostered("te_a", "te_b", "te_hurt")
    table = [proj("te_a", 12.0), proj("te_b", 8.0), proj("te_hurt", 0.0), proj("fa", 9.0)]

    grouped = suggest_waivers_by_position(conn, "L1", ["TE", "BN"], table)

    fa = grouped["TE"][0]
    assert fa.proj.player_id == "fa"
    assert fa.vor == 1.0


def test_multi_week_bids_are_sized_on_per_week_value():
    conn = league_with_rostered("te_a")
    table = [proj("te_a", 50.0), proj("fa", 90.0)]  # +40 VOR over 10 weeks = +4/week

    weekly = suggest_waivers_by_position(conn, "L1", ["TE"], table)["TE"][0]
    season = suggest_waivers_by_position(conn, "L1", ["TE"], table, weeks=10)["TE"][0]

    assert weekly.bid_pct == 100
    assert season.bid_pct == 16


def test_excluded_players_are_not_suggested():
    conn = league_with_rostered("te_a")
    table = [proj("te_a", 12.0), proj("played", 11.0), proj("fa", 9.0)]

    grouped = suggest_waivers_by_position(conn, "L1", ["TE"], table, exclude_ids={"played"})

    assert [s.proj.player_id for s in grouped["TE"]] == ["fa"]
