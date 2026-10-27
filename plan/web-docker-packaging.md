# Plan: package the agent as a Docker app with a web chat page

## Context

Today the agent is a terminal CLI that only works from a cloned repo. The user
needs Python, uv and a hand-edited `.env`. The goal is that a non-technical
user downloads the app, starts it with one script, fills in a setup form in
the browser, and chats with the agent there.

Decisions already made with the user:

- **Form:** Docker container, Next.js chat page served by a FastAPI backend,
  per AGENT.md. Start and stop scripts wrap docker compose.
- **Model access:** each user enters their own OpenRouter key. Leave room for
  a shared key later.

Facts from the code that shape the plan:

- The agent has **no write tools**; its only Sleeper account call is the read
  `get_pending_claims`. So a chat-only web page needs no approval flow. Writes
  (lineup, IR, add, claim) stay CLI-only behind
  `src/execution/approval.py`, reachable with `docker compose exec`.
- Settings are read once from env at import in `config.py`. Every module reads
  them as `config.X` at call time (only `SLEEPER_API_BASE` is imported by
  value, and it never changes), so reloading `config` attributes is enough.
- `run_sync(conn, league_query=...)` in `src/ingestion/sync.py` already accepts
  a league id, so the web never needs the interactive `prompt_choose_league`.
- `data/` (`config.DATA_DIR`) already holds both SQLite files. Mounting it as a
  volume persists everything.

## Scope

In: setup form, league picker, sync button, chat with resumable history, new
chat, Docker image, start/stop scripts, prebuilt image on GitHub so users
download only two small files.

Out (stays in the CLI via `docker compose exec`): the deterministic commands,
Sleeper writes and their APPROVE gate, email digest, draft assistant,
backtest. Single user only; no login.

## Steps

### 1. Runtime settings (`config.py`)

- Add `SETTINGS_PATH = DATA_DIR / "settings.env"`.
- Move the env-derived values (`SLEEPER_USERNAME`, `SLEEPER_SEASON`,
  `SLEEPER_TOKEN`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, Gmail, LangSmith
  is env-only already) into a `reload()` function that runs
  `load_dotenv()` (repo `.env`, for CLI developers) then
  `load_dotenv(SETTINGS_PATH, override=True)` and sets the module globals.
  Call it once at import.
- **Shared-key opening:** add `OPENROUTER_BASE_URL` to `reload()` as an env
  setting with today's value as default. A future shared key then needs no
  code change in the agent: point the base URL at a small proxy that holds the
  key. `build_agent` in `src/agent/orchestrator.py` already reads both from
  `config`, so the key lives in exactly one place. Note this in the README.

### 2. Reusable chat call (`src/agent/orchestrator.py`)

- Extract from `chat()` the per-turn body into
  `chat_turn(conn, agent, ctx, question) -> str`: refresh, `ctx.reload_state()`
  if a full sync ran, then `_run(agent, question, run_config(ctx, "chat", CHAT_THREAD), show_progress=False)`.
  The CLI loop calls it, so web and CLI share one thread and history.
- Extract `open_chat_agent(conn, new=False)` returning `(agent, ctx)`, built
  from the existing `SqliteSaver` code in `chat()`.

### 3. FastAPI backend (`src/web/app.py`, new)

One module, sync endpoints, a module-level `threading.Lock` around anything
touching the DB or agent (single user, one request at a time):

| Endpoint | Does |
|---|---|
| `GET /api/status` | configured? (username + key set), synced?, active league, `user_leagues` from state |
| `POST /api/settings` | writes username, OpenRouter key, optional Sleeper token and model to `SETTINGS_PATH` with `dotenv.set_key`, then `config.reload()`; never returns the key back |
| `GET /api/leagues` | `SleeperClient` lookup of the user's leagues for the picker |
| `POST /api/sync` | `run_sync(conn, league_query=league_id)` |
| `GET /api/chat` | stored messages of the chat thread (human and final AI only) |
| `POST /api/chat` | `chat_turn(...)`, returns the answer |
| `POST /api/chat/new` | `checkpointer.delete_thread(CHAT_THREAD)` |

- Map `AgentError` and `SleeperError` to HTTP 400 with their message; their
  texts are already written for users.
- Mount the Next.js export with `StaticFiles(directory="web/out", html=True)` at `/`.
- Add `fastapi` and `uvicorn` to `pyproject.toml`.
- No streaming in v1: the page shows a "thinking" state for the 10 to 60
  seconds a question takes.

