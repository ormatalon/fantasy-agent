"""Windows desktop entry point: the app in its own window, data and log under %LOCALAPPDATA%\FantasyAgent.

Any FANTASY_* variable already set wins (FANTASY_UI_NATIVE=0 serves headless, for checks).
Without FANTASY_UI_PORT a free local port is used, so a Docker container on 8080 does not stop the app.
"""

import multiprocessing
import os
import socket
import sys
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def configure() -> None:
    home = Path(os.environ["LOCALAPPDATA"]) / "FantasyAgent"
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FANTASY_DATA_DIR", str(home))
    os.environ.setdefault("FANTASY_LOG_FILE", str(home / "fantasy-agent.log"))
    os.environ.setdefault("FANTASY_UI_NATIVE", "1")
    os.environ.setdefault("NICEGUI_STORAGE_PATH", str(home / ".nicegui"))
    if "FANTASY_UI_PORT" not in os.environ:
        os.environ["FANTASY_UI_PORT"] = str(free_port())
    if sys.stdout is None:  # a windowed exe has no console: send prints and tracebacks to the log
        sys.stdout = sys.stderr = open(os.environ["FANTASY_LOG_FILE"], "a", encoding="utf-8", buffering=1)


if __name__ in {"__main__", "__mp_main__"}:
    multiprocessing.freeze_support()
    configure()
    from ui.app import serve

    serve()
