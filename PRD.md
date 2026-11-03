# PRD: Fantasy Agent (Sleeper NFL fantasy assistant)

One document with what the product must do and everything the first build
taught us. It replaces the code that used to live on this branch. Sources:
`plan/PLAN.md` (original build directive), `PROJECT_LOG.md` (retrospective),
`plan/ISSUES.md` (bug and request tracker), the feature plans in `plan/`,
`TEST_QUESTIONS.md` (live regression checks) and the git history of `main`.
Where they disagree, this document has the final answer.

Terminology rule from `AGENT.md` applies throughout: **fantasy team** is a
manager's roster in a Sleeper league; **NFL team** is a real franchise. Never
a bare "team".

---

## 1. Problem and goal

Managing a Sleeper fantasy league well every week takes projections, injury
news, waiver math, lineup rules and lock times. The product is an
**LLM-orchestrated agent** that does this for one manager: it reads the
league, produces player projections with uncertainty, recommends lineups,
waiver claims, trades and draft picks, explains why, and enacts a move only
after the manager approves it.

**The agent orchestrates; tools compute.** The LLM decides which tools to
call, reads news and explains. Every number comes from a deterministic tool.
If the model ever needs to estimate something, a tool is missing.

### Users

- **Primary:** the developer-owner, managing their own leagues (one IDP,
  superflex, full-PPR league plus a test league).
- **Secondary:** non-technical friends in other leagues (including non-IDP
  leagues) who will not install Python or use a terminal. They need a
  one-download app and a setup form.

### Non-goals

- Autonomous moves without a human approving the exact change.
- Trades through the API (proposing/accepting reaches another manager and can
  execute immediately).
- Multi-user hosting with logins. Each install is single-user and local.
- Paid data sources or scraping sites that disallow it.

---

## 2. Principles (proven in the first build)

1. **Agent orchestrates, tools compute.** No arithmetic in model prose: not
   differences, not sums, not "edges out by 0.2". The prompt forbids combining
   tool outputs arithmetically.
2. **Give the model facts, not rules.** Every time the model answered from
   stale training memory (wrong year, DK Metcalf on Seattle, invented player
   roles), adding a prompt rule did not fix it. Adding a tool that returned
   the fact, or putting the fact in the system prompt, did.
3. **Freshness is something the model receives, not something it decides to
   fetch.** Data the answer depends on (date, season, week, my roster) goes
   into the system prompt, rebuilt every model turn.
4. **Evaluation gates the projection engine.** A projection change ships only
   if the backtest beats the Sleeper-only baseline in two independent seasons.
5. **Mean and variance, always.** Uncertainty drives start/sit and bids.
6. **One projection artifact** (mean + variance per player per week) feeds
   every decision module. No projection logic inside decision modules.
7. **Brain before hands.** Read-only until decisions are trustworthy; writes
   last, behind an approval gate that cannot be bypassed.
8. **Read eligibility from Sleeper, don't reimplement it.** Lock and slot
   rules come from Sleeper's own data.
9. **Unattended jobs must not depend on the model.** The weekly digest is
   deterministic.
10. **Isolate undocumented APIs.** Each one lives in a single module, so a
    break means fixing one file.
11. **Keep it simple.** No speculative features, no defensive code for cases
    that can't happen, minimal docs, no emojis.

---

## 3. Surfaces

| Surface | For | Can do |
|---|---|---|
| Desktop app (Windows installer, Mac dmg) | Non-technical users | Setup form, league picker, sync, chat |
| Web app in Docker (localhost only) | Users with Docker | Same as desktop |
| CLI | Owner/developer | Everything: deterministic commands, chat, digest, draft assistant, backtest, and all Sleeper writes behind APPROVE |

- Web and desktop are the same page: a Next.js static export served by a
  FastAPI backend at `/`. Desktop wraps it in a native window (pywebview) on a
  free local port.
- **Writes are CLI-only.** The agent has no write tools, so the web page needs
  no approval flow. In-app, the agent tells users to make changes in the
  Sleeper app; on the CLI it names the exact command.
- Web and CLI share one chat thread and history.

---

## 4. Functional requirements

### 4.1 Setup and leagues

- The user gives a **Sleeper username**, never a league ID. Resolve
  username -> user_id -> leagues for the season.
- One league: use it. Several: ask which one; never guess. Remember the active
  league.
- Switch leagues by name (CLI `use`, agent tool `switch_league`, web picker).
  An unknown league name fails and lists the real ones, even when the user has
  only one league.
