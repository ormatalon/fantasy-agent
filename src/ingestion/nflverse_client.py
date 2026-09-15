"""Fetches nflverse's weekly player stats directly from their current
release asset.

`nfl_data_py==0.3.3` (the only version ever published to PyPI — the
package is effectively unmaintained) still points its `import_weekly_data`
at nflverse's older `player_stats/player_stats_<year>.parquet` release,
which nflverse stopped publishing after the 2024 season. The data itself
still exists, just under a renamed asset: `stats_player/stats_player_week_
<year>.parquet` — same columns we need (player_id, position, team,
opponent_team, week, fantasy_points, fantasy_points_ppr, receptions),
confirmed against a live pull for 2025 and 2026. This reads that asset
directly rather than going through the stale package function.
"""

import pandas as pd

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.parquet"


def import_weekly_stats(years: list[int]) -> pd.DataFrame:
    frames = [pd.read_parquet(STATS_URL.format(year=year)) for year in years]
    combined = pd.concat(frames, ignore_index=True)
    return combined.rename(columns={"team": "recent_team"})
