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