- The season is auto-detected; an empty setting means auto.
- Settings (username, OpenRouter key, optional Sleeper token, model) are
  entered in the page, saved to a local settings file and applied without a
  restart. Keys are never echoed back.
- Each user brings their own OpenRouter key. The base URL is a setting, so a
  shared key later only needs a proxy.

### 4.2 Data and freshness

Stored locally (SQLite), league-wide, with timestamps:

- players catalog (ids, names, positions, NFL team, injury status, depth chart);
- **every** fantasy team's roster: players, starters, IR (`reserve`);
- matchups and results, including per-player actual points (`players_points`);
- transactions; draft picks; projections per source; news.

Freshness rules:

| What | When |
|---|---|
| Full sync (league, rosters, matchups, news for my roster, caches cleared) | Before a question when the last full sync is over 2h old; on demand |
| Rosters, current-week matchups and transactions (3 requests) | Before **every** question |
| Players catalog (injury designations) | 6h TTL (Sleeper asks for about once a day) |
| Week rollover | Picked up mid-chat |

- A failed auto-sync is reported and the answer proceeds on stored data.
- Rosters come from Sleeper's **GraphQL** `league_rosters` (live, no token).
  The REST endpoint is Cloudflare-cached for up to ~10 minutes, so a move made
  in the app would otherwise be invisible.
- Each roster save compares my roster before/after and records what changed
  ("added X; dropped Y", "lineup changed"). The first save is not a change.
- Schema changes migrate existing databases; users never delete their DB.

### 4.3 Projection engine

- **Pluggable sources** behind one interface, each with a weight in config. A
  source with weight 0 is off. The blend tolerates any subset. The owner's own
  ML model is a planned future source and must drop in without touching the
  blend or decisions.
- Current sources: **Sleeper projections** and an **nflverse-derived** source
  (recent box scores). FantasyPros is excluded: its data endpoints are
  disallowed by its robots.txt; it needs a paid API key.
- Output per player per week: blended mean and variance (with a variance
  floor when only one source exists).
- Scoring uses the league's own scoring settings, including IDP stat keys.
- **Injury overlay:** discount by designation (Q/D/O/IR...), bump backups when
  a starter is ruled out.
- **Matchup adjustment: built, disabled by default.** It does not beat plain
  blending (see section 6). Keep it scored by the backtest so a rework gets an
  immediate verdict.
- **Season projections** come from Sleeper's season endpoint, scored the same
  way. Default horizon is **rest-of-season**, prorated by regular-season weeks
  remaining (ignores where byes fall; at most one week of error). Full season
  stays available. No matchup adjustment; only season-long designations (IR)
  apply.
- **IDP:** roster-slot eligibility maps slots to granular positions (`DB`
  accepts CB/S/FS/SS, DE/DT/NT pool into `DL`). nflverse defensive columns are
  translated to Sleeper's `idp_*` keys.
- **League format is derived, never assumed:** PPR/half/standard, TE premium,
  superflex, IDP only when DL/LB/DB/IDP_FLEX slots exist, team DEF, no kicker.

### 4.4 Decisions (each returns structured data plus a "why")

- **Lineup:** an ILP (not greedy) maximizing expected points under slot
  rules. It must:
  - start from the lineup currently set in Sleeper, with a small keep-bonus so
    equal-points reorderings never show up as changes;
  - respect locks: locked starters stay, locked bench players can't come in;
  - add a tiny fill-bonus so a 0.0 player still fills a slot over "(empty)";
  - never start a player in an IR slot, and say when an IR-slotted player is
    no longer IR-eligible;
  - give per-starter facts for the explanation: NFL team, std dev, who each
    changed slot replaces, and the latest news on players in changed slots.
- **Waivers / FAAB:** rank unrostered players by value over replacement
  (VORP). Replacement level per position = Nth-best rostered player, where N =
  fantasy teams x the position's slot demand (flex slots split credit).
  - positions with no slot demand are excluded (else a team DEF in an IDP
    league reads as pure value);
  - rostered players projected 0 (Out/IR/bye) are excluded from the
    replacement pool (else a TE baseline of 0.0 made every free-agent TE look
    great);
  - two horizons: this week and rest-of-season; show both when the user
    doesn't say which;
  - weekly targets exclude players whose game started or who are on bye, and
    say how many were excluded;
  - the FAAB bid is a labeled guide (VOR x 4%), sized on per-week VOR for the
    season horizon.
- **Injured stash:** unrostered players with a long-term designation, ranked by
  **healthy** rest-of-season projection, each with the latest news for the
  return timeline.
