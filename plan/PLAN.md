# PLAN: Fantasy Agent v1

The build plan for the rebuild. `PRD.md` is the spec and `AGENT.md` holds the
fixed rules and stack; this file decides what v1 contains, who owns what, and
the order of work. Where this plan narrows the PRD for v1, the PRD still
describes the full product.

Terminology (from `AGENT.md`): **fantasy team** is a manager's roster in a
Sleeper league; **NFL team** is a real franchise. Never a bare "team".

---

## 1. Goal

A desktop app that helps one manager run a Sleeper fantasy league mid-season:
it reads the league, projects players with mean and variance, recommends
lineups, waiver pickups, injured stashes and trades, answers questions about
NFL teams and players, and explains every answer from data the product
fetched. An LLM orchestrates; deterministic tools compute every number.

### v1 scope

| Area | In v1 |
|---|---|
| Setup | Sleeper username, league picker (asks when there are several, remembers the choice, switch by name), OpenRouter key, model choice. Takes effect without restart; the key is never shown back |
| Leagues | Season-long redraft only: IDP or team DEF; PPR, half-PPR or standard. Other roster settings read as they are. Dynasty, keeper, best ball and guillotine are reported as not supported at setup |
| Lineup | Best lineup from the one set in Sleeper, respecting locks and IR rules (PRD 4.4), with a why. Expected-points by default; a high-risk, high-reward lineup on request or when the agent judges the matchup calls for it, from a tool's projected totals and win probability |
| Waivers | VORP ranking for this week and rest of season, FAAB guide within the league's budget, game-started and bye exclusions |
| Injured stash | Unrostered injured players by healthy rest-of-season value, with return news |
| Trades | Evaluate a proposed trade (default rest-of-season); find trade ideas for a target player or positional need, naming the other fantasy team and why it plausibly helps them (their starting lineup value rises). Asked about a specific fantasy team, search only that one; asked in general, first ask which of my players I am willing to trade. No suggestions after the trade deadline; uneven trades count roster spots (PRD 4.4) |
| NFL knowledge | NFL team rosters and depth order, injury designations and practice status, game-day inactives, player news, usage (snaps, targets, carries), season-to-date stats and leaders. All from tools, each fact with source and freshness |
| Results | Last completed week: both scores, result, opponent's top scorers, points left on my bench |
| Agent | Chat with per-turn context (date, season, week, league, format, my roster as set in Sleeper, recent changes); persistent chat history per league with "start fresh". The model's own knowledge is used only for phrasing |
| GUI | Setup screen, chat with progress (current step, elapsed time), my fantasy team panel (roster as set in Sleeper, this week's matchup and score, injury flags) |
| Observability | Per-question trace of model input, tool calls with arguments and results, tokens, latency, retries. Off by default; never in tests; no credentials |
| Packaging | Docker image + start/stop scripts (owner and dev path); Windows desktop installer built by CI (friend path); short user guide (install, OpenRouter key, free or paid model, first setup, example questions) |
| Projections | Sleeper + nflverse blend with mean and variance; IR or ruled-out players projected 0 for that game, no other injury adjustment; scoring only from projected stats, with unprojected scoring items listed; rest-of-season (through week 18) default; weekly and rest-of-season backtests. Matchup adjustment stays off |

### Out of scope for v1

- Any write to Sleeper (lineup, IR, add/drop, waiver claims, trades). v1 is
  read-only; moves are v2. Nothing in v1 needs a Sleeper token.
- Android app, Mac build, user-facing CLI, standalone web surface (the Docker
  path serves the same page locally, but it is not a separate product).
- Draft assistant, weekly email digest, remembered preferences.
- Own ML projection model (v2, via notebooks and the backtest gate).
- Code signing and auto-update.
- Recommendation screens outside chat (lineup, waiver board, trade evaluator
  as their own views).

### Free to run

- Default model is a free OpenRouter model, pinned by id, that reliably calls
  tools. Users bring their own key. A paid model may be chosen in Settings.
- Data sources are free and legal to use (documented or public APIs, or terms
  and robots.txt that allow it). No paid feeds.
- Everything runs on the user's machine; there is no hosted backend.

### Done for v1

1. Every read-only live check in `TEST_QUESTIONS.md` (rewritten for v1, see
   Phase 3) passes, each through the right tool call. A correct answer
   without the right tool call is a fail.
2. A non-technical friend installs the Windows build on a clean machine,
   enters a username and key, and gets correct answers about their own league
   (including a non-IDP league).

---

## 2. Architecture

```
Desktop shell (native window)          Docker (owner/dev)
        |                                    |
        +------------ FastAPI (localhost only) ------------+
        |  serves the NiceGUI page at / (same process)     |
        |  /api/*  setup, leagues, my fantasy team, chat   |
        |          (chat streams progress events)          |
        +---------------------------+----------------------+
                                    |
                     Agent: tool-calling loop over OpenRouter
                     context block rebuilt every model turn
                                    | calls tools
      +-------------+---------------+---------------+-------------+
      |             |               |               |             |
  Ingestion     NFL facts      Projections      Decisions     Results/leaders
  (Sleeper REST, (rosters,     (sources ->     (lineup ILP,   (actuals under
   GraphQL reads, depth, injuries, blend -> mean  VORP, stash,  league scoring)
   nflverse)     usage, news)   + variance)     trades)
      |             |               |               |
      +-------------+------ SQLite (created if missing, migrated in place)
```

- **Frontend:** NiceGUI (`src/ui`), in the FastAPI process, calling the
  backend modules directly. Same app in the desktop shell (NiceGUI native
  window via pywebview, bundled with PyInstaller) and in Docker. Chosen over
  the Next.js version built in 2.6 for one language and one process.
- **Tracing:** LangSmith free tier, off by default, enabled by env vars.
- **Backend:** Python, FastAPI, uv. SQLite in the user's app-data folder
  (desktop) or a Docker volume.
- **Agent:** OpenRouter via an OpenAI-compatible client. Framework (LangGraph
  or a plain loop) is the ai-engineer's Phase 1 decision, recorded with a
  reason. Retries match OpenRouter's HTTP-200 error bodies; a daily quota fails
  fast with a clear message and keeps the question.
- **Projection artifact:** one table, per player per week (and rest of
  season): mean, variance, contributing sources. Every decision reads it; no
  decision computes its own projections.
- **Sources:** each behind a common interface with a configurable weight.
  Projection sources: Sleeper, nflverse. NFL fact sources: Sleeper catalog,
  nflverse (weekly rosters, depth charts, snap counts, participation), the
  NFL's official injury reports, ESPN public JSON. Each undocumented API is
  called from one module only.
- **Freshness:** data at most 2 hours old, at most 2 minutes on game days
  while the app is open (PRD 4.2), within each source's limits. My roster,
  lineup and current-week matchup re-pulled before each question (GraphQL
  `league_rosters`); players catalog about daily; injuries, inactives and
  scores from lighter sources on the 2-minute cycle on game days; roster diffs
  recorded and given to the model; refresh failures reported. "This week" is
  the next week to be played. Kickoff times come from nflverse or ESPN, since
  Sleeper's schedule has dates only.
- **Inaccessible sources** are not used; the product works without them and
  the agent states the gap.
- **Security:** port bound to localhost; keys stored locally, never logged,
  never in traces, never sent to the model.

---

## 3. Roles and ownership

The team lead (this session) owns `plan/`, `PRD.md`, `AGENT.md`, coordination
and stage hand-offs. Each folder has one owner; others change it only through
its owner or a contract change.

| Role | Owns | Responsibilities |
|---|---|---|
| backend-developer | `src/web/`, `src/ingestion/`, `src/storage/`, `pyproject.toml`, `scripts/`, `desktop/`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/`, `.env.example` | API, Sleeper and NFL data sources, crosswalk, storage and migrations, freshness, Docker, Windows installer, CI. Author of `docs/api-contract.md` |
| data-scientist | `src/projections/`, `src/decisions/`, `src/evaluation/`, `notebooks/` | Projection sources and blend, variance, injury overlay, rest-of-season, lineup ILP, VORP, stash, trade evaluation and ideas, results and leaders, backtest. Author of `docs/data-contract.md` |
| ai-engineer | `src/agent/` | Framework decision, model choice and verification, tool catalog, per-turn context, system prompt (including the fantasy team / NFL team rule), retries and quota handling, chat history, LangSmith tracing. Author of the agent-tools section of `docs/data-contract.md` |
| ui-designer | `design/`, `docs/user-guide.md` | Screens (setup, chat with progress, my fantasy team panel), states (loading, empty, error, quota), tokens from the `AGENT.md` colors, desktop window sizes |
| frontend-developer | `src/ui/` | NiceGUI app built to the design, calling the backend modules |
| qa-expert | `tests/`, `TEST_QUESTIONS.md` | Live-check runner over the API, grounding check, acceptance runs, installer test plan. Reviews every contract for testability |

Unit tests and their fixtures live next to the code they test
(`src/<area>/tests/`) and belong to that area's owner; anyone may read them. Tests use real fixtures (captured API responses,
real headlines).

---

## 4. Phases

AGENT.md applies throughout: each stage ends with a stop, a summary, exact test
commands and the owner's approval; one stage per commit, committed only after
approval. Root cause before any fix.

### Phase 1: contracts and design

| # | Task | Owner | Depends on | Acceptance |
|---|---|---|---|---|
| 1.1 | Spikes: confirm the default model is still free and calls tools reliably (5 tool-routing prompts); check access and terms for the NFL injury report, ESPN JSON and nflverse 2026 in-season files (depth, snaps, participation); confirm GraphQL `league_rosters` and news still work | ai-engineer (model), backend-developer (sources) | none | Short findings note in `docs/spikes.md` with captured sample responses saved as fixtures; each source marked usable or not, with the reason |
| 1.2 | `docs/data-contract.md`: SQLite schema and migration rule; source interface; player id crosswalk (Sleeper id as the key, mapping rules for nflverse, ESPN, NFL ids); projection artifact; decision result shapes, each with a structured "why"; freshness and source fields on every NFL fact | data-scientist, backend-developer | 1.1 | Reviewed by ai-engineer and qa-expert; every v1 tool's data has a home |
| 1.3 | Agent-tools section of the data contract: one tool per question type in PRD 4.5 (v1 subset), inputs, outputs (summary first, labeled facts), what each does not cover; per-turn context block; framework decision with reason | ai-engineer | 1.2 draft | Each `TEST_QUESTIONS.md` v1 check maps to a tool |
| 1.4 | `docs/api-contract.md`: setup and settings, league list and switch, my fantasy team panel, chat (send, stream progress events, history, start fresh), errors (quota, refresh failed, missing key) | backend-developer | 1.3 | Reviewed by frontend-developer, ai-engineer, qa-expert |
| 1.5 | Design: setup, chat with progress, my fantasy team panel, all states, in `design/` | ui-designer | 1.4 draft | Owner approves the screens |
| 1.6 | Test plan: rewrite `TEST_QUESTIONS.md` for v1 (drop write checks 22-25 to a v2 list; replace CLI steps with API calls); define the grounding check (every player name, NFL team and role in an answer appears in that turn's tool results) | qa-expert | 1.3, 1.4 | Owner approves the list |

**Phase 1 gate:** owner approves both contracts, the design and the test plan.
One commit.

### Phase 2: build

Stages are approved and committed one at a time. Work inside a stage can run
in parallel against the contracts and fixtures.

| # | Stage | Owner | Depends on | Acceptance |
|---|---|---|---|---|
| 2.1 | Backend skeleton and data spine: FastAPI serving a placeholder page, settings, league discovery and selection, Sleeper ingestion into SQLite for all fantasy teams (rosters, matchups with per-player points, transactions, players catalog), freshness rules, roster diffs, league format derived from settings | backend-developer | Phase 1 | Given a username: league picker works with one and several leagues; my roster matches the Sleeper app; a move in the app shows on the next request; upgrade keeps existing data |
| 2.2 | NFL facts: NFL team rosters, depth order, injury designations and practice status, inactives, usage, news, each with source and freshness; crosswalk to Sleeper ids | backend-developer | 2.1 | Seattle roster has no DK Metcalf; a player's designation matches the official report; crosswalk coverage reported per source (target near the first build's 98%) |
| 2.3 | Projections and backtests: Sleeper and nflverse sources, blend, variance, IR/ruled-out set to 0, rest-of-season and full season, league scoring including IDP; weekly and rest-of-season backtests | data-scientist | 2.1 (fixtures earlier) | One command reproduces both backtests; blend beats Sleeper-only weekly in 2024 and 2025; rest-of-season result reported against the prorated Sleeper baseline in both seasons |
| 2.4 | Decisions: lineup (expected and high-risk, with projected totals and win probability), waivers (two horizons), injured stash, trade evaluation, trade ideas, week results, season leaders | data-scientist | 2.3, 2.2 for stash news | Unit tests on real fixtures for each PRD 4.4 rule (locks both sides, no empty slot, no IR starter, no equal-value reshuffle, byes, game started, budget, trade deadline, roster spots in uneven trades); trade ideas name a fantasy team and both value changes |
| 2.5 | Agent: tools wired to 2.1-2.4, per-turn context, prompt, retries and quota, chat history, tracing | ai-engineer | 2.1, tools grow with 2.2-2.4 | Routing checks pass for every tool; numbers in answers match the tool output; tracing off by default and free of credentials |
| 2.6 | Frontend: setup, chat with streamed progress, my fantasy team panel | frontend-developer | 1.5, API from 2.1/2.5 (mocks earlier) | Works against the real backend in Docker; all designed states reachable |
| 2.7 | Packaging: Dockerfile, compose, `scripts/start|stop.sh|.bat`; Windows desktop shell and per-user installer built by CI; data in app-data; log file | backend-developer | 2.6 | `scripts/start.bat` serves the app on localhost only; CI produces an installer that runs on a clean Windows VM without admin rights |
| 2.8 | User guide `docs/user-guide.md`: install, create an OpenRouter account and key, free vs paid model, first setup, example questions | ui-designer | 2.7 | A friend follows it alone in 3.3 |

Order: 2.1, then 2.2 / 2.3 / 2.6 in parallel, then 2.4, 2.5, 2.7. 2.5 and 2.6
start early on mocks and finish once their dependencies land.

### Phase 3: QA

| # | Task | Owner | Depends on | Acceptance |
|---|---|---|---|---|
| 3.1 | Live-check runner: posts each question to the chat API, records tool calls and the answer, applies the grounding check, writes a dated result table | qa-expert | 2.5 | Runs the full list in one sitting within the free daily quota |
| 3.2 | Full live pass in "Or Test League" (IDP, user OrMatalon) and a non-IDP league; game-window checks (game started, locks) on a Sunday | qa-expert | 3.1, 2.7 | All read-only checks pass (done criterion 1); failures filed with root cause to the owning role |
| 3.3 | Friend install: clean Windows machine, installer only, setup, five common questions | qa-expert with the owner | 2.7 | Done criterion 2 |
| 3.4 | Regression: backtest, unit tests and live checks rerun after fixes | qa-expert | 3.2 | Green before the v1 tag |

**Phase 3 gate:** owner signs off v1.

---

## 5. Contracts

| File | Author | Reviewers | Contents |
|---|---|---|---|
| `docs/api-contract.md` | backend-developer | frontend-developer, ai-engineer, qa-expert | HTTP endpoints, request and response shapes, progress event stream, error codes and messages |
| `docs/data-contract.md` | data-scientist, backend-developer; agent-tools section by ai-engineer | qa-expert | SQLite schema and migrations, source interface, crosswalk, projection artifact, decision results, NFL fact shape with source and freshness, agent tool catalog and per-turn context |

A contract change after Phase 1 goes through its author and is approved by its
reviewers before code depends on it.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Free model quality (prose arithmetic, unflagged name guesses, long tasks) | Tools return summaries first and labeled facts; grounding check in QA; paid model selectable |
| Free tier quota (about 50 requests a day) limits live checks and real use | Few model turns per question; fail fast on quota; runner budgets the pass |
| Pinned free model goes paid or disappears | Spike 1.1 rechecks; model chosen in Settings with a recommended default |
| Undocumented APIs (Sleeper GraphQL, ESPN) change | One module each; reads fall back to Sleeper REST |
| NFL injury reports or ESPN not usable under their terms | Spike 1.1 decides; v1 can ship on Sleeper + nflverse with the gap stated |
| Crosswalk gaps across Sleeper, nflverse, ESPN, NFL ids | Fallback rules from PRD 5; coverage reported per source |
| nflverse in-season files lag | Freshness shown on every fact; the agent states the date |
| Windows installer: SmartScreen warning without signing; bundler misses dynamically loaded modules | Install guide covers the warning; CI smoke test starts the bundled app and calls every source |
| Mid-season start: little stored history | Backfill completed weeks from Sleeper on first sync |

---

## 7. Open questions

1. Non-IDP league for the format checks: the owner creates one (team DEF,
   drafted so rosters exist) and names it before Phase 3.

Resolved: tracing is LangSmith (free tier, off by default). Desktop shell is
pywebview + PyInstaller. `PRD.md` carries a v1 scope note pointing here. IDP
checks run in "Or Test League" (user OrMatalon). Trade ideas scope as in
section 1.
