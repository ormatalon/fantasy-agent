"""Cache and trimming layer over Sleeper's projection and stats rows. HTTP is ingestion.sleeper's.

Rows are trimmed (zero stats and ADP and rank keys dropped; a row with no stats is Sleeper saying 0) and cached as
gzipped json under the app data folder, so a backtest downloads each file once.
"""

import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ingestion import sleeper
from storage.db import data_dir

COUNTERS = {"gp", "gms_active", "gs"}
DROP_PREFIXES = ("adp_", "pos_adp_", "pos_rank_", "rank_", "tm_", "q1_", "q2_", "q3_", "q4_", "h1_", "h2_")


def cache_dir() -> Path:
    path = data_dir() / "backtest_cache" / "sleeper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _trim(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        stats = {k: v for k, v in r["stats"].items() if v and k not in COUNTERS and not k.startswith(DROP_PREFIXES)}
        player = r.get("player") or {}
        out.append(
            {
                "player_id": r["player_id"],
                "week": r.get("week"),
                "position": player.get("position"),
                "name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                "nfl_team": r.get("team") or player.get("team"),
                "opponent": r.get("opponent"),
                "date": r.get("date"),
                "last_modified": r.get("last_modified"),
                "stats": stats,
            }
        )
    return out


def _cached(name: str, fetch, max_age: float | None = None) -> list[dict]:
    """Cached forever for past seasons; current-season calls pass max_age (seconds)."""
    path = cache_dir() / f"{name}.json.gz"
    if path.exists() and (max_age is None or time.time() - path.stat().st_mtime <= max_age):
        return json.loads(gzip.decompress(path.read_bytes()))
    rows = _trim(fetch())
    path.write_bytes(gzip.compress(json.dumps(rows).encode()))
    return rows


def weekly_projections(season: int, week: int, max_age: float | None = None) -> list[dict]:
    return _cached(f"proj_{season}_{week}", lambda: sleeper.get_projections(season, week), max_age)


def season_projections(season: int) -> list[dict]:
    return _cached(f"proj_{season}_season", lambda: sleeper.get_projections(season))


def weekly_stats(season: int, week: int) -> list[dict]:
    """Actual stats, same keys as the projections."""
    return _cached(f"stats_{season}_{week}", lambda: sleeper.get_stats(season, week))


def prefetch(season: int, weeks: range) -> None:
    """Download everything a backtest of this season needs, in parallel."""
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda w: weekly_projections(season, w), weeks))
        list(pool.map(lambda w: weekly_stats(season, w), weeks))
    season_projections(season)
