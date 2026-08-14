# PLAN.md — NFL Fantasy Engine

> A build directive for an AI coding agent. Read this top-to-bottom before writing code. Build **inside-out**: the decision engine (the "brain") first, execution (the "hands") last. Do not skip the evaluation harness — it is how every later claim gets proven. **Work in checkpoints: at the end of every stage, STOP, explain how to test it, and wait for my approval before committing and moving on** (see §4a — Checkpoint protocol).

---

## 1\. Goal

Build an **LLM-orchestrated agent** that plays my Sleeper NFL fantasy league for me. It ingests data, produces adjusted player projections, and recommends the best moves — weekly lineups, waiver/FAAB bids, trades, and draft picks. It notifies me by email, and (later) can enact approved moves in the Sleeper web UI via browser automation.

**This is an agent, not just a pipeline.** An LLM is the orchestrator: it decides which tools to call, interprets news and context, explains its reasoning in natural language, and drives the interaction. The numerical work (projections, optimization) lives in deterministic tools the agent calls — the LLM orchestrates the math, it does not do the math itself.

I am the human-in-the-loop: for a long time, the agent **recommends** and I **approve**. Only after it has proven itself does it get to act on its own.

---

## 2\. Hard constraints & decisions (already made — do not relitigate)

- **Platform: Sleeper.** Read API is public and keyless. Use it for all reads (league, roster, matchups, players, projections, scoring settings).  
- **Sleeper has NO write API.** Roster changes cannot be made via API. Execution must go through the Sleeper web UI via browser automation (Stage 5). Everything before Stage 5 is read-only.  
- **Interface, for now: terminal CLI.** No web app, no chat app. The CLI is both the dev surface and the interactive surface.  
- **Notifications: email.** A small `notify(subject, body)` module sends via Gmail SMTP (app password) or a transactional email API (Resend/SendGrid). Do NOT rely on the Gmail MCP connector for this — it is read/draft-only and cannot send.  
- **Projections are a blend, not a single source, and the set of sources is open-ended.** Combine multiple source feeds into a baseline, then adjust for (a) opponent matchup and (b) injury/news. Output a mean AND a variance per player. Every source sits behind a common `ProjectionSource` interface so new sources — including **my own ML model** — can be added later just by implementing the interface and adding a weight in `config.py`. Do not hardcode a fixed source list; design for extension from day one.  
- **The LLM agent is the orchestrator (central, not optional).** An LLM sits at the center and runs the operation: it decides which tools to call and when, parses news into structured signal, explains recommendations, and manages the interaction. The decision modules and projection engine are exposed to it as **callable tools**. Start with a hosted LLM API; do not self-host early. Keep deterministic math in the tools, never in LLM free-text.

---

## 3\. Architecture

An **LLM agent orchestrates** a set of deterministic tools. Data flows through a pipeline with a clean seam between the "brain" (decisions) and the "hands" (execution); the agent sits on top and calls into it:

```
                        +-----------------------------+
                        |   LLM AGENT (orchestrator)  |
                        |  plans, calls tools,        |
                        |  parses news, explains,     |
                        |  drives interaction         |
                        +--------------+--------------+
                                       | calls tools
   Ingestion  ->  Projection engine  ->  Decision modules  ->  Interface  ->  Execution
   (read-only)    (mean + variance)      (thin consumers)      (CLI/email)    (browser, last)
```

Everything downstream consumes ONE artifact: a table of **adjusted projected points per player per week, with an uncertainty estimate**. Get that middle layer right and every decision module becomes small. Each module is also exposed as a **tool the agent can call** (with a clear name, input schema, and structured output). Keep the seam clean so the execution layer (email today, WhatsApp/browser later) is swappable without touching the brain.

### Suggested repo layout

