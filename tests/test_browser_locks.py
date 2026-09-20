"""A player whose game has kicked off cannot be moved. Getting this wrong
means proposing a change the human approves and Sleeper then refuses, which
is exactly what happened in live use before this was handled.
"""

import pytest

from src.execution.browser import _lock_reason, is_locked, surname


@pytest.mark.parametrize("row_text", [
    "WR\nD Samuel\nWR - SF\n(8)\nFinal W 41-31 vs DET\n22.00",
    "QB\nM Stafford\nQB - LAR\nFinal L 10-20 vs NYG",
    "RB\nS Barkley\nRB - PHI\nQ3 10:32 vs TEN",
    "TE\nT Kelce\nTE - KC\nHalf vs CIN",
])
def test_started_games_are_locked(row_text):
    assert is_locked(row_text)


@pytest.mark.parametrize("row_text", [
    "QB\nA Rodgers\nQB - PIT\n(5)\nSun 8:00 PM\nvs MIN\n14.90",
    "WR\nJ Williams\nWR - DET\nTue 3:15 AM\nvs NYG",
    "K\nB Aubrey\nK - DAL\nMon 11:05 PM",
    "BN\nEmpty",
])
def test_upcoming_games_are_not_locked(row_text):
    assert not is_locked(row_text)


def test_lock_reason_distinguishes_finished_from_in_progress():
    assert "finished" in _lock_reason("Final W 41-31 vs DET")
    assert "in progress" in _lock_reason("Q3 10:32 vs TEN")


def test_lock_reason_falls_back_without_claiming_to_know_why():
    # If Sleeper declines a swap for some other reason, say so plainly
    # rather than inventing an explanation.
    reason = _lock_reason("Sun 8:00 PM vs MIN")
    assert "not offered" in reason


def test_surname_matching_survives_sleeper_abbreviations():
    # Sleeper renders "D Swift" where our projections say "D'Andre Swift".
    assert surname("D Swift") == surname("D'Andre Swift") == "swift"
    assert surname("Aaron Rodgers") == surname("A Rodgers") == "rodgers"
    assert surname("A Jones") != surname("J Williams")
