# Plan: desktop app

Status: implemented and verified on Windows (2026-10-21). Mac build untested.

## Goal

Non-technical users download one file, double-click it, and get the web
chat page in its own window. No command line, Node, Python or Docker.

## Approach

- **Bundle** with PyInstaller (one folder, not one file: starts fast and is
  flagged less by antivirus). It holds Python, the dependencies and the
  prebuilt page (`web/out`).
- **Launcher** `src/desktop.py`: starts the FastAPI app on a free local port
  in a background thread, then opens it in a native window with pywebview
  (WebView2 on Windows, WebKit on Mac). Closing the window quits the app.
- **Data** goes to the user's app-data folder when bundled
  (`%LOCALAPPDATA%\FantasyAgent`, `~/Library/Application Support/FantasyAgent`),
  since the install folder may be read-only. From a clone it stays `data/`.
  `FANTASY_AGENT_DATA` overrides both.
- **Installers**, built by GitHub Actions on a `v*` tag next to the Docker
  release: Windows with Inno Setup (Start menu and desktop shortcut, no admin),
  Mac as a `.dmg`.

## Steps

1. `config.py`: `FROZEN`, `BUNDLE_DIR` (where `web/out` is) and the per-user
   `DATA_DIR`. `src/web/app.py` serves `config.WEB_DIR`.
2. `src/desktop.py`: free port, uvicorn in a thread, wait until it answers,
   pywebview window, exit when the window closes.
3. `desktop/fantasy-agent.spec` (PyInstaller) and `desktop/installer.iss`
   (Inno Setup). A `desktop` dependency group with pyinstaller and pywebview.
4. Build locally on Windows, launch the exe, check the page loads and a
   question runs against a copy of real data.
5. `.github/workflows/desktop.yml`: build on windows-latest and macos-latest,
   attach the installer and the dmg to the release.
6. README: a "Download" section first.

## Not included

- Code signing. Windows shows "Windows protected your PC" (More info, Run
  anyway); Mac needs right-click Open or System Settings. Signing costs money
  and can be added to the workflow later.
- Sleeper writes stay CLI-only, as in the Docker version.
- Auto-update: a new version is a new download.
