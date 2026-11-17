"""The per-turn context block: facts the model gets every turn, including the GAPS line."""

from datetime import datetime

from agent.context import build_context
from agent.prompt import system_prompt
from ingestion import sync
from .conftest import KEY, TEST_LEAGUE


def lines(conn, since=None) -> dict:
    text = build_context(conn, TEST_LEAGUE, since, now=datetime(2026, 11, 3, 14, 30).astimezone())
    return {line.split(":")[0]: line for line in text.splitlines() if not line.startswith("  ")}, text


def test_date_season_week_league_format(league, fake):
    head, _ = lines(league)
    week = fake.state["week"]
    assert head["TODAY"].startswith("TODAY: Tuesday 2026-11-03, 14:30 local time")
    assert head["SEASON"] == f"SEASON: 2026. THIS WEEK: {week} (next to be played). LAST COMPLETED WEEK: {week - 1}"
    assert head["LEAGUE"] == ("LEAGUE: Or Test League. MY OTHER LEAGUES: Or 8-Team League (not playable: pre draft); "
                              "FantaSteelers")
    assert "IDP (individual defensive players); full PPR (1 point per reception); superflex" in head["FORMAT"]
    assert head["WAIVERS"] == "WAIVERS: rolling waiver order (no FAAB). TRADE DEADLINE: week 11 (not passed)"
    assert head["GAME WINDOW"] == "GAME WINDOW: closed (no NFL game within 3 hours)"
    assert head["MY FANTASY TEAM"] == "MY FANTASY TEAM: OrMatalon, record 4-0, 1st of 4"
    assert head["THIS WEEK'S OPPONENT"] == "THIS WEEK'S OPPONENT: OrHuji (fantasy team), record 1-3"


def test_my_roster_uses_the_tool_labels(league):
    _, text = lines(league)
    assert "  QB: Josh Allen, QB, NFL TEAM: BUF, MY ROSTER (starter, QB)" in text
    assert "  SUPER_FLEX: Christian Watson, WR, NFL TEAM: GB, MY ROSTER (starter, SUPER_FLEX), injury: Questionable" in text


def test_gaps_are_facts_in_the_context(league):
    head, _ = lines(league)
    for gap in ("inactives are known only after ESPN marks them", "no per-day history", "guillotine leagues cannot be detected",
                "pending waiver claims and open trade offers not available"):
        assert gap in head["GAPS"]


def test_recent_changes_and_refresh_failed(league, fake):
    before = lines(league)[0]
    assert "RECENT CHANGES TO MY ROSTER" not in before and "REFRESH FAILED" not in before
    mine = fake.rosters[0]
    mine["players"] = [p for p in mine["players"] if p != "4866"] + ["12526"]
    sync.refresh_league(league, TEST_LEAGUE)
    head, _ = lines(league)
    assert "added Tetairoa McMillan; dropped Saquon Barkley" in head["RECENT CHANGES TO MY ROSTER"]
    assert "RECENT CHANGES TO MY ROSTER" not in lines(league, since="2999-01-01T00:00:00Z")[0]
    fake.down = True
    sync.refresh_league(league, TEST_LEAGUE)
    assert lines(league)[0]["REFRESH FAILED"].startswith("REFRESH FAILED: Could not refresh from Sleeper. Using data from")


def test_no_key_or_user_id_in_context_or_prompt(league):
    _, text = lines(league)
    for secret in (KEY, "sk-or", "1268547099817943040"):
        assert secret not in text and secret not in system_prompt()


def test_system_prompt_states_the_two_kinds_of_team():
    p = system_prompt()
    assert "A fantasy team is a manager's roster" in p and "An NFL team is a real" in p
    assert 'Never write the word "team" alone' in p
