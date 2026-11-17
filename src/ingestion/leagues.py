"""Choosing a league. No web or HTTP types here, so the agent and the API share it."""

import sqlite3

from storage.db import set_settings

from . import sync


class LeagueNotFound(Exception):
    def __init__(self, available: list[str]):
        super().__init__("no league with that name")
        self.available = available


def resolve_league(leagues: list[dict], league_id: str | None = None, league_name: str | None = None) -> dict:
    """The league (a LeagueInfo item) by id, or by name: exact (any case), then a unique prefix.

    Raises LeagueNotFound, carrying the real league names.
    """
    if league_id:
        match = [l for l in leagues if l["league_id"] == league_id]
    else:
        name = (league_name or "").strip().lower()
        match = [l for l in leagues if l["name"].lower() == name]
        if not match and name:
            match = [l for l in leagues if l["name"].lower().startswith(name)]
    if len(match) != 1:
        raise LeagueNotFound([l["name"] for l in leagues])
    return match[0]


def select_league(conn: sqlite3.Connection, league_id: str) -> None:
    """Load the league from Sleeper, then make it current. Raises SleeperError when it cannot be loaded."""
    sync.refresh(conn, league_id)
    set_settings(conn, {"current_league_id": league_id})
