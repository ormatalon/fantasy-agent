from src.projections.engine import REGULAR_SEASON_WEEKS, rest_of_season_fraction


def test_week_one_is_the_whole_season():
    assert rest_of_season_fraction(1) == 1.0


def test_mid_season_prorates_by_weeks_remaining():
    # Week 10 of 18: weeks 10..18 = 9 weeks left.
    assert rest_of_season_fraction(10) == 9 / REGULAR_SEASON_WEEKS


def test_past_the_regular_season_is_zero():
    assert rest_of_season_fraction(REGULAR_SEASON_WEEKS + 1) == 0.0
