from src.decisions.waivers import unavailable_this_week
from src.ingestion import schedule
from src.projections.engine import AdjustedProjection

GAMES = [
    {"week": 4, "away": "SF", "home": "LAR", "status": "complete", "date": "2026-10-01", "game_id": "1"},
    {"week": 4, "away": "BUF", "home": "MIA", "status": "pre_game", "date": "2026-10-04", "game_id": "2"},
    {"week": 4, "away": "NYJ", "home": "NE", "status": "in_game", "date": "2026-10-04", "game_id": "3"},
    {"week": 5, "away": "KC", "home": "DEN", "status": "pre_game", "date": "2026-10-08", "game_id": "4"},
    # Sleeper keeps a canceled game listed next to its rescheduled replacement.
    {"week": 6, "away": "SEA", "home": "DEN", "status": "pre_game", "date": "2026-10-15", "game_id": "6"},
    {"week": 6, "away": "SEA", "home": "DAL", "status": "canceled", "date": "2026-10-15", "game_id": "5"},
    {"week": 6, "away": "DAL", "home": "GB", "status": "pre_game", "date": "2026-10-18", "game_id": "7"},
]


def proj(pid, team):
    return AdjustedProjection(pid, pid, "WR", team, 10.0, 4.0, 1.0, 1.0, 1)


def test_statuses_cover_only_the_weeks_teams():
    assert set(schedule.statuses_from_games(GAMES, 4)) == {"SF", "LAR", "BUF", "MIA", "NYJ", "NE"}


def test_game_status_played_upcoming_bye():
    statuses = schedule.statuses_from_games(GAMES, 4)
    assert schedule.game_status("SF", statuses) == schedule.PLAYED
    assert schedule.game_status("BUF", statuses) == schedule.UPCOMING
    assert schedule.game_status("KC", statuses) == schedule.BYE


def test_a_game_in_progress_counts_as_started():
    assert schedule.game_status("NE", schedule.statuses_from_games(GAMES, 4)) == schedule.PLAYED


def test_unknown_when_no_team_or_no_schedule():
    statuses = schedule.statuses_from_games(GAMES, 4)
    assert schedule.game_status(None, statuses) is None
    assert schedule.game_status("SF", {}) is None


def test_players_who_cannot_score_this_week_are_flagged():
    statuses = schedule.statuses_from_games(GAMES, 4)
    table = [proj("played", "SF"), proj("upcoming", "BUF"), proj("bye", "KC"), proj("fa", None)]
    assert unavailable_this_week(table, statuses) == {"played": "played", "bye": "bye"}


def test_a_canceled_game_is_ignored_not_counted_as_played():
    statuses = schedule.statuses_from_games(GAMES, 6)
    assert schedule.game_status("SEA", statuses) == schedule.UPCOMING
    assert schedule.game_status("DAL", statuses) == schedule.UPCOMING
