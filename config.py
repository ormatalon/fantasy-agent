"""Central config: env vars, paths, and (later) source weights."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
# True inside the desktop app (PyInstaller), whose files sit in sys._MEIPASS.
FROZEN = getattr(sys, "frozen", False)
WEB_DIR = (Path(sys._MEIPASS) if FROZEN else ROOT_DIR) / "web" / "out"


def _default_data_dir() -> Path:
    """data/ in a clone or the Docker image; the user's app-data folder in
    the desktop app, whose install folder may be read-only."""
    if not FROZEN:
        return ROOT_DIR / "data"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FantasyAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FantasyAgent"
    return Path.home() / ".local" / "share" / "FantasyAgent"


DATA_DIR = Path(os.environ.get("FANTASY_AGENT_DATA") or _default_data_dir())
DB_PATH = DATA_DIR / "fantasy.db"
# `chat` history, kept apart from league data so either can be wiped alone.
CONVERSATIONS_DB_PATH = DATA_DIR / "conversations.db"
# Most recent messages sent to the model each turn. Older history stays
# stored (and resumable) but isn't replayed, so long-lived chats don't grow
# the prompt without bound. Durable facts belong in remembered preferences.
AGENT_HISTORY_MESSAGES = 40

# Settings saved from the web app's setup form. They override the repo's
# .env, which is how the CLI is configured when run from a clone.
SETTINGS_PATH = DATA_DIR / "settings.env"

SLEEPER_API_BASE = "https://api.sleeper.app/v1"

# Players catalog is a large payload; Sleeper asks that it not be pulled
# more than once a day. But it's also the only source of injury designations,
# and a day-old copy missed an injury reported the evening before. 6h is the
# compromise: a handful of pulls on a busy day, never one per question.
PLAYERS_CACHE_TTL_HOURS = 6

# `ask`/`chat` re-sync before answering once local data is older than this,
# so answers don't silently come from yesterday's rosters and injuries.
AUTO_SYNC_HOURS = 2

# Rosters alone (3 small requests) are re-pulled before a question once
# they're older than this, so a move just made in Sleeper is seen at once.
# 0 = before every question; raise it to rate-limit.
ROSTER_REFRESH_SECONDS = 0

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

DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def reload() -> None:
    """(Re)read the user settings from the environment, the repo's .env and
    SETTINGS_PATH. Modules read these as `config.X` at call time, so a
    settings change from the web app applies without a restart."""
    global SLEEPER_USERNAME, SLEEPER_SEASON, SLEEPER_TOKEN
    global OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL
    global GMAIL_ADDRESS, GMAIL_APP_PASSWORD
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(SETTINGS_PATH, override=True)

    SLEEPER_USERNAME = os.environ.get("SLEEPER_USERNAME", "")
    SLEEPER_SEASON = os.environ.get("SLEEPER_SEASON", "")
    # Your Sleeper login token. Only writes (lineup, IR, add/drop, waiver
    # claims) and a few account reads (pending claims) need it.
    SLEEPER_TOKEN = os.environ.get("SLEEPER_TOKEN", "")

    # --- Agent layer (Stage 3.5) ---
    # PLAN.md originally specified openai/gpt-oss-120b:free; OpenRouter has
    # since made that slug paid-only, so the default is a pinned free model
    # verified to emit tool calls. Pinned rather than an auto-router so the
    # agent's behavior stays reproducible.
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
    # The key and base URL are only read here, so a shared key later means
    # pointing this at a proxy that holds it, with no change to the agent.
    OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"

    # --- Email notifications (Stage 4) ---
    # A Gmail app password (16 chars), NOT the account password. The Gmail MCP
    # connector can't be used here - it is read/draft-only and cannot send.
    GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
    GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


reload()

