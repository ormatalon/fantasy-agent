"""Desktop app: the web app in its own window.

Starts the FastAPI app on a free local port in a background thread, then
opens it in a native window (pywebview). Closing the window quits the app.
Built into an installer by desktop/fantasy-agent.spec; from a clone:

    uv run --group desktop python -m src.desktop
"""

import socket
import sys
import threading
import time

import config


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    # A windowed app has no console (stdout/stderr are None): keep the output
    # in a log file users can send when something goes wrong.
    if sys.stdout is None or sys.stderr is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(config.DATA_DIR / "desktop.log", "a", encoding="utf-8", buffering=1)

    import uvicorn
    import webview

    from src.web.app import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise SystemExit("The app's server failed to start - see desktop.log.")
        time.sleep(0.05)

    webview.create_window("Fantasy Agent", f"http://127.0.0.1:{port}/", width=1000, height=820, min_size=(420, 560))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
