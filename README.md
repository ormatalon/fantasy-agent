# NFL Fantasy Engine

See `plan/PLAN.md` for the full build plan.

## Download

From the latest GitHub release:

- **Windows:** `FantasyAgent-Setup.exe`. Run it. Windows may say "Windows
  protected your PC": click More info, then Run anyway (the app isn't signed).
- **Mac (Apple silicon):** `FantasyAgent-Mac.dmg`. Drag the app to
  Applications. On first open, macOS blocks it: open System Settings, Privacy &
  Security, and click Open Anyway.

Open Fantasy Agent, enter your Sleeper username and a free OpenRouter key from
https://openrouter.ai/keys, pick a league and ask away.

"Model not available" means the free model is down. Pick another one under
Settings, Advanced.

Settings, league data, chats and a `desktop.log` are kept in
`%LOCALAPPDATA%\FantasyAgent` (Windows) or
`~/Library/Application Support/FantasyAgent` (Mac).

## Run with Docker

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Download `fantasy-agent-docker.zip` from the latest release and unzip it.
3. Run `scripts/start.bat` (Windows) or `scripts/start.sh` (Mac/Linux). The page
   opens at http://localhost:8000. `scripts/stop.bat` / `scripts/stop.sh` stop it.

Data is kept in a Docker volume. The page is only reachable from your own computer.

Lineup changes, adds and waiver claims stay on the command line, behind the
APPROVE prompt. Run any CLI command below inside the app, from the unzipped
folder (add `SLEEPER_TOKEN` in Settings first):

```
docker compose exec app python -m src.interface.cli lineup --apply
```

The model key and base URL are read only in `config.reload()`. A shared key
later means pointing `OPENROUTER_BASE_URL` at a proxy that holds it.

## Develop

Build the page once: `cd web && npm ci && npm run build`. Then:

- Web app: `uv run uvicorn src.web.app:app --port 8000`
- Desktop window: `uv run --group desktop python -m src.desktop`
- Desktop build: `uv run --group desktop pyinstaller desktop/fantasy-agent.spec --noconfirm`,
  then the Windows installer with Inno Setup: `iscc desktop/installer.iss`
- Docker: `scripts/start.sh --build` (or `scripts\start.bat --build`)

Publishing: every push to `main` builds the Docker image to
`ghcr.io/ormatalon/fantasy-agent`. Tagging `v*` (e.g. `git tag v1.0.0 && git push --tags`)
also builds the Windows installer and the Mac app, and creates the release.
The GHCR package must be made public once, in its settings on GitHub.

### CLI setup

```
uv sync
cp .env.example .env   # then fill in SLEEPER_USERNAME
```

### CLI usage

```
uv run python -m src.interface.cli sync [--league "name"] [--all]
uv run python -m src.interface.cli leagues                 # your leagues; which is active
uv run python -m src.interface.cli use "league name"       # switch the active league
uv run python -m src.interface.cli roster
uv run python -m src.interface.cli roster --fantasy-team "another fantasy team in the league"
uv run python -m src.interface.cli transactions
uv run python -m src.interface.cli projections [--week N] [--season]
uv run python -m src.interface.cli lineup [--week N]
uv run python -m src.interface.cli waivers [--limit N] [--season]
uv run python -m src.interface.cli trades --give "name,name" --receive "name,name" [--season]
uv run python -m src.interface.cli draft
uv run python -m src.interface.cli news [--player "name"] [--limit N]
uv run python -m src.interface.cli trending [--drop] [--limit N]
uv run python -m src.interface.cli results [--week N]      # defaults to last completed week
uv run python -m src.interface.cli graph                   # the agent's LangGraph, as Mermaid
```

Enact moves on Sleeper (needs `SLEEPER_TOKEN` in `.env`, see `.env.example`).
Each one shows exactly what will change and waits for you to type `APPROVE`:

```
uv run python -m src.interface.cli lineup --live            # set in Sleeper vs recommended
uv run python -m src.interface.cli lineup --apply           # set it
uv run python -m src.interface.cli ir [--set "name,name"]   # show or set your IR slots
uv run python -m src.interface.cli add "name" [--drop "name"]
uv run python -m src.interface.cli claim "name" [--drop "name"] [--bid N]
uv run python -m src.interface.cli claim --cancel <id>
uv run python -m src.interface.cli claims                   # pending waiver claims
```

`--season` means rest-of-season (from the current week on). Weekly `waivers`
leave out players whose game has already kicked off or who are on bye.

Weekly digest (lineup + waiver board + watch list + last week's result):

```
uv run python -m src.interface.cli digest              # print only
uv run python -m src.interface.cli digest --email      # also email it
```

Emailing needs `GMAIL_ADDRESS` and a 16-character Gmail **app password**
(`GMAIL_APP_PASSWORD`) in `.env` — a normal account password will not work.
Generate one at https://myaccount.google.com/apppasswords (requires 2-Step
Verification).

To run it every Sunday morning, schedule `sync` then `digest --email`:

```
# macOS/Linux - crontab -e
0 9 * * 0 cd /path/to/fantasy-agent && uv run python -m src.interface.cli sync && uv run python -m src.interface.cli digest --email

# Windows - Task Scheduler, weekly trigger, action:
#   uv  run python -m src.interface.cli digest --email
#   (Start in: C:\path\to\fantasy-agent)
```

Ask the agent in natural language (needs `OPENROUTER_API_KEY` in `.env`):

```
uv run python -m src.interface.cli ask "who should I start at flex?"
uv run python -m src.interface.cli chat
```

`ask` and `chat` re-sync on their own when local data is more than 2 hours old
(`AUTO_SYNC_HOURS` in `config.py`), and `chat` re-checks on every question.

## Tracing

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env` to send each
`ask`/`chat` question to LangSmith (project `LANGSMITH_PROJECT`): every model
turn, tool call and result, tokens and latency, tagged with league, week and
model. Traces include league, fantasy team and manager names. Tests never trace.

## Tests

```
uv run pytest
```