- **Trades:** change in total projected value, default rest-of-season.
- **Draft assistant:** once activated it polls the live draft, keeps the board,
  and when it is my pick it pushes a ranked suggestion unprompted. Roster need
  counts: a player filling an open starting slot keeps full VOR, depth gets
  half. Advises only; never auto-picks.
- **Week results:** both scores, win/loss/tie, opponent's name and top
  scorers, best lineup in hindsight ("points left on bench"). Defaults to the
  last completed week.
- **Season-to-date leaders:** actual points under league scoring, games played
  and per-game averages, from Sleeper's `/stats` (same keys and ids as
  projections, IDP included, no crosswalk). Verified 807/807 player-weeks
  against the league's own `players_points`.

### 4.5 The agent

- LangGraph loop: `plan -> (tools -> plan)* -> end`. The system prompt is
  rebuilt on **every model turn** with today's date, season, week, league,
  fantasy team name, league format, other leagues, saved preferences, and the
  **MY ROSTER** block (starters by slot, bench, IR, refresh time, last change).
  MY ROSTER is declared authoritative over anything earlier in the chat.
- Only the last 40 messages go to the model. Results from roster-dependent
  tools asked **before** the latest roster change are replaced with an
  "outdated" marker in what is sent (not in storage), so the model can't repeat
  them.
- Tools (read-only), each docstring saying what it covers, what it doesn't, and
  which tool to use instead:

  | Tool | Answers |
  |---|---|
  | `get_league_state` | season, week, opponent, standings |
  | `get_my_roster` | the lineup **set in Sleeper** ("who are my starters") |
  | `get_fantasy_team_roster` | another manager's fantasy team |
  | `get_nfl_team_roster` | an NFL team's players by depth chart, each labeled my roster (starter/bench/IR) / other fantasy team / free agent, with an ownership summary up front |
  | `get_recent_transactions` | league moves |
  | `get_projections` / `get_season_projections` | weekly / rest-of-season |
  | `get_season_to_date_leaders` | actual points so far |
  | `recommend_lineup` | what the lineup **should** be |
  | `get_waiver_targets` | waiver board, week or season horizon |
  | `get_injured_stash_candidates` | injured free agents worth holding |
  | `evaluate_proposed_trade` | trade value delta |
  | `get_player_news` / `get_roster_news` | news for any NFL player / my roster (capped per player) |
  | `get_trending_players` | Sleeper-wide adds/drops (context, never a projection input) |
  | `get_week_results` | recap |
  | `get_pending_claims` | my pending waiver claims (needs token) |
  | `sync_league` / `switch_league` | refresh / change league |
  | `remember_preference` / `forget_preference` | durable user preferences |

- Prompt rules that must hold:
  - never claim a different year or call the current season unavailable;
  - report tool failures as failures;
  - never list an NFL team's players, or a player's NFL team, from memory;
  - "who are my starters" = Sleeper's lineup, not the recommendation;
  - reasons must each rest on a tool fact (where player facts come from is
    stated, not just "don't invent");
  - say "fantasy team" or "NFL team" wherever it could be ambiguous.
- **Player name resolution** ranks exact full name, then rostered in the
  league, then on an NFL team; returns candidates instead of guessing; accepts
  "Name (POS/TEAM)".
- **Memory:** chat persists across runs (SQLite checkpointer); "new chat"
  clears it. Preferences are stored in the league DB and injected in the prompt.
- **Progress:** a question takes 10-60s over several model calls. Show each
  tool call live (CLI: stderr, so stdout pipes clean; web: current step and
  elapsed time). Silence looks like a hang.

### 4.6 Writes to Sleeper

Through Sleeper's undocumented GraphQL API with the user's token, CLI only:

| Action | Mutation |
|---|---|
| Set lineup | `update_matchup_leg` **and** `roster_update_starters` (see open item) |
| IR slots | `roster_update_reserve` (full list) |
| Free-agent add/drop | `league_create_transaction` (type `free_agent`) |
| Waiver claim | `submit_waiver_claim` (bid only in FAAB leagues, checked against budget) |
| Cancel claim | `cancel_waiver_claim` |
| Pending claims (read) | `league_transactions_filtered` (status `pending`) |

Every write:

- is built from player **ids**, shows names, and shows exactly what will change;
- waits for the user to type `APPROVE` (not `y`); there is no `--yes`/`--force`;
- is bound to a hash of the exact payload shown; an approval can't be
  constructed elsewhere, reused or applied to a changed payload;
