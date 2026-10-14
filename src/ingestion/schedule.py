"""NFL schedule from Sleeper: whether each team's game this week has started.

Answers "has this player's game already started?" and "is his team on bye?"
- neither of which the players catalog or projections can tell us. A waiver
pickup whose game has kicked off can't score for you this week, and a
starter whose game has kicked off can't be moved.

Undocumented endpoint (powers the Sleeper app, outside /v1):
  /schedule/nfl/regular/<season> -> [{week, home, away, status, date, game_id}]
`status` is "pre_game" before kickoff and "complete" after; anything else
(an in-progress game) counts as started, except "canceled": a canceled game
stays listed next to its rescheduled replacement, so it is skipped. Team
codes are Sleeper's own.
"""

import sys

import httpx

SCHEDULE_URL = "https://api.sleeper.app/schedule/nfl/regular/{season}"
NOT_STARTED = "pre_game"
CANCELED = "canceled"

PLAYED = "played"
BYE = "bye"
UPCOMING = "upcoming"


def statuses_from_games(games: list[dict], week: int) -> dict[str, str]:
    """{team: Sleeper game status} for every team playing in `week`."""
    out: dict[str, str] = {}
    for g in games:
        if g.get("week") == int(week) and g.get("status") != CANCELED:
            for team in (g["away"], g["home"]):
                out[team] = g.get("status") or NOT_STARTED
    return out


def load_game_statuses(season: str | int, week: int) -> dict[str, str]:
    """{team: status} for the week, as of now. Not cached: on game day it
    changes by the hour. Empty if the schedule can't be fetched - callers
    then skip game-state checks rather than fail."""
    try:
        resp = httpx.get(SCHEDULE_URL.format(season=season), timeout=15.0)
        resp.raise_for_status()
        games = resp.json() or []
    except Exception as e:
        print(f"schedule: unavailable ({e}); skipping game-state checks.", file=sys.stderr)
        return {}
    return statuses_from_games(games, week)


def game_status(team: str | None, statuses: dict[str, str]) -> str | None:
    """PLAYED (started or finished), BYE or UPCOMING for a team this week;
    None when unknown (no team, or no schedule loaded)."""
    if not team or not statuses:
        return None
    status = statuses.get(team)
    if status is None:
        return BYE
    return UPCOMING if status == NOT_STARTED else PLAYED
