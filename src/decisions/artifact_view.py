"""Keep the projection artifact fresh for the decisions that read it (docs/data-contract.md section 7)."""

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from ingestion.nfl import service
from projections import artifact, sleeper_data
from projections.blend import DEFAULT_WEIGHTS
from projections.sources import NflverseSource, SleeperSource
from storage.db import get_json, get_setting, set_settings

MAX_AGE = timedelta(hours=2)


def default_sources(conn: sqlite3.Connection, season: int) -> list:
    schedule = defaultdict(set)
    for g in conn.execute("select week, home, away from nfl_game where season = ?", (season,)):
        schedule[g["week"]] |= {g["home"], g["away"]}
    return [
        SleeperSource(lambda s, w: sleeper_data.weekly_projections(s, w, max_age=3600)),
        NflverseSource.for_season(season, schedule=schedule, max_age=7200),
    ]


def stored_weights(conn: sqlite3.Connection) -> dict:
    return get_json(conn, "projection_weights", DEFAULT_WEIGHTS)


def injury_hash(conn: sqlite3.Connection, season: int) -> str:
    """The injury designations the artifact is built from: official reports, ESPN and the Sleeper catalog."""
    reports = conn.execute("select player_id, source, week, designation, return_date from nfl_injury where season = ? order by 1, 2, 3", (season,))
    catalog = conn.execute("select player_id, injury_status from player where injury_status is not null order by 1")
    return hashlib.sha1(json.dumps([[tuple(r) for r in reports], [tuple(r) for r in catalog]]).encode()).hexdigest()


def is_stale(conn: sqlite3.Connection, league_id: str, scoring: dict, week: int, now: datetime) -> bool:
    """Missing, older than 2 hours, or built for another scoring, week or injury state (contract sections 5 and 7)."""
    if get_setting(conn, f"projection_injury_hash:{league_id}") != injury_hash(conn, service.season(conn)):
        return True
    meta = conn.execute("select * from projection_meta where league_id = ?", (league_id,)).fetchone()
    if meta is None or meta["scoring_hash"] != hashlib.sha1(json.dumps(scoring, sort_keys=True).encode()).hexdigest():
        return True
    first = conn.execute("select min(week) from projection where league_id = ? and horizon = 'week'", (league_id,)).fetchone()[0]
    return first != week or now - datetime.fromisoformat(meta["computed_at"].replace("Z", "+00:00")) > MAX_AGE


def ensure(conn: sqlite3.Connection, league_id: str, now: datetime | None = None) -> list[str]:
    """Rebuild the league's artifact when it is missing or stale. Returns warnings: when the rebuild fails the
    stored artifact is used and the warning says how old it is; with nothing stored the failure is raised."""
    now = now or datetime.now(UTC)
    league = conn.execute("select season, scoring_settings from league where league_id = ?", (league_id,)).fetchone()
    scoring, week = json.loads(league["scoring_settings"]), service.this_week(conn)
    if not is_stale(conn, league_id, scoring, week, now):
        return []
    try:
        rows, meta = artifact.build(conn, league_id, league["season"], week, default_sources(conn, league["season"]),
                                    stored_weights(conn))  # fmt: skip
        artifact.save(conn, rows, meta)
        set_settings(conn, {f"projection_injury_hash:{league_id}": injury_hash(conn, league["season"])})
    except Exception as e:  # network, source or id failures: keep what is stored
        old = conn.execute("select computed_at from projection_meta where league_id = ?", (league_id,)).fetchone()
        if old is None:
            raise
        return [f"projections could not be refreshed ({e}); using the ones stored at {old['computed_at']}"]
    return []
