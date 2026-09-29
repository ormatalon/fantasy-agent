from datetime import date

from src.agent.prompts import build_system_prompt, describe_format

IDP_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DL", "LB", "DB", "BN", "BN", "IR"]
STANDARD_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]


def prompt(slots, scoring=None, others=None):
    return build_system_prompt(
        season="2026", week=4, league_name="Test League", team_name="My Team",
        roster_positions=slots, scoring_settings=scoring or {"rec": 1.0},
        other_league_names=others, today=date(2026, 9, 29),
    )


def test_prompt_states_date_season_and_week_so_the_model_cannot_guess_them():
    text = prompt(IDP_SLOTS)
    assert "2026-09-29" in text
    assert "season is 2026" in text
    assert "week 4" in text
    assert "Never claim it is a different year" in text


def test_idp_is_described_only_for_idp_leagues():
    assert "IDP" in prompt(IDP_SLOTS)
    standard = prompt(STANDARD_SLOTS)
    assert "IDP" not in standard
    assert "team defense" in standard


def test_scoring_format_comes_from_league_settings():
    assert "full PPR" in describe_format(STANDARD_SLOTS, {"rec": 1.0})
    assert "half PPR" in describe_format(STANDARD_SLOTS, {"rec": 0.5})
    assert "standard" in describe_format(STANDARD_SLOTS, {})
    assert "superflex" in describe_format(STANDARD_SLOTS + ["SUPER_FLEX"], {"rec": 1.0})


def test_slots_are_summarised_with_counts():
    text = prompt(IDP_SLOTS)
    assert "2x RB" in text
    assert "2 bench" in text


def test_other_leagues_are_named_with_how_to_switch():
    assert "switch_league" in prompt(IDP_SLOTS, others=["Work League"])
    assert "Work League" in prompt(IDP_SLOTS, others=["Work League"])
    assert "switch_league" not in prompt(IDP_SLOTS)
