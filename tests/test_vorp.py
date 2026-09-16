from src.decisions.vorp import compute_replacement_levels, rank_by_vorp
from src.projections.engine import AdjustedProjection


def make_proj(player_id: str, position: str, mean: float) -> AdjustedProjection:
    return AdjustedProjection(
        player_id=player_id, name=player_id, position=position, team=None,
        mean=mean, variance=1.0, matchup_multiplier=1.0, injury_multiplier=1.0, num_sources=1,
    )


def test_direct_slot_demand_uses_num_teams_times_slot_count():
    # 10 teams, 1 RB slot each -> replacement rank 10 -> 10th-best RB projection
    levels = compute_replacement_levels(
        roster_positions=["RB", "BN"],
        num_teams=10,
        projections_by_position={"RB": [30, 25, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2]},
    )
    assert levels["RB"] == 6  # 10th-highest value


def test_flex_slot_splits_demand_across_eligible_positions():
    # 1 team, 1 FLEX slot (RB/WR/TE) -> each position gets 1/3 demand share,
    # rounding to nearest whole player -> rank 1 (best) at each position.
    levels = compute_replacement_levels(
        roster_positions=["FLEX"],
        num_teams=1,
        projections_by_position={"RB": [10, 5], "WR": [12, 6], "TE": [8, 3]},
    )
    assert levels["RB"] == 10
    assert levels["WR"] == 12
    assert levels["TE"] == 8


def test_idp_granular_positions_pool_into_one_shared_baseline():
    # DL slot accepts DE/DT/NT identically - a rostered pool split across
    # those granular tags should pool into one "DL" baseline, not three
    # separate (mostly empty) ones.
    levels = compute_replacement_levels(
        roster_positions=["DL"],
        num_teams=2,
        projections_by_position={"DE": [10, 6], "DT": [9], "NT": [4]},
    )
    # pooled + sorted desc: [10, 9, 6, 4]; demand = 2 teams * 1.0 -> rank 2 -> 9
    assert levels["DL"] == 9


def test_replacement_level_zero_when_no_players_at_position():
    levels = compute_replacement_levels(roster_positions=["K"], num_teams=5, projections_by_position={})
    assert levels["K"] == 0.0


def test_rank_by_vorp_excludes_positions_with_no_roster_demand():
    # DEF has no entry in replacement_levels (e.g. an IDP league with no DEF
    # slot) - it must be dropped, not scored against an implicit 0.0 baseline.
    candidates = [make_proj("def1", "DEF", 12.0), make_proj("rb1", "RB", 10.0)]
    replacement_levels = {"RB": 8.0}

    ranked = rank_by_vorp(candidates, replacement_levels)

    assert [p.player_id for p, _vor in ranked] == ["rb1"]


def test_rank_by_vorp_orders_by_value_over_baseline():
    candidates = [make_proj("a", "RB", 12.0), make_proj("b", "WR", 25.0), make_proj("c", "RB", 8.0)]
    replacement_levels = {"RB": 10.0, "WR": 18.0}

    ranked = rank_by_vorp(candidates, replacement_levels)

    assert [p.player_id for p, _vor in ranked] == ["b", "a", "c"]
    assert ranked[0][1] == 7.0  # WR: 25 - 18
    assert ranked[1][1] == 2.0  # RB: 12 - 10
    assert ranked[2][1] == -2.0  # RB: 8 - 10
