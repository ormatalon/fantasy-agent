"""The refresh that comes before every question: Sleeper first, then whatever NFL facts are due."""

import sqlite3

from . import sync
from .nfl import service


def before_question(conn: sqlite3.Connection, league_id: str) -> None:
    """Raises SleeperError when Sleeper cannot be reached and nothing is stored.

    NFL sources that fail keep their stored rows and appear in sync.failures(); the due-check keeps
    this cheap (a source is pulled only when its interval has passed, not forced).
    """
    sync.refresh(conn, league_id)
    service.refresh(conn)
