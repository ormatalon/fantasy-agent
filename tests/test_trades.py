from src.decisions.trades import evaluate_trade
from src.projections.engine import AdjustedProjection


def make_proj(player_id: str, mean: float) -> AdjustedProjection:
    return AdjustedProjection(
        player_id=player_id, name=player_id, position="RB", team=None,
        mean=mean, variance=1.0, matchup_multiplier=1.0, injury_multiplier=1.0, num_sources=1,
    )


def test_favorable_trade():
    projections = {"give1": make_proj("give1", 10.0), "recv1": make_proj("recv1", 15.0)}

    result = evaluate_trade(["give1"], ["recv1"], projections)

    assert result.delta == 5.0
    assert result.verdict == "favorable"


def test_unfavorable_trade():
    projections = {"give1": make_proj("give1", 15.0), "recv1": make_proj("recv1", 10.0)}

    result = evaluate_trade(["give1"], ["recv1"], projections)

    assert result.delta == -5.0
    assert result.verdict == "unfavorable"


def test_roughly_even_trade_within_threshold():
    projections = {"give1": make_proj("give1", 10.0), "recv1": make_proj("recv1", 10.5)}

    result = evaluate_trade(["give1"], ["recv1"], projections)

    assert result.verdict == "roughly even"


def test_multi_player_trade_sums_both_sides():
    projections = {
        "g1": make_proj("g1", 10.0),
        "g2": make_proj("g2", 5.0),
        "r1": make_proj("r1", 12.0),
    }

    result = evaluate_trade(["g1", "g2"], ["r1"], projections)

    assert result.give_total == 15.0
    assert result.receive_total == 12.0
    assert result.delta == -3.0
