from src.decisions.lineup import optimize_lineup
from src.projections.engine import AdjustedProjection


def make_proj(player_id: str, mean: float) -> AdjustedProjection:
    return AdjustedProjection(
        player_id=player_id, name=player_id, position=None, team=None,
        mean=mean, variance=1.0, matchup_multiplier=1.0, injury_multiplier=1.0, num_sources=1,
    )


def test_fills_required_slot_with_only_eligible_player():
    roster_positions = ["QB", "BN"]
    roster_player_ids = ["qb1", "rb1"]
    projections = {"qb1": make_proj("qb1", 20.0), "rb1": make_proj("rb1", 25.0)}
    player_meta = {"qb1": {"name": "QB One", "position": "QB"}, "rb1": {"name": "RB One", "position": "RB"}}

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    assert len(lineup) == 1
    assert lineup[0].slot == "QB"
    assert lineup[0].player_id == "qb1"


def test_flex_slot_picks_highest_projected_eligible_player():
    roster_positions = ["FLEX"]
    roster_player_ids = ["rb1", "wr1", "te1"]
    projections = {"rb1": make_proj("rb1", 8.0), "wr1": make_proj("wr1", 15.0), "te1": make_proj("te1", 6.0)}
    player_meta = {
        "rb1": {"name": "RB", "position": "RB"},
        "wr1": {"name": "WR", "position": "WR"},
        "te1": {"name": "TE", "position": "TE"},
    }

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    assert lineup[0].player_id == "wr1"


def test_idp_slot_accepts_granular_position():
    roster_positions = ["DB"]
    roster_player_ids = ["cb1"]
    projections = {"cb1": make_proj("cb1", 9.0)}
    player_meta = {"cb1": {"name": "Corner", "position": "CB"}}

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    assert lineup[0].player_id == "cb1"


def test_empty_slot_when_no_eligible_player():
    roster_positions = ["K"]
    roster_player_ids = ["qb1"]
    projections = {"qb1": make_proj("qb1", 20.0)}
    player_meta = {"qb1": {"name": "QB One", "position": "QB"}}

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    assert lineup[0].player_id is None
    assert lineup[0].slot == "K"


def test_optimizer_maximizes_total_over_greedy_trap():
    # rb1 is the best RB but also the only player eligible for FLEX alongside
    # a worse option; a greedy fill-strict-slots-first pass could strand
    # points on the table. Two RB slots + one FLEX, three RBs of varying value.
    roster_positions = ["RB", "RB", "FLEX"]
    roster_player_ids = ["rb1", "rb2", "rb3"]
    projections = {"rb1": make_proj("rb1", 20.0), "rb2": make_proj("rb2", 15.0), "rb3": make_proj("rb3", 10.0)}
    player_meta = {pid: {"name": pid, "position": "RB"} for pid in roster_player_ids}

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    assert {s.player_id for s in lineup} == {"rb1", "rb2", "rb3"}
    assert sum(s.mean for s in lineup) == 45.0
