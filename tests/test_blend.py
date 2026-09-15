from src.projections.blend import blend
from src.projections.sources.base import SourceProjection


def test_single_source_has_no_variance():
    result = blend(
        {"sleeper": [SourceProjection(player_id="1", points=20.0)]},
        weights={"sleeper": 1.0},
    )

    assert result["1"].mean == 20.0
    assert result["1"].variance is None
    assert result["1"].num_sources == 1


def test_two_sources_weighted_mean_and_variance():
    result = blend(
        {
            "sleeper": [SourceProjection(player_id="1", points=20.0)],
            "nflverse": [SourceProjection(player_id="1", points=10.0)],
        },
        weights={"sleeper": 1.0, "nflverse": 1.0},
    )

    assert result["1"].mean == 15.0
    assert result["1"].variance == 25.0  # equal weights, +/-5 from mean each
    assert result["1"].num_sources == 2


def test_zero_weight_source_is_excluded():
    result = blend(
        {
            "sleeper": [SourceProjection(player_id="1", points=20.0)],
            "nflverse": [SourceProjection(player_id="1", points=1000.0)],
        },
        weights={"sleeper": 1.0, "nflverse": 0},
    )

    assert result["1"].mean == 20.0
    assert result["1"].num_sources == 1