### 4. Next.js page (`web/`, new)

- Next.js with `output: "export"`, TypeScript, one page, plain CSS using the
  AGENT.md colors (navy headings, purple submit buttons, blue links, yellow
  accent, gray labels).
- Three states driven by `/api/status`:
  1. **Setup:** Sleeper username, OpenRouter key with a link to
     openrouter.ai/keys, optional Sleeper token with the help text from
     `.env.example`.
  2. **League:** pick a league, then sync with a progress state.
  3. **Chat:** message list, input, "New chat", "Sync now", active league name,
     a link back to settings.
- Render answers as Markdown (`react-markdown` with `remark-gfm` for tables).
- A footer line that writes (lineup, add, claims) are done from the CLI.

### 5. Docker and scripts

- `Dockerfile`, multi-stage: `node:22-slim` runs `npm ci && npm run build`;
  `python:3.11-slim` with uv copied from `ghcr.io/astral-sh/uv`, runs
  `uv sync --frozen --no-dev`, copies `src/`, `config.py` and `web/out`, and
  starts `uvicorn src.web.app:app --host 0.0.0.0 --port 8000`.
- `.dockerignore`: `.venv`, `data`, `.env`, `web/node_modules`, `.git`.
- `docker-compose.yml`: `image: ghcr.io/ormatalon/fantasy-agent:latest`,
  port `127.0.0.1:8000:8000` (local only, since there is no login and keys are
  stored), named volume on `/app/data`, `restart: unless-stopped`.
- `docker-compose.build.yml` override with `build: .` for developers.
- `scripts/start.sh`, `scripts/stop.sh`, `scripts/start.bat`,
  `scripts/stop.bat`: `docker compose pull` (skip when building), `up -d`,
  print and open `http://localhost:8000`; stop runs `docker compose down`.

### 6. Publishing for download

- `.github/workflows/docker.yml`: on push to `main` and on version tags, build
  for `linux/amd64` and `linux/arm64` (Apple silicon) and push to GHCR.
- Each GitHub release attaches a zip of `docker-compose.yml` and the four
  scripts. The user flow: install Docker Desktop, unzip, run start, open the
  page, fill the form.
- The GHCR package must be set public once by hand on GitHub.

### 7. Docs

- README: a short "Run the app" section first (the user flow above, and how to
  run a CLI command with `docker compose exec app uv run python -m src.interface.cli ...`).
  Keep the developer CLI section below it.
- PROJECT_LOG.md: one entry for this stage.

## Critical files

- Modify: `config.py`, `src/agent/orchestrator.py`, `pyproject.toml`, `README.md`.
- New: `src/web/app.py`, `web/` (Next.js), `Dockerfile`, `.dockerignore`,
  `docker-compose.yml`, `docker-compose.build.yml`, `scripts/*`,
  `.github/workflows/docker.yml`, `tests/test_web.py`.
- Reuse: `run_sync`, `refresh_before_question` (`src/ingestion/sync.py`);
  `build_agent`, `_run`, `run_config`, `CHAT_THREAD`
  (`src/agent/orchestrator.py`); `storage.get_connection`, `storage.get_state`
  (`src/ingestion/storage.py`); `SleeperClient.get_user`,
  `get_user_leagues` (`src/ingestion/sleeper.py`).

## Verification

1. `uv run pytest`: existing 194 tests still pass. New `tests/test_web.py`
   uses FastAPI's `TestClient` with a temp `DATA_DIR`: settings round trip and
   `config.reload()`, status before and after setup, sync errors mapped to 400,
   chat with a fake agent (pattern from `tests/test_agent.py`), new chat clears
   history. The `conftest.py` tracing-off rule still applies.
2. `npm run build` in `web/` produces `web/out`.
3. `docker compose -f docker-compose.yml -f docker-compose.build.yml up --build`,
   then in a browser: setup form, pick league, sync, ask
   "who should I start at flex?", reload the page and see history kept,
   "New chat" clears it.
4. `docker compose down` then `up`: settings and league data survive (volume).
5. `docker compose exec app uv run python -m src.interface.cli lineup` works.
6. Fresh machine check (or a clean folder): download only the release zip,
   run `scripts/start.bat`, and confirm the image pulls and the page loads.
