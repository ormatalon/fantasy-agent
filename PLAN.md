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
- **League discovery, not a hardcoded league ID.** The agent takes my Sleeper **username**, not a league ID. It resolves `username -> user_id` (`GET /v1/user/<username>`), then lists that user's leagues for the season (`GET /v1/user/<user_id>/leagues/nfl/<season>`). If there is exactly one league, use it. If there is more than one, **the agent must ask me which league to address** before doing anything else — never guess. Cache the resolved league ID for the session/local storage so it doesn't re-prompt every run.  
- **Sleeper has NO write API.** Roster changes cannot be made via API. Execution must go through the Sleeper web UI via browser automation (Stage 5). Everything before Stage 5 is read-only.  
- **Interface, for now: terminal CLI.** No web app, no chat app. The CLI is both the dev surface and the interactive surface.  
- **Notifications: email.** A small `notify(subject, body)` module sends via Gmail SMTP (app password) or a transactional email API (Resend/SendGrid). Do NOT rely on the Gmail MCP connector for this — it is read/draft-only and cannot send.  
- **Projections are a blend, not a single source, and the set of sources is open-ended.** Combine multiple source feeds into a baseline, then adjust for (a) opponent matchup and (b) injury/news. Output a mean AND a variance per player. Every source sits behind a common `ProjectionSource` interface so new sources — including **my own ML model** — can be added later just by implementing the interface and adding a weight in `config.py`. Do not hardcode a fixed source list; design for extension from day one.  
- **Start Stage 1 with two sources: Sleeper + nflverse-derived.** FantasyPros was the intended third source, but its projections load via client-side XHR to `/api/`/`/ajax/` endpoints that its own `robots.txt` explicitly disallows — not just "unofficial," but stated off-limits to crawl. Deferred until either a paid FantasyPros API key is available or another free/legitimate consensus source is picked. Each source's weight lives in `config.py`; a source with weight `0` (or simply commented out) is disabled without touching `blend.py` or any decision module. The blend must tolerate any subset of registered sources being active.  
- **The LLM agent is the orchestrator (central, not optional).** An LLM sits at the center and runs the operation: it decides which tools to call and when, parses news into structured signal, explains recommendations, and manages the interaction. The decision modules and projection engine are exposed to it as **callable tools**. Access the model via **OpenRouter** (`OPENROUTER_API_KEY` in `.env`), model id `nvidia/nemotron-3-super-120b-a12b:free`; do not self-host early. Keep deterministic math in the tools, never in LLM free-text.  
- **Agent framework: LangGraph.** Build the orchestrator (`agent/orchestrator.py`) as a LangGraph graph — nodes for plan/tool-call/observe/explain, tool nodes wrapping the modules in `agent/tools.py`, and explicit state for conversation + in-progress recommendation. Use LangGraph specifically for the stateful, multi-step, sometimes-long-running flows where it earns its keep — the draft assistant's poll-and-react loop (Stage 3) and any multi-turn tool-calling sequence (gather projections -> check injury news -> re-rank -> explain). For a single-shot tool call with no branching (e.g., a plain `lineup` CLI invocation), a direct SDK tool-call loop is fine — don't force LangGraph where a linear call suffices. Do not adopt a second agent framework alongside it.

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
      orchestrator.py       # LangGraph graph: plan -> call tools -> explain
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

Python 3.11+, `requests`/`httpx` for Sleeper, `pandas` for data, `pulp` or `OR-Tools` for the lineup optimizer, `pytest` for tests, `nfl_data_py` (nflverse) for historical play-by-play. For the agent layer, **OpenRouter** (`nvidia/nemotron-3-super-120b-a12b:free`) with tool/function calling, orchestrated via **LangGraph** — expose each module as a tool node; keep the graph thin, one node per responsibility. Keep dependencies minimal early.

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