- fails early with a clear message if the token is missing;
- is read back through a **different** GraphQL query (never cached REST) and
  reported as `VERIFIED` / `MISMATCH` / `SENT, could not verify`;
- resolves names only against a known pool (my roster, or unrostered players
  with an NFL team);
- warns when a drop leaves a starting slot empty.

Lineup writes are limited to the current week and keep locked players where
they are. The GraphQL client sends only named operations defined in our code,
so betting and payment operations are unreachable by construction. The token is
full account access for about a year: stored locally, never logged, never a
tool argument, sent only to sleeper.com.

### 4.7 Weekly digest and email

- Deterministic: recommended lineup, waiver board, last week's result, and a
  **watch list** of injury-adjusted players plus news whose headline suggests a
  decision (injury, practice status, role/snap change). Match on word stems
  ("practi" catches "practicing"); bias toward false positives.
- Email via Gmail SMTP with a 16-character **app password**; validate its shape
  before connecting and say how to get one. Missing Gmail settings only affect
  the email command.
- Scheduled by the OS (cron / Task Scheduler): sync, then digest.

### 4.8 Observability

- LangSmith tracing, off by default, enabled by env vars. Each question is one
  trace: model turns, tool calls and results, tokens, latency, retries, tagged
  with command, model, league, season, week. The pre-question refresh is its
  own span. Tests never trace.
- Traces contain league, fantasy team and manager names and preferences; the
  Sleeper token never appears.

### 4.9 Packaging and release

- Docker: one multi-stage image (Node builds the page, Python with uv runs
  FastAPI), port bound to `127.0.0.1` only (no login, stored keys), data in a
  named volume, start/stop scripts wrapping docker compose (`.sh` and `.bat`).
- Desktop: PyInstaller one-folder bundle (starts fast, fewer antivirus flags),
  per-user install without admin (Inno Setup on Windows, dmg on Mac). Data in
  the user's app-data folder, with a log file since there is no console.
  Unsigned: document the "More info, Run anyway" / "Open Anyway" steps.
- CI: push to `main` publishes amd64+arm64 images to GHCR; a `v*` tag builds
  the installers and creates the release.

### 4.10 Model access and failure handling

