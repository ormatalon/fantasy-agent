"""Download raw data for the research notebooks into research/data/.

Sleeper weekly projections and actual stats (regular season, QB/RB/WR/TE), plus nflverse schedules
(Vegas lines), injury reports, the player id map, weekly rosters (point-in-time team and roster status),
FantasyPros PPR draft rankings (preseason snapshots are picked in build_dataset.py), ff_opportunity
(expected fantasy points of past games), and weekly team and player stats (EPA, CPOE, WOPR; used lagged). 2021 is fetched for history features only; 2026 stops at the
last finished week.

Sleeper's weekly projections are used only as the benchmark to beat (task 1 and the model evaluation),
never as model features.

Run: uv run python fetch_data.py
"""

from pathlib import Path

import httpx
import nflreadpy as nfl
import pandas as pd

DATA = Path(__file__).parent / "data"
SEASONS = range(2021, 2027)
LAST_WEEK = {2021: 18, 2022: 18, 2023: 18, 2024: 18, 2025: 18, 2026: 7}
POSITIONS = ["QB", "RB", "WR", "TE"]


def fetch_sleeper(kind: str) -> pd.DataFrame:
    """kind is 'projections' or 'stats'. One row per player-week, stats flattened."""
    params = [("season_type", "regular")] + [("position[]", p) for p in POSITIONS]
    frames = []
    with httpx.Client(timeout=60) as client:
        for season in SEASONS:
            for week in range(1, LAST_WEEK[season] + 1):
                rows = client.get(f"https://api.sleeper.app/{kind}/nfl/{season}/{week}", params=params).json()
                df = pd.json_normalize(rows, sep="_")
                df["season"], df["week"] = season, week
                frames.append(df)
                print(kind, season, week, len(df))
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.removeprefix("stats_") for c in df.columns]
    return df.drop(columns=[c for c in df.columns if c.startswith("player_metadata")])


def main() -> None:
    DATA.mkdir(exist_ok=True)
    fetch_sleeper("projections").to_parquet(DATA / "sleeper_proj.parquet")
    fetch_sleeper("stats").to_parquet(DATA / "sleeper_stats.parquet")
    seasons = list(SEASONS)
    nfl.load_schedules(seasons).to_pandas().to_parquet(DATA / "schedules.parquet")
    nfl.load_injuries(seasons).to_pandas().to_parquet(DATA / "injuries.parquet")
    nfl.load_ff_playerids().to_pandas().to_parquet(DATA / "playerids.parquet")
    nfl.load_rosters_weekly(seasons).to_pandas().to_parquet(DATA / "rosters_weekly.parquet")
    rank = nfl.load_ff_rankings("all").to_pandas()
    rank[rank.fp_page == "/nfl/rankings/ppr-cheatsheets.php"].to_parquet(DATA / "fp_draft_rankings.parquet")
    nfl.load_ff_opportunity(seasons, stat_type="weekly").to_pandas().to_parquet(DATA / "ff_opportunity.parquet")
    nfl.load_team_stats(seasons, summary_level="week").to_pandas().to_parquet(DATA / "team_stats.parquet")
    nfl.load_player_stats(seasons, summary_level="week").to_pandas().to_parquet(DATA / "player_stats.parquet")


if __name__ == "__main__":
    main()
