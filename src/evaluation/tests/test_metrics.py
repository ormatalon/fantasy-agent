"""Backtest metrics on small hand-checked numbers; the backtest itself is run by `python -m evaluation.backtest`."""

import pytest

from evaluation.backtest import tier
from evaluation.metrics import calibration, paired_abs_diff, summarize


def test_summarize():
    s = summarize([(10.0, 8.0), (5.0, 9.0), (7.0, 7.0)])
    assert s["n"] == 3 and s["mae"] == pytest.approx(2.0) and s["bias"] == pytest.approx(-2 / 3)
    assert s["rmse"] == pytest.approx((20 / 3) ** 0.5)


def test_paired_difference_is_negative_when_the_candidate_is_closer():
    d = paired_abs_diff([9.0, 6.0, 8.0], [12.0, 3.0, 8.0], [8.0, 9.0, 7.0])
    assert d["mean_diff"] == pytest.approx(((1 - 4) + (3 - 6) + (1 - 1)) / 3)
    assert d["ci95"][0] < d["mean_diff"] < d["ci95"][1]


def test_calibration_ratio_one_when_variance_equals_squared_error():
    rows = [{"position": "RB", "pred": 10.0, "variance": 4.0, "actual": a} for a in (8.0, 12.0, 12.0, 8.0)]
    assert calibration(rows)["RB"]["ratio"] == pytest.approx(1.0)
    assert calibration(rows)["RB"]["within_1sd"] == 1.0


def test_tier_reports_change_against_the_baseline():
    rows = [{"position": "QB", "actual": 10.0, "a": 12.0, "b": 11.0}, {"position": "QB", "actual": 20.0, "a": 16.0, "b": 17.0},
            {"position": "WR", "actual": 5.0, "a": 4.0, "b": 4.5}]  # fmt: skip
    t = tier("b", rows, "b", "a")
    assert t["mae"] == pytest.approx((1 + 3 + 0.5) / 3) and t["mae_vs_baseline_pct"] == pytest.approx(100 * (1.5 / (7 / 3) - 1))
    assert set(t["by_position"]) == {"QB", "WR"}
