from src.decisions.draft import (
    DraftState,
    is_my_turn,
    pick_number_to_slot,
    suggest_pick,
    unfilled_starting_slots,
)
from src.projections.engine import AdjustedProjection


def test_snake_draft_round_1_is_straight_order():
    # 8 teams, round 1: pick 1 -> slot 1, pick 8 -> slot 8
    assert pick_number_to_slot(1, teams=8, is_snake=True) == 1
    assert pick_number_to_slot(8, teams=8, is_snake=True) == 8


def test_snake_draft_round_2_is_reversed():
    # 8 teams, round 2 (picks 9-16): pick 9 -> slot 8, pick 16 -> slot 1
    assert pick_number_to_slot(9, teams=8, is_snake=True) == 8
    assert pick_number_to_slot(16, teams=8, is_snake=True) == 1


def test_linear_draft_never_reverses():
    assert pick_number_to_slot(9, teams=8, is_snake=False) == 1
    assert pick_number_to_slot(16, teams=8, is_snake=False) == 8


def test_is_my_turn_true_when_next_pick_matches_slot():
    state = DraftState(draft_id="d1", teams=8, rounds=15, is_snake=True, my_slot=8)
    # 8 picks made (round 1 complete) -> pick 9 is slot 8 (start of the snake-back)
    assert is_my_turn(state, picks_made=8) is True


def test_is_my_turn_false_when_slot_unknown():
    state = DraftState(draft_id="d1", teams=8, rounds=15, is_snake=True, my_slot=None)
    assert is_my_turn(state, picks_made=0) is False


def make_proj(player_id: str, position: str, mean: float) -> AdjustedProjection:
    return AdjustedProjection(
        player_id=player_id, name=player_id, position=position, team=None,
        mean=mean, variance=1.0, matchup_multiplier=1.0, injury_multiplier=1.0, num_sources=1,
    )


def test_unfilled_slots_fill_specific_slots_before_flex():
    slots = ["QB", "RB", "RB", "FLEX", "BN"]
    assert unfilled_starting_slots(slots, ["RB", "RB"]) == ["QB", "FLEX"]
    assert unfilled_starting_slots(slots, ["RB", "RB", "RB"]) == ["QB"]
    assert unfilled_starting_slots(slots, ["RB", "RB", "RB", "RB"]) == ["QB"]  # 4th RB is depth


def test_need_weighting_prefers_an_empty_qb_slot_over_a_fourth_rb():
    slots = ["QB", "RB", "RB", "FLEX", "BN", "BN"]
    # 2 teams: QB replacement = 2nd-best QB (10) -> qb1 VOR +8; RB replacement
    # = worst RB (5) -> rb4 VOR +13, halved to +6.5 as depth behind 3 RBs.
    available = [make_proj("rb4", "RB", 18.0), make_proj("qb1", "QB", 18.0)] + [
        make_proj(f"filler_rb{i}", "RB", 5.0) for i in range(3)
    ] + [make_proj("filler_qb", "QB", 10.0)]

    pure = suggest_pick(available, slots, num_teams=2, drafted_ids=set())
    needy = suggest_pick(available, slots, num_teams=2, drafted_ids=set(), my_positions=["RB", "RB", "RB"])

    assert pure[0][0].player_id == "rb4"
    assert needy[0][0].player_id == "qb1"


def test_suggest_pick_excludes_drafted_players():
    available = [make_proj("a", "RB", 20.0), make_proj("b", "RB", 15.0)]

    ranked = suggest_pick(available, roster_positions=["RB"], num_teams=1, drafted_ids={"a"})

    assert [p.player_id for p, _vor in ranked] == ["b"]