```
nfl-fantasy-engine/
  README.md
  PLAN.md
  pyproject.toml            # or requirements.txt
  .env.example              # SLEEPER_LEAGUE_ID, EMAIL creds, etc. (never commit real secrets)
  config.py                 # league id, scoring, source weights, paths
  data/                     # local cache / storage (gitignored)
  src/
    agent/
      orchestrator.py       # LLM agent loop: plan -> call tools -> explain
      tools.py              # tool registry: exposes decision modules + engine to the LLM
      prompts.py            # system prompt, tool descriptions, guardrails
    ingestion/
      sleeper.py            # read client: league, rosters, matchups, players, projections, txns
      news.py               # injury designations + role-change signal
      id_crosswalk.py       # player id mapping across sources
    projections/
      sources/
        base.py             # ProjectionSource interface (pluggable)
        sleeper_src.py      # Sleeper projections
        fantasypros_src.py  # FantasyPros consensus
        nflverse_src.py     # nflverse-derived
        my_model_src.py     # MY OWN ML MODEL (future) — same interface, add + weight it
      blend.py              # weighted baseline across all registered sources
      matchup.py            # defense pts-allowed-by-position -> per-player multiplier
      injury.py             # designation discounts + backup bumps
      engine.py             # orchestrates -> mean + variance per player/week
    evaluation/
      backtest.py           # projections vs actual scored points
      metrics.py            # error vs raw-consensus baseline (per source + blended)
    decisions/
      lineup.py             # constrained start/sit optimizer
      waivers.py            # FAAB bids via value-over-replacement + variance
      trades.py             # roster value delta evaluation
      draft.py              # real-time draft tracker + autonomous pick suggestions
    interface/
      cli.py                # entry point: print recommendations + "why"
      notify.py             # email notifier (SMTP / transactional API)
    execution/
      browser.py            # Sleeper UI automation (STUB until Stage 5)
  tests/
```

### Suggested stack

Python 3.11+, `requests`/`httpx` for Sleeper, `pandas` for data, `pulp` or `OR-Tools` for the lineup optimizer, `pytest` for tests, `nfl_data_py` (nflverse) for historical play-by-play. For the agent layer, a hosted LLM API with tool/function calling (e.g., Anthropic or OpenAI SDK) — expose each module as a tool; keep the agent framework thin. Keep dependencies minimal early.

---

## 4\. Build stages

Work stage by stage. Each stage has a definition of done. Do not start a stage before the previous one meets its DoD.

### 4a. Checkpoint protocol (MANDATORY between every stage)

At the end of each stage, the agent must **stop and wait for me** — do not roll straight into the next stage. Specifically, at every checkpoint the agent will:

1. **Stop.** Announce the stage is complete and summarize what was built.  
2. **Explain how to test it.** Give exact commands to run and the expected output, tied to the stage's Definition of Done, so I can verify the checkpoint myself.  
3. **Wait for my explicit approval.** Do not proceed on assumption. If I request changes, fix and re-present the checkpoint.  
4. **Only after I approve, commit** that stage as a single, self-contained git commit with a clear message (e.g., `Stage 1: projection blend + matchup/injury adjustments`).  
5. **Then, and only then, begin the next stage.**

Never bundle two stages into one commit, and never commit an unapproved stage.

### Stage 0 — Data spine (read-only)

- Sleeper read client: given a league ID, fetch league, scoring settings, all rosters, my roster, weekly matchups, and the players catalog.  
- Player ID crosswalk across sources (Sleeper \<-\> nflverse \<-\> FantasyPros). This is unglamorous and everything depends on it — get it solid.  
- **Local storage holds league-wide state, not just my team** (parquet/SQLite is fine). Persist:  
  - the full **players catalog** (ids, names, positions, teams, status);  
  - **every team's roster** in the league, not only mine (needed for waivers, trades, and to know who is actually available);  
  - **weekly matchups** and results;  
  - **all league transactions / moves** (adds, drops, waivers, trades) as a history;  
  - **draft data** (picks, order) when applicable;  
  - **weekly actual stats**, projections (per source), and pulled news.  
  - Snapshot with timestamps so history is queryable over time, not just the latest state.  
- **DoD:** `cli.py` can print my current roster with names, positions, and this week's opponent, and can list another team's roster and the league's recent transactions from local storage.

### Stage 1 — Projection engine (the core edge)

- Pull base projections from Sleeper \+ at least one other source; reconcile via the crosswalk.  
- Implement each feed behind the `ProjectionSource` interface (`projections/sources/`) and blend all **registered** sources — never hardcode the source list. Adding a new source later (including **my own ML model**, `my_model_src.py`) must require only implementing the interface and adding a weight in `config.py`, with no changes to `blend.py` or any decision module.  
- Weighted-average blend into a baseline (weights live in `config.py`).  
- Matchup adjustment: compute each defense's points-allowed-by-position from nflverse, convert to a multiplier vs. league average, scale each player. (The "RB vs. worst run defense gets a bump" case must fall out of this.)  
- Injury/news overlay: apply discounts for Q/D/O designations; bump backups on role changes (e.g., starter ruled out).  
- **Output a mean AND a variance per player per week.**  
- **DoD:** engine emits an adjusted projection table; a spot-check of 3–4 players shows the matchup and injury adjustments moving numbers in the correct direction.

