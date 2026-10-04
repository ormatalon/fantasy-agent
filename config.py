"""Central config: env vars, paths, and (later) source weights."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "fantasy.db"
# `chat` history, kept apart from league data so either can be wiped alone.
CONVERSATIONS_DB_PATH = DATA_DIR / "conversations.db"
# Most recent messages sent to the model each turn. Older history stays
# stored (and resumable) but isn't replayed, so long-lived chats don't grow
# the prompt without bound. Durable facts belong in remembered preferences.
AGENT_HISTORY_MESSAGES = 40

SLEEPER_USERNAME = os.environ.get("SLEEPER_USERNAME", "")
SLEEPER_SEASON = os.environ.get("SLEEPER_SEASON", "")

SLEEPER_API_BASE = "https://api.sleeper.app/v1"

# Players catalog is a large payload; Sleeper asks that it not be pulled
# more than once a day. But it's also the only source of injury designations,
# and a day-old copy missed an injury reported the evening before. 6h is the
# compromise: a handful of pulls on a busy day, never one per question.
PLAYERS_CACHE_TTL_HOURS = 6

# `ask`/`chat` re-sync before answering once local data is older than this,
# so answers don't silently come from yesterday's rosters and injuries.
AUTO_SYNC_HOURS = 2

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

# --- Agent layer (Stage 3.5) ---
# PLAN.md originally specified openai/gpt-oss-120b:free; OpenRouter has since
# made that slug paid-only, so the default here is a pinned free model that
# was verified to emit tool calls. Pinned rather than an auto-router so the
# agent's behavior stays reproducible.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Email notifications (Stage 4) ---
# A Gmail app password (16 chars), NOT the account password. The Gmail MCP
# connector can't be used here - it is read/draft-only and cannot send.
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