- Sleeper read client: given my **username**, resolve `user_id` -> list leagues for the season -> if more than one, **prompt me to pick which league** -> then fetch league, scoring settings, all rosters, my roster, weekly matchups, and the players catalog for the chosen league.  
- Player ID crosswalk across sources (Sleeper \<-\> nflverse \<-\> FantasyPros). This is unglamorous and everything depends on it — get it solid.  
- **Local storage holds league-wide state, not just my team** (parquet/SQLite is fine). Persist:  
  - the full **players catalog** (ids, names, positions, teams, status);  
  - **every team's roster** in the league, not only mine (needed for waivers, trades, and to know who is actually available);  
  - **weekly matchups** and results;  
  - **all league transactions / moves** (adds, drops, waivers, trades) as a history;  
  - **draft data** (picks, order) when applicable;  
  - **weekly actual stats**, projections (per source), and pulled news.  
  - Snapshot with timestamps so history is queryable over time, not just the latest state.  
- **DoD:** given only my Sleeper username, `cli.py` resolves my league(s) (prompting me to choose if there's more than one), then prints my current roster with names, positions, and this week's opponent, and can list another team's roster and the league's recent transactions from local storage.

### Stage 1 — Projection engine (the core edge)

- Pull base projections from **Sleeper and nflverse-derived** (FantasyPros deferred, see §2); reconcile via the crosswalk.  
- Implement each feed behind the `ProjectionSource` interface (`projections/sources/`) and blend all **registered** sources — never hardcode the source list. Adding a new source later (including **my own ML model**, `my_model_src.py`) must require only implementing the interface and adding a weight in `config.py`, with no changes to `blend.py` or any decision module.  
- Weighted-average blend into a baseline (weights live in `config.py`).  
- Matchup adjustment: compute each defense's points-allowed-by-position from nflverse, convert to a multiplier vs. league average, scale each player. (The "RB vs. worst run defense gets a bump" case must fall out of this.) **Update after Stage 2's backtest: this adjustment, as built, consistently underperforms plain blending (see Stage 2, tested across two independent seasons) and is disabled by default (`config.MATCHUP_ADJUSTMENT_ENABLED = False`) until reworked and re-proven — the underlying multiplier-computation code stays, and the evaluation harness still scores it every run so a future attempt gets an immediate answer.**  
- Injury/news overlay: apply discounts for Q/D/O designations; bump backups on role changes (e.g., starter ruled out).  
- **Output a mean AND a variance per player per week.**  
- **DoD:** engine emits an adjusted projection table; a spot-check of 3–4 players shows the matchup and injury adjustments moving numbers in the correct direction. (Met for injury; matchup's spot-check moved numbers in the *expected* direction — see live 2024 example in Stage 2 — but Stage 2 then showed that direction doesn't reliably beat a simpler baseline, which is exactly what the harness is for.)

### Stage 2 — Evaluation harness (do NOT skip)

- Backtest projections against actual scored points for past weeks.  
- Report error (MAE/RMSE) and, critically, error **relative to the raw-consensus baseline** — prove the adjustments add value rather than assuming they do.  
- This becomes the regression guard for every later change to the projection engine.  
- **DoD:** one command produces an accuracy report vs. baseline over a backtest window.
- **Data source note:** `nfl_data_py==0.3.3` (the only version ever published — effectively unmaintained) points at a stale nflverse release path that stops at the 2024 season. `src/ingestion/nflverse_client.py` reads nflverse's current asset directly instead, which is what makes the 2025+ backtest below possible. That same investigation also surfaced a real bug: the raw file includes every roster position (OL, DL, LB, punters, ...), not just fantasy-relevant ones, which was quietly inflating match counts with trivial "0 predicted vs. 0 actual" pairs and made the matchup adjustment look far worse than it is. Fixed by filtering to `src/projections/positions.py`'s `SKILL_POSITIONS` everywhere nflverse data is consumed (source, matchup, backtest).
- **Result (weeks 3-17, skill positions only, both seasons independently — not tuned and evaluated on the same one):**

  | Tier | 2024 MAE | 2024 vs. baseline | 2025 MAE | 2025 vs. baseline |
  |---|---|---|---|---|
  | Sleeper only (baseline) | 5.00 | — | 4.80 | — |
  | + blend (Sleeper + nflverse) | 4.63 | -7.4% | 4.44 | -7.5% |
  | + blend + matchup | 4.86 | -2.9% | 4.63 | -3.5% |

  Blending clearly beats the single-source baseline in both seasons. The matchup adjustment beats the naive baseline too, but consistently underperforms plain blend — i.e. it adds noise on top of an already-better signal. Confirmed with a 24-combination shrinkage/trailing-window sweep across both seasons: heavier shrinkage monotonically approaches (but in 2024 never quite reaches) blend-only performance, never exceeds it. So it stays disabled by default (`config.MATCHUP_ADJUSTMENT_ENABLED = False`, §2, Stage 1) — this is the harness doing its job twice now: first catching a plausible-sounding adjustment that doesn't help, then catching a bug in its own ground-truth data before that conclusion could be trusted.
- **Known limitation:** the injury/depth-chart overlay isn't backtested — the local players table only holds *current* injury/depth-chart state, so scoring it against a past week would leak information not available at the time. It's covered by unit tests + a live spot-check instead (Stage 1 DoD) until a historical injury feed exists.

### Stage 3 — Decision modules (thin consumers of Stage 1\)

Order: lineup \-\> waivers \-\> trades \-\> draft. (Move draft earlier only if my draft is imminent.)

- **IDP support added at this stage.** My league starts individual defensive players (`DL`/`LB`/`DB`), not a team defense — discovered when building the lineup optimizer, since those 3 roster slots would otherwise always be empty. Extended: `projections/positions.py` (roster-slot -> granular-position eligibility, e.g. `DB` accepts `CB`/`S`/`FS`/`SS`), both sources (Sleeper's projections endpoint natively covers IDP stat categories; nflverse's raw defensive columns are translated to Sleeper's `idp_*` scoring keys via `projections/idp_stats.py`, sanity-checked against real players — Fred Warner, T.J. Watt, etc. — landing in a plausible range). Matchup and backtest stay offense-only for now (documented in their own modules) — an IDP's value depends on the opposing *offense's* tendencies, not "points allowed by position" the same way, so extending matchup to IDP is a separate future model, not a mechanical filter change.  
- **Lineup optimizer:** constrained start/sit maximizing expected points under roster slot rules, solved as an ILP (`pulp`) rather than greedy-filled, since a greedy fill of the "most specific" slots first can strand points that a flex slot could have captured better. First end-to-end win.  
- **Waivers/FAAB:** rank available (non-rostered) players by value-over-replacement (`decisions/vorp.py`); replacement level per position is the Nth-best *rostered* player at that position, where N = num_teams × demand share (a direct slot credits 1.0, a flex-type slot splits credit across its eligible positions). FAAB bid is a simple, clearly-labeled `VOR × 4%` guide, not a bidding strategy. **Fixed a real bug found via live testing against my actual roster:** a position with no roster-slot demand at all (team `DEF` in this IDP league) was defaulting to a `0.0` baseline, making its *entire* projection look like pure value — `rank_by_vorp` now excludes positions with no computed demand instead of defaulting. **Also pools granular IDP positions** (DE/DT/NT → one `DL` baseline, etc.) rather than computing each independently, since a `DL` slot doesn't distinguish between them and per-granular baselines were sparse enough to be meaningless.  
- **Trades:** score a proposed trade by the change in total projected value, on either horizon (`--season` for full-season totals, default weekly on the CLI; the agent's tool defaults to season, since that's usually what a trade should be judged on). **The earlier weekly-only limitation is closed** — see "Season-long projections" below.  
- **Draft assistant (autonomous, real-time):** once I activate it for a draft, it runs on its own — no prompting from me per pick. It **polls the live draft**, maintains the board (who's gone, who's left, my roster needs), and the instant it becomes my turn it **proactively pushes a ranked pick suggestion** (with VORP and the "why"). It keeps tracking after each pick without me asking. The only thing I do is activate it at the start and make the actual pick. It advises; it does not auto-pick unless I explicitly grant that later. **Limitation:** pick value uses week-1 projections as a season-long value stand-in (same weekly-only-engine constraint as trades). **Not live-testable this session** — my draft already happened — but validated against the real completed draft: correctly resolved my draft slot (2 of 10) and correctly detected the draft was already complete.  
- **Season-long projections (added after Stage 3.5).** Sleeper has a season-level endpoint (`/projections/nfl/<season>`, no week segment) returning full projected season stat totals in the *same stat-key namespace* as the weekly one, so `score_stats` applies the league's own scoring to it unchanged. `engine.build_season_projection_table` produces the same artifact shape as the weekly table, with two deliberate differences: **no matchup adjustment** (it's a per-opponent multiplier; there's no single opponent across a season) and **only season-long injury designations apply** (`injury.SEASON_LONG_DESIGNATIONS` — an IR tag should cut a season outlook, a Questionable tag should not). Single-source for now: nflverse is historical box scores and has no forward-looking season projection, so `num_sources` is 1 and the variance floor carries the uncertainty estimate. Exposed as `projections --season`, `trades --season`, and the agent's `get_season_projections` tool. **Still open:** these are full-season totals (`gp: 18`), not rest-of-season — mid-season use should prorate or subtract games played, which is a modeling decision, not a mechanical one.  
- **Known gap — draft pick suggestions ignore roster need.** `suggest_pick` ranks purely by VORP across all undrafted players; it does not look at what I've already drafted, so it will happily suggest a 4th RB while I have no QB. Visible in `draft --replay`, where the same player tops the list for nine straight rounds. The fix is deterministic and contained (track my own picks \-\> compute unfilled starting slots \-\> weight VORP toward unmet need, plus tier-cliff awareness) and belongs in the **tool**, not the LLM — per §5, need-weighted VORP is arithmetic over ~200 players, which is exactly what the agent layer must not be doing in free text. Deferred, not forgotten.  
- **DoD:** each module prints a ranked recommendation with a human-readable "why" (the matchup and injury factors that drove it). For the draft assistant specifically: once started, it detects my turn from live draft state and surfaces a suggestion **unprompted**, updating as picks come in — logic verified via unit tests (snake-draft turn detection) and against the real completed draft, pending a live in-progress draft to fully confirm the unprompted-suggestion path.

### Stage 3.5 — Agent orchestrator (the LLM layer itself)

The plan declared the agent "central, not optional" (§2, §3) but never gave it a stage — everything through Stage 3 built the deterministic **tools**; this stage builds the thing that *calls* them. Numbered 3.5 rather than renumbering 4/5, which are referenced elsewhere in this doc.

Strategy: **thin agent, fat tools.** The graph should be boring — plan, call a tool, observe, explain. Every number the agent says must come from a tool call, never from its own reasoning. If the agent ever needs to "estimate" something, that's a signal a tool is missing, not that the prompt needs work.

- **Tool registry (`agent/tools.py`).** Wrap the existing modules as typed tools — each with a name, a description the model can route on, an input schema, and structured output. Initial set: `sync_league`, `get_roster`, `get_league_state` (matchup/opponent/standings), `get_transactions`, `get_projections`, `optimize_lineup`, `get_waiver_targets`, `evaluate_trade`, `suggest_draft_pick`. The decision modules already return dataclasses rather than printed text, which is exactly what makes this a wrapping job and not a rewrite. **Do NOT expose `evaluation/backtest.py`** — it's a dev-side regression guard, not something the agent should be invoking mid-conversation.
- **Prompts \+ guardrails (`agent/prompts.py`).** System prompt carrying league context (scoring settings, roster slots, IDP format), the hard rule that numbers come only from tool output, the recommend-don't-act posture, and how to explain a recommendation (name the factors — matchup, injury, replacement level — not just the number).
- **LangGraph graph (`agent/orchestrator.py`).** Explicit state (conversation, league context, in-progress recommendation), nodes for plan \-\> tool-call \-\> observe \-\> explain, conditional edge back into the tool loop, checkpointing so a multi-turn conversation survives. Per §2, keep LangGraph for the stateful/multi-step flows; a single-shot `lineup` CLI call does not need to route through the graph.
- **Model wiring.** OpenRouter (`nvidia/nemotron-3-super-120b-a12b:free`) via an OpenAI-compatible client with `base_url` pointed at OpenRouter, tools bound to the model. Key already in `.env` as `OPENROUTER_API_KEY`. **Model changed during this stage:** the originally specified `openai/gpt-oss-120b:free` was discontinued by OpenRouter (paid-only now), so candidates were probed for actual tool-call emission before choosing — Nemotron is pinned rather than using the `openrouter/free` auto-router so behavior stays reproducible.
- **CLI entry.** `cli.py ask "<question>"` for one-shot questions and an interactive `chat` mode for follow-ups. The existing deterministic subcommands stay — the agent is an addition, not a replacement, and is the fallback-free path when I want the raw numbers.
- **Guardrail tests.** A small set of golden prompts asserting the agent routes to the right tool ("who should I start?" \-\> `optimize_lineup`) and that reported numbers match what the tool returned directly. This is the agent's equivalent of Stage 2's regression guard.
- **Market + actuals signals (added after the season-projection pass).** Two Sleeper sources that were being left on the table: **trending adds/drops** (`/v1/players/nfl/trending/{add,drop}`, counts across every Sleeper league) is the one signal our own math cannot produce — news and hype, surfaced as context the LLM layers on top of VORP, never as a replacement for it; and **`matchups.players_points`**, actual per-player scored points, which we were already fetching and discarding. The latter powers `decisions/results.py`, which feeds *actual* scores back through the Stage 3 lineup optimizer to compute the best lineup in hindsight — so "points left on the bench" falls out of code that already existed. Exposed as `trending`, `results`, and the agent tools `get_trending_players` / `get_week_results`. Adding `players_points` also motivated a real schema migration (`storage._migrate`) rather than making anyone delete their local DB for a new column.  
- **Bug found by the results view:** the lineup ILP maximizes points with a `<= 1` per-slot constraint, so a player projected (or scoring) exactly 0.0 was worth the same as an empty slot — the optimizer left slots "(empty)" while an eligible player sat there. Fixed with a tiny per-assignment bonus that breaks ties toward filling a slot, too small to ever outrank a real points difference. This affected live lineup recommendations too, not just the hindsight view.  
- **News (`ingestion/news.py`) — built.** Sleeper's app uses an **undocumented GraphQL endpoint** (`sleeper.app/graphql`, `get_player_news`) returning per-player `title` / `description` (what happened) / `analysis` (what it means for fantasy) / `url` / `published`. Sleeper's robots.txt is fully permissive (`Allow: /`), unlike the FantasyPros path we declined. Because it's undocumented and can change without notice, **every call to it lives in that one module** — a break is one file to fix, not a hunt. Cached in a `player_news` table; `sync` pulls news for my own roster only (one request per player, so the league's ~200 rostered players would make sync crawl), with `--skip-news` to opt out. Exposed as the `news` command and the agent tools `get_player_news` / `get_roster_news` (capped items-per-player — an unbounded tool is the real context risk, not the window size).
- **Deliberately stopped at surfacing news, not parsing it into signal.** §2 imagines the LLM turning news into structured signal feeding the injury overlay. The prose was kept instead: `analysis` is already written for fantasy managers, and flattening it into a `starter_out: bool` would discard the nuance ("nice matchup against a defense that just gave up 30") that makes it worth reading at all. With a 262k-token window, ~7k tokens of roster news is ~3% — compression buys nothing here. If the *injury overlay* ever needs machine-readable input, the extraction pass goes on top of this, and the split must hold: the LLM classifies text, a deterministic module computes the adjustment.
- **Progress output.** A question can take ~30s across several model round-trips (longer when the free tier forces retries), and silence that long is indistinguishable from a hang — the first thing that looked like "no answer" in real use. `ask`/`chat` now stream each tool call as it happens, to **stderr**, so piping stdout still yields just the answer.  
- **Three real bugs surfaced by live testing, all fixed:** (1) LangGraph runs tool nodes on worker threads, and SQLite connections are thread-bound by default — fixed at the source in `storage.get_connection` with `check_same_thread=False`; (2) OpenRouter reports upstream provider failures as HTTP 200 with the error in the *body*, so the OpenAI SDK's `max_retries` never sees them — free-tier overloads therefore needed an explicit retry wrapper in `orchestrator.py`, not a config flag; (3) model-generated text can contain any Unicode, which crashed the Windows cp1252 console on print — `cli.py` now forces UTF-8 on stdout/stderr, since avoiding stray characters in our own source can't help when the *model* writes the output.  
- **DoD — met.** `ask "Who should I start this week?"` routes to `recommend_lineup` and returns a lineup whose every player and total (149.5) matches `cli.py lineup` run directly. Asked for a nonexistent player's history and an all-time record, it refused both and named what it *can* retrieve instead, rather than inventing numbers. The agent recommends only — it has no write path until Stage 5.

### Stage 4 — Interface (CLI \+ email)

- CLI subcommands: `lineup`, `waivers`, `trades`, `draft`, each printing recommendations \+ reasons. **Done in Stage 3**, along with `projections`, `backtest`, `news`, `trending`, `results`, `ask` and `chat`.  
- `notify.py`: `notify(subject, body)` via Gmail SMTP (app password). Validates the app password's shape *before* opening an SMTP connection, so the common mistake (pasting an account password) produces an actionable message instead of an opaque auth failure; spaces are stripped since Google displays app passwords in four groups of four.  
- `digest.py` \+ the `digest` command: recommended lineup, a **watch list**, the waiver board, and last week's result. **Deliberately deterministic** — this is what an unattended Sunday-morning job sends, so it must not depend on a flaky free-tier model being up. The agent narrates the same data on demand via `ask`; the digest is the version that has to work alone.  
- **The watch list needed a real filter.** Listing starters "with news" produced a watch list containing the entire lineup, since every starter has news every week. It now flags only injury-adjusted players plus news whose headline suggests a decision (injury, practice status, role/snap change), biased toward false positives — one extra line in an email costs far less than missing a ruled-out starter. A test caught that the first keyword list matched "practice" but not "practi**cing**, which is how the status is actually reported.  
- Scheduled run: documented in the README as a cron / Task Scheduler pairing of `sync` then `digest --email`.  
- **DoD — half met.** `digest` prints a readable weekly report (verified against the live league). The email half is **blocked on credentials, not code**: `GMAIL_APP_PASSWORD` in `.env` is 8 characters and Gmail app passwords are 16, so `--email` stops at validation with an actionable error and sends nothing. Needs a real app password generated at myaccount.google.com/apppasswords to close out.

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

- ~~Is my draft imminent?~~ — **resolved:** already drafted, season underway. Keep the draft assistant last in Stage 3 as originally ordered (lineup -> waivers -> trades -> draft).  
- ~~My Sleeper league ID / username~~ — **resolved:** no fixed ID. The agent takes my username and discovers the league(s) itself (§2), prompting me to pick if I'm in more than one.  
- League **scoring format** (PPR / half-PPR / standard) and roster slots — pull from Sleeper settings automatically in Stage 0; I'll confirm once it prints.  
- ~~Which second projection source to start with~~ — **resolved:** Sleeper + nflverse-derived to start; FantasyPros deferred (robots.txt blocks its data endpoints, see §2), config-driven so it (or another source) can be added later (§2).  
- ~~Hosted LLM API~~ — **resolved:** OpenRouter (`nvidia/nemotron-3-super-120b-a12b:free`), orchestrated via LangGraph (§2, §3).

---

## 7\. First action for the agent

Start **Stage 0**. Scaffold the repo layout above, implement `ingestion/sleeper.py` and `interface/cli.py`, and make the DoD command work: given my Sleeper **username**, discover and (if needed) let me pick my league, then print my roster and this week's opponent, and persist league-wide state (all rosters, transactions, matchups, players) to local storage. Ask me for my Sleeper username (`.env` holds it, not a league ID). Then **honor the checkpoint protocol (§4a): stop, tell me how to test it, wait for my approval, commit Stage 0, and only then continue.**  
