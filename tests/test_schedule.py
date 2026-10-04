from datetime import datetime, timezone

import pandas as pd

from src.decisions.waivers import unavailable_this_week
from src.ingestion import schedule
from src.projections.engine import AdjustedProjection

GAMES = pd.DataFrame([
    # Thursday night: kicks off 2026-10-01 20:15 ET = 2026-10-02 00:15 UTC.
    {"season": 2026, "game_type": "REG", "week": 4, "gameday": "2026-10-01", "gametime": "20:15",
     "away_team": "SF", "home_team": "LA"},
    {"season": 2026, "game_type": "REG", "week": 4, "gameday": "2026-10-04", "gametime": "13:00",
     "away_team": "BUF", "home_team": "MIA"},
    {"season": 2026, "game_type": "REG", "week": 5, "gameday": "2026-10-08", "gametime": "20:15",
     "away_team": "KC", "home_team": "DEN"},
    {"season": 2025, "game_type": "REG", "week": 4, "gameday": "2025-09-28", "gametime": "13:00",
     "away_team": "KC", "home_team": "DEN"},
])
FRIDAY = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def proj(pid, team):
    return AdjustedProjection(pid, pid, "WR", team, 10.0, 4.0, 1.0, 1.0, 1)


def test_kickoffs_are_utc_and_use_sleeper_team_codes():
    kickoffs = schedule.kickoffs_from_frame(GAMES, "2026", 4)
    assert kickoffs["LAR"] == datetime(2026, 10, 2, 0, 15, tzinfo=timezone.utc)
    assert "LA" not in kickoffs
    assert set(kickoffs) == {"SF", "LAR", "BUF", "MIA"}


def test_game_status_played_upcoming_bye():
    kickoffs = schedule.kickoffs_from_frame(GAMES, "2026", 4)
    assert schedule.game_status("SF", kickoffs, FRIDAY) == schedule.PLAYED
    assert schedule.game_status("BUF", kickoffs, FRIDAY) == schedule.UPCOMING
    assert schedule.game_status("KC", kickoffs, FRIDAY) == schedule.BYE


def test_unknown_when_no_team_or_no_schedule():
    kickoffs = schedule.kickoffs_from_frame(GAMES, "2026", 4)
    assert schedule.game_status(None, kickoffs, FRIDAY) is None
    assert schedule.game_status("SF", {}, FRIDAY) is None


def test_players_who_cannot_score_this_week_are_flagged():
    kickoffs = schedule.kickoffs_from_frame(GAMES, "2026", 4)
    table = [proj("played", "SF"), proj("upcoming", "BUF"), proj("bye", "KC"), proj("fa", None)]
    assert unavailable_this_week(table, kickoffs, FRIDAY) == {"played": "played", "bye": "bye"}
