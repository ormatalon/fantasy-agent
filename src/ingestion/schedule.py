"""NFL schedule from nflverse: each team's kickoff time for a week.

Answers "has this player's game already started?" and "is his team on bye?"
- neither of which the players catalog or projections can tell us. A waiver
pickup whose game has kicked off can't score for you this week.
"""

import sys
from datetime import datetime, timezone

import pandas as pd

SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# nflverse abbreviations that differ from Sleeper's.
_TO_SLEEPER = {"LA": "LAR"}

PLAYED = "played"
BYE = "bye"
UPCOMING = "upcoming"


def kickoffs_from_frame(df: pd.DataFrame, season: str | int, week: int) -> dict[str, datetime]:
    games = df[(df["season"] == int(season)) & (df["game_type"] == "REG") & (df["week"] == int(week))]
    out: dict[str, datetime] = {}
    for g in games.itertuples():
        # nflverse lists kickoff date/time in US Eastern.
        kickoff = pd.Timestamp(f"{g.gameday} {g.gametime}").tz_localize("America/New_York").tz_convert("UTC")
        for team in (g.away_team, g.home_team):
            out[_TO_SLEEPER.get(team, team)] = kickoff.to_pydatetime()
    return out


def load_kickoffs(season: str | int, week: int) -> dict[str, datetime]:
    """{team: kickoff (UTC)} for the week. Empty if the schedule can't be
    fetched - callers then skip game-state checks rather than fail."""
    try:
        df = pd.read_csv(SCHEDULE_URL, usecols=["season", "game_type", "week", "gameday", "gametime", "away_team", "home_team"])
    except Exception as e:
        print(f"schedule: unavailable ({e}); skipping game-state checks.", file=sys.stderr)
        return {}
    return kickoffs_from_frame(df, season, week)


def game_status(team: str | None, kickoffs: dict[str, datetime], now: datetime | None = None) -> str | None:
    """PLAYED, BYE or UPCOMING for a team this week; None when unknown (no
    team, or no schedule loaded)."""
    if not team or not kickoffs:
        return None
    kickoff = kickoffs.get(team)
    if kickoff is None:
        return BYE
    return PLAYED if kickoff <= (now or datetime.now(timezone.utc)) else UPCOMING