- OpenRouter, free models. Settings offers a short list with a recommended
  default and one-line notes (larger models handle multi-step questions
  better; one non-Nvidia option for when Nvidia's are down).
- OpenRouter returns upstream failures as **HTTP 200 with an error body**:
  retry on the body text, not the status.
- Short-term 429s are retried. The **daily free quota (about 50 requests)**
  fails fast with a clear message (wait, add credits, or pick another model),
  and the web page puts the failed question back in the input.
- Force UTF-8 output on Windows consoles: model text contains any Unicode.

---

## 5. Data sources and API facts

| Source | Use | Facts |
|---|---|---|
| Sleeper REST v1 (documented, keyless) | league, users, players, drafts, past weeks, trending | Rosters/transactions cached `s-maxage=300` + stale 300; matchups 60s |
| Sleeper `/projections`, `/stats/nfl/<season>/<week>`, `/schedule/nfl/regular/<season>` (undocumented) | projections, actual stats, game state | Stats use the projection keys. Schedule gives per-game `status` (`pre_game`, `complete`), date only. A canceled game is listed next to its rescheduled replacement: skip it |
| Sleeper GraphQL `sleeper.com/graphql` (undocumented, mapped publicly Sept 2026) | live rosters, news, writes, pending claims | Needs a User-Agent (403 without). Use variables, never string interpolation. Introspection open (~240 queries, ~350 mutations) |
| nflverse release assets (read directly) | historical box scores for the nflverse source and backtest | `nfl_data_py` is unmaintained and stops at 2024. Filter to fantasy positions (raw data includes linemen/punters) |
| ID crosswalk Sleeper <-> nflverse | nflverse source, backtest | `gsis_id` alone matched ~15%. Fall back to name+team+position, then a name unique among active players; normalize suffixes and team codes (`LA`=`LAR`). Coverage ~98% |
| News | per-player title, description, fantasy analysis, url | Surface the prose; don't parse it into flags. Sync pulls news for my roster only |

Not usable without external access: X/Twitter beat writers (paid API; list in
`Bitwriters.md`; first check whether Sleeper's news already sources them),
Reddit (noisy; sentiment context only if ever), FantasyPros (paid key), The
Athletic (paywalled).

---

## 6. Evaluation

### Backtest (weeks 3-17, skill positions, MAE vs. Sleeper-only)

| Tier | 2024 | 2025 |
|---|---|---|
| Sleeper only (baseline) | 4.56 | 4.35 |
| + blend with nflverse | 4.40 (-3.6%) | 4.19 (-3.7%) |
| + blend + matchup | 4.58 (+0.4%) | 4.34 (-0.2%) |

N about 4,800-5,200 per tier. Blending earns its place; matchup does not
(a 24-combination shrinkage/window sweep never beat plain blend). An earlier
table showed -7.5% for blending, but it was measured on the 15% crosswalk
sample: the harness caught its own bad ground truth twice.

Not backtested: the injury overlay (only current injury state is stored;
scoring past weeks would leak) and IDP (an IDP's value depends on the opposing
offense, a separate model).

### Tests

- About 200 unit tests. The ones that mattered: adversarial approval-gate tests
  (forge, reuse, sidestep), the backtest as a standing regression guard, and
  real-text fixtures (actual headlines) for news and lock detection.
- `TEST_QUESTIONS.md`: live questions for model behavior. A correct answer
  reached without the right tool call is a **fail** (it came from memory). A
  full pass uses about 40 of the 50 daily free requests.

---

## 7. Lessons that should shape the rebuild

- **The free model is the main quality limit.** It sometimes does prose
  arithmetic, contradicts the numbers it just quoted, resolves ambiguous names
  without saying so, compresses away per-line labels in long tool output, and
  gets confused on long multi-step tasks. Mitigations that worked: put
  summaries first in tool output, label facts directly ("MY ROSTER (bench)"),
  give one tool per question type. The remaining lever is a stronger model.
- **Concurrency:** LangGraph runs tools on worker threads; SQLite connections
  must allow cross-thread use.
- **Lineup diffs:** slots repeat (2 RB, 2 WR), so compare positionally / per
  slot type, never via a dict keyed by slot name.
- **Locks:** check both sides of a swap (you can't bench a player who played,
  and can't start one who has). Check before asking for approval, not after.
- **Browser automation was a dead end** (bot detection, cookie overlays,
  selectors, surname matching). GraphQL replaced it; don't go back.
- **"Starters with news" is everyone.** Filters need a decision-relevance test.
- **Gmail account passwords no longer work for SMTP** (since 2022); only app
  passwords.
- **PyInstaller misses modules loaded by name** (pandas loads fastparquet that
  way): declare them as hidden imports, or a projection source silently drops
  out.
- **Scoring settings must come from the active league,** not the first league
  in the table.

---

## 8. Open items

| Item | Status |
|---|---|
| Which lineup mutation Sleeper scores from | Unresolved: two public reverse-engineerings disagree, so both are sent and both read back. Test question 23 settles it; then drop the other |
| Live checks needing a token or a game in progress | Test questions 4, 7, 20-26 not yet run |
| In-game schedule `status` value | Capture during a live game and record it |
| IR in season projections | Treated as 0 for the rest of the season, not 0 until a return week (needs a return-week estimate) |
| Matchup adjustment | Disabled until a rework beats plain blend in both seasons |
| IDP matchup model | Not built |
| nflverse source rebuilt on Sleeper stats | Would remove the crosswalk and IDP translation; ships only if the backtest still beats Sleeper-only |
| Taxi squads | Shown as bench |
| Mac build | Built by CI, never run by hand |
| Code signing, auto-update | Not done |
| Sleeper terms of use for GraphQL writes | Not reviewed |
| LangSmith evaluations from `TEST_QUESTIONS.md` | Later, once tracing has been in use |
| Two-way access away from a desk (e.g. a chat bot) | Idea only |

---

## 9. Technical decisions

From `AGENT.md`: Next.js frontend; Python FastAPI backend serving the static
site at `/`; Docker with uv; OpenRouter for model calls; SQLite, created if
missing; start/stop scripts as docker compose wrappers. Plus, from the first
build: Python 3.11+, LangGraph (the only agent framework), pulp for the lineup
ILP, pytest, pywebview + PyInstaller for desktop.

Colors: accent yellow `#ecad0a`, blue primary `#209dd7`, purple secondary
`#753991` (submit buttons), dark navy `#032147` (headings), gray `#888888`
(supporting text).

Model: `AGENT.md` names `openai/gpt-oss-120b:free`, but OpenRouter made it
paid-only mid-project. The build used `nvidia/nemotron-3-super-120b-a12b:free`
as the default and later recommended `nvidia/nemotron-3-ultra-550b-a55b:free`
in Settings. Re-check what is free and emits tool calls before choosing; pin a
specific model rather than an auto-router so behavior is reproducible.
