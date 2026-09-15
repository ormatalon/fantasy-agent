from src.projections import matchup


def test_multiplier_found_for_known_opponent():
    multipliers = {("KC", "RB"): 1.35}
    team_opponents = {"DAL": "KC"}

    result = matchup.get_multiplier(multipliers, team_opponents, team="DAL", position="RB")

    assert result == 1.35


def test_multiplier_neutral_when_no_opponent_scheduled():
    result = matchup.get_multiplier({}, {}, team="DAL", position="RB")

    assert result == matchup.NEUTRAL_MULTIPLIER


def test_multiplier_neutral_when_position_not_in_data():
    multipliers = {("KC", "WR"): 1.35}
    team_opponents = {"DAL": "KC"}

    result = matchup.get_multiplier(multipliers, team_opponents, team="DAL", position="RB")

    assert result == matchup.NEUTRAL_MULTIPLIER
