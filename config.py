"""Central config: env vars, paths, and (later) source weights."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "fantasy.db"

SLEEPER_USERNAME = os.environ.get("SLEEPER_USERNAME", "")
SLEEPER_SEASON = os.environ.get("SLEEPER_SEASON", "")

SLEEPER_API_BASE = "https://api.sleeper.app/v1"

# Players catalog is a large payload; Sleeper asks that it not be pulled
# more than once a day. Re-sync only if the local copy is older than this.
PLAYERS_CACHE_TTL_HOURS = 24

# Weight of each registered projection source in the blend. A source with
# weight 0 (or omitted here) is disabled without touching blend.py.
SOURCE_WEIGHTS: dict[str, float] = {
    "sleeper": 1.0,
    "nflverse": 0.5,
}

# Trailing window (in weeks) used to compute each defense's points-allowed
# baseline for the matchup adjustment.
MATCHUP_TRAILING_WEEKS = 8

# Whether the matchup adjustment is actually applied to live projections.
# Stage 2's backtest (2024 season, weeks 3-17) showed it makes MAE *worse*
# than the plain blend (5.09 vs 4.83), even after trying shrinkage across
# 16 (shrink_k, trailing_weeks) combos — none beat the unadjusted blend.
# Per PLAN.md's own rule ("if it doesn't beat baseline, it doesn't ship"),
# leave this off until the model is reworked and re-proven via `backtest`.
# The evaluation harness still scores it every run (see evaluation/backtest.py)
# so future attempts have an immediate answer on whether they helped.
MATCHUP_ADJUSTMENT_ENABLED = False