### Stage 2 — Evaluation harness (do NOT skip)

- Backtest projections against actual scored points for past weeks.  
- Report error (MAE/RMSE) and, critically, error **relative to the raw-consensus baseline** — prove the adjustments add value rather than assuming they do.  
- This becomes the regression guard for every later change to the projection engine.  
- **DoD:** one command produces an accuracy report vs. baseline over a backtest window.

### Stage 3 — Decision modules (thin consumers of Stage 1\)

Order: lineup \-\> waivers \-\> trades \-\> draft. (Move draft earlier only if my draft is imminent.)

- **Lineup optimizer:** constrained start/sit maximizing expected points under roster slot rules. First end-to-end win.  
- **Waivers/FAAB:** rank available players by value-over-replacement; size bids using projection and variance.  
- **Trades:** score a proposed trade by the change in total projected roster value.  
- **Draft assistant (autonomous, real-time):** once I activate it for a draft, it runs on its own — no prompting from me per pick. It **polls the live draft**, maintains the board (who's gone, who's left, my roster needs), and the instant it becomes my turn it **proactively pushes a ranked pick suggestion** (with VORP, tiers, and the "why"). It keeps tracking after each pick without me asking. The only thing I do is activate it at the start and make the actual pick. It advises; it does not auto-pick unless I explicitly grant that later.  
- **DoD:** each module prints a ranked recommendation with a human-readable "why" (the matchup and injury factors that drove it). For the draft assistant specifically: once started, it detects my turn from live draft state and surfaces a suggestion **unprompted**, updating as picks come in.

### Stage 4 — Interface (CLI \+ email)

- CLI subcommands: `lineup`, `waivers`, `trades`, `draft`, each printing recommendations \+ reasons.  
- `notify.py`: `notify(subject, body)` via Gmail SMTP (app password) or transactional API.  
- Optional scheduled run (e.g., Sunday AM) that emails the weekly lineup \+ waiver board.  
- **DoD:** running the weekly command both prints to terminal and emails me a readable digest.

### Stage 5 — Execution (the "hands", LAST)

- Browser automation drives the Sleeper web UI to enact an **approved** move (set lineup, add/drop, place waiver claim).  
- Keep a mandatory human-approve gate before ANY write for a long while.  
- **DoD:** given an approved recommendation, the agent sets my Sleeper lineup in the browser and confirms the result; the approve gate cannot be bypassed.

---

## 5\. Guiding principles

- **Stop at every checkpoint.** Finish a stage, explain how to test it, wait for approval, commit, then continue (§4a). Never skip ahead.  
- **Agent orchestrates, tools compute.** The LLM plans and calls tools; deterministic modules do the math. Never put numeric computation in LLM free-text.  
- **Design for new projection sources.** Sources are pluggable; my own ML model is a first-class future source that must drop in without touching the blend or decision code.  
- **Brain before hands.** No execution code until decisions are trustworthy.  
- **Evaluation right after projections.** If a change doesn't beat the consensus baseline in the backtest, it doesn't ship.  
- **Human-approve gate** on every write until the engine has earned autonomy.  
- **One projection artifact** feeds everything — resist duplicating projection logic inside decision modules.  
- **Mean and variance, always.** Uncertainty is what makes bids and start/sit smart.  
- **Secrets in env vars**, never committed. Provide `.env.example`.

---

## 6\. Open questions to confirm with me before/while building

- Is my **draft imminent** — i.e., is my league's draft happening within the next few days/weeks? The draft assistant is normally built last (it's only useful seasonally), so if the draft is coming up soon, reorder Stage 3 to build the real-time draft assistant FIRST so it's ready in time. Tell me my draft date.  
- My **Sleeper league ID / username** (needed to wire Stage 0 to my real roster).  
- League **scoring format** (PPR / half-PPR / standard) and roster slots — pull from Sleeper settings, but confirm.  
- Which **second projection source** to start with (FantasyPros vs. nflverse-derived).  
- Hosted LLM API vs. none for the (optional, later) news-parsing / Q\&A layer.

---

## 7\. First action for the agent

Start **Stage 0**. Scaffold the repo layout above, implement `ingestion/sleeper.py` and `interface/cli.py`, and make the DoD command work: print my roster and this week's opponent, and persist league-wide state (all rosters, transactions, matchups, players) to local storage. Ask me for my Sleeper league ID (or build against a placeholder I can swap into `.env`). Then **honor the checkpoint protocol (§4a): stop, tell me how to test it, wait for my approval, commit Stage 0, and only then continue.**  
