# Fantasy Agent

A read-only assistant for your Sleeper fantasy football team. Specs: `PRD.md`, `plan/PLAN.md`.

## Run

Docker (needs Docker Desktop or Docker Engine), then open http://localhost:8080:

    scripts/start.sh      # Windows: scripts\start.bat
    scripts/stop.sh       # Windows: scripts\stop.bat

The port is published on this computer only. Data lives in the `fantasy-data` volume. An optional `.env` (see `.env.example`) is passed in.

Without Docker: `uv run python -m ui`.

## Windows desktop app

    uv sync --group desktop
    uv run pyinstaller desktop/fantasy-agent.spec --noconfirm
    iscc desktop\installer.iss

CI builds the per-user installer (no admin rights) and uploads it as an artifact. Data and log: `%LOCALAPPDATA%\FantasyAgent`.

## Test

    uv run pytest -q
