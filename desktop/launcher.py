"""Windows desktop entry point: the app in its own window, data and log under %LOCALAPPDATA%\FantasyAgent.

Any FANTASY_* variable already set wins (FANTASY_UI_NATIVE=0 serves headless, for checks).
"""

import multiprocessing
import os
import sys
from pathlib import Path

home = Path(os.environ["LOCALAPPDATA"]) / "FantasyAgent"
home.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("FANTASY_DATA_DIR", str(home))
os.environ.setdefault("FANTASY_LOG_FILE", str(home / "fantasy-agent.log"))
os.environ.setdefault("FANTASY_UI_NATIVE", "1")
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(home / ".nicegui"))
if sys.stdout is None:  # a windowed exe has no console: send prints and tracebacks to the log
    sys.stdout = sys.stderr = open(os.environ["FANTASY_LOG_FILE"], "a", encoding="utf-8", buffering=1)

from ui.app import serve  # noqa: E402

if __name__ in {"__main__", "__mp_main__"}:
    multiprocessing.freeze_support()
    serve()
