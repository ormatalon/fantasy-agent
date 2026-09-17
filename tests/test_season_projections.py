from src.projections import injury


def test_week_specific_designations_do_not_discount_a_season_total():
    # Questionable this week says nothing about a full-season outlook.
    assert injury.get_season_designation_multiplier("Questionable") == 1.0
    assert injury.get_season_designation_multiplier("Out") == 1.0
    assert injury.get_season_designation_multiplier("Doubtful") == 1.0


def test_season_long_designations_do_discount_a_season_total():
    assert injury.get_season_designation_multiplier("IR") == 0.0
    assert injury.get_season_designation_multiplier("PUP") == 0.0
    assert injury.get_season_designation_multiplier("Sus") == 0.0


def test_healthy_player_is_neutral_over_a_season():
    assert injury.get_season_designation_multiplier(None) == 1.0


def test_weekly_and_season_overlays_disagree_where_they_should():
    # The whole point of the split: "Out" zeroes a week but not a season.
    assert injury.get_designation_multiplier("Out") == 0.0
    assert injury.get_season_designation_multiplier("Out") == 1.0
