# PRD: Fantasy Agent (Sleeper NFL fantasy assistant)

What the product must do, and what the first build taught us. Sources:
`plan/PLAN.md` (original build directive), `PROJECT_LOG.md` (retrospective),
`plan/ISSUES.md` (bug and request tracker), the feature plans in `plan/`,
`TEST_QUESTIONS.md` (live regression checks) and the git history of `main`.
Where they disagree, this document wins.

## How to read this

- **Must** is a requirement. **Should** is the expected default; the team may
  depart from it with a reason. **May** is optional.
- **"First build:"** notes record what was built before and why. They are
  evidence and guidance, not requirements. The team owns architecture, module
  layout, tool names and boundaries, thresholds, schemas and UI design, and may
  replace any of it with something better.
- Sections 5-7 (API facts, evaluation results, lessons) are findings. Treat
  them as known pitfalls to avoid, not designs to copy.
- Fixed constraints are only those in section 9 (from `AGENT.md`) and anything
  marked **must**.

**v1 scope:** this PRD describes the full product. The first release is
narrower: read-only (no moves), Windows desktop plus Docker, no Android, Mac,
CLI, draft assistant, digest or preferences. `plan/PLAN.md` defines v1.

Terminology rule from `AGENT.md` applies throughout: **fantasy team** is a
manager's roster in a Sleeper league; **NFL team** is a real franchise. Never
a bare "team".

---

## 1. Problem and goal

Managing a Sleeper fantasy league well every week takes projections, injury
news, waiver math, lineup rules and lock times. The product is an
**LLM-orchestrated agent** that does this for one manager: it reads the
league, produces player projections with uncertainty, recommends lineups,
waiver claims, trades and draft picks, explains why, and **carries out the
moves** (lineup, IR, adds/drops, waiver claims, trade offers) once the manager
confirms them.

**The agent orchestrates; tools compute.** The LLM decides what to do, reads
news and explains. Every number comes from deterministic code. If the model
needs to estimate something, a tool is missing.

### Users

- **Primary:** the developer-owner, managing their own leagues (one IDP,
  superflex, full-PPR league plus a test league).
- **Secondary:** non-technical friends in other leagues (including non-IDP
  leagues) who will not install Python or use a terminal. They need a
  one-download app and a setup form, on a computer or an Android phone.

### Non-goals (for now)

- Moves made without the manager confirming that exact move (see 4.6 for how
  autonomy could be added later).
- Hosting for many users with accounts and billing. The product serves one
  manager per install (the Android app may still need a backend somewhere,
  see 4.9).
- Paid data sources, or scraping sites that disallow it.

---

## 2. Principles (proven in the first build)

1. **Agent orchestrates, tools compute.** No arithmetic in model prose: not
   differences, not sums, not "edges out by 0.2".
2. **Only what it was given.** Every fact, reason and prediction the agent
   states must come from information the product supplied in that
   conversation (tool results, the prompt's context, the user's own words).
   It must not draw on its training knowledge about players, NFL teams,
   roles, coaches, schemes, matchups or likely outcomes, even when that
   knowledge seems right: it is stale and can't be checked. When the
   information isn't there, the agent says so ("I don't have target share for
   him") instead of filling the gap. The model's own contribution is choosing
   tools, weighing the facts it was given, and explaining them. Its training
   knowledge is used only to phrase the answer, never as a source of facts or
   definitions (including fantasy concepts such as VORP or FAAB).
3. **Give the model facts, not rules.** Every time the model answered from
   stale training memory (wrong year, DK Metcalf on Seattle, invented player
   roles), a new prompt rule did not fix it. A tool returning the fact, or the
   fact placed in the prompt, did.
4. **Freshness is something the model receives, not something it decides to
   fetch.** Data every answer depends on (date, season, week, my roster) should
   be in the model's context on every turn.
5. **Evaluation gates the projection engine.** A projection change ships only
   if it beats the current baseline in a backtest over two independent seasons.
6. **Mean and variance, always.** Variance is used when a high-risk,
   high-reward choice is wanted (see Lineup in 4.4).
7. **One projection artifact** feeds every decision. No projection logic
   duplicated inside decision code.
8. **Nothing is written without the manager's confirmation of that exact
   change**, and that rule must be enforced in code, not in the prompt.
9. **Read eligibility from Sleeper, don't reimplement it.** Lock and slot rules
   come from Sleeper's own data.
10. **Unattended jobs must not depend on the model.** The weekly digest is
   deterministic.
11. **Isolate undocumented APIs** behind one boundary each, so a break is one
    place to fix.
12. **Keep it simple.** No speculative features, no defensive code for cases
    that can't happen, minimal docs, no emojis.

---

## 3. Surfaces

The product must offer:

- **A desktop app** (Windows and Mac, one download, no terminal) for
  non-technical users.
- **An Android app** (installable from a single download or the Play Store)
  with the same chat, setup and move confirmation as desktop, usable on a
  phone screen.
- **A web app in Docker**, reachable only from the user's own machine.
- **A CLI** for the owner/developer: deterministic commands, chat, digest,
  draft assistant, backtest.

All of them must let the user chat with the agent **and confirm moves**. Chat
history should be shared between surfaces on the same install or account.

The Android app should also notify the manager (may be a later phase): a
starter ruled out before lock, a pending decision before waivers run, an
incoming trade offer, and the draft assistant's "you're on the clock". A
notification may open the chat with the suggested move ready to confirm.

First build: the web and desktop apps were the same Next.js static page served
by FastAPI; desktop wrapped it in a pywebview window on a free local port.
Writes were CLI-only then; this PRD moves them into every surface.

---

## 4. Functional requirements

### 4.1 Setup and leagues

- The user must give a **Sleeper username**, never a league ID. The product
  finds their leagues for the season.
- One league: use it. Several: ask which one; never guess. Remember the choice.
- The user must be able to switch leagues by name in chat and in the UI. An
  unknown league name must fail and list the real ones.
- The season should be auto-detected.
- Settings (username, model key, Sleeper token, model) must be enterable in the
  app and take effect without a restart. Keys must never be shown back.
- Each user brings their own model API key. The design should leave room for a
  shared key later (first build: a configurable base URL, so a proxy could hold
  the key).

### 4.2 Data and freshness

The product must keep league-wide state, not just the user's fantasy team:
the players catalog (positions, NFL team, injury status, depth chart), every
fantasy team's roster (players, starters, IR), matchups and results with
per-player actual points, transactions, draft picks, projections and news,
with enough history to answer questions about past weeks.

Freshness requirements:

- An answer about **my roster or lineup** must reflect a move made in the
  Sleeper app moments earlier, without the user asking to sync.
- Data must be no more than **2 hours** old when a question is asked, and no
  more than **2 minutes** old on game days (any day with an NFL game) while
  the app is open, when inactives are announced shortly before kickoff. Each
  source is refreshed within its own rate limits and terms: Sleeper asks apps
  to fetch its large players catalog about once a day, so the 2-minute target
  is met by lighter sources (injuries, inactives, live rosters, scores). Where
  no usable source meets a target, the answer states how old the data is.
- **"This week"** is the next week to be played: once the last game of week N
  is complete, "this week" is week N+1 (on the Tuesday after week 9, it is
  week 10). "Last week" is the last completed week. The rollover must be
  picked up in a running session.
- The product must know each NFL game's kickoff time and status (not started,
  in progress, complete), for locks, "his game already started" and waiver
  exclusions.
- A failed refresh must be reported, and the answer may proceed on stored data.
- When my roster changes between questions, the agent must know what changed
  and must not repeat earlier answers based on the old roster.
- Upgrading the app must not require users to delete their data.

First build: a full sync before a question when the last one was over 2h old;
rosters, current-week matchups and transactions re-pulled before every
question (3 requests); players catalog on a 6h TTL (Sleeper asks for about
once a day). Rosters came from GraphQL `league_rosters`, because the REST
endpoint is cached for up to ~10 minutes. Each roster save diffed my roster
and recorded "added X; dropped Y". Older roster-dependent tool results in the
chat were replaced with an "outdated" marker before being sent to the model.

#### NFL teams

The product must know every NFL team's current state, not only the players
rostered in fantasy leagues:

- **Rosters:** who is on each NFL team now, including recent signings,
  releases and trades. The agent must never fall back on its training data for
  this (the first build's model put DK Metcalf on Seattle years after he left).
- **Injury updates:** each player's designation (Questionable, Doubtful, Out,
  IR, PUP...), practice participation through the week, and game-day
  inactives, refreshed often enough to act on before lock.
- **Lineup and depth order:** who starts and in what order at each position
  (QB1, RB1/RB2, WR1/WR2/WR3, TE1, and IDP starters where relevant), shown
  as labels like "WR2". Where possible, also show usage (snap share,
  target share, carries) so the order reflects the real role, not only the
  official depth chart. The answer must say which one it is based on.
- **Changes:** when a starter goes down, the agent should name who moves up,
  from the depth order. This is information for the manager; it does not
  change projections (4.3).

Each fact should carry its source and how fresh it is, so the agent can say
"per the team's injury report, Thursday".

First build: NFL team rosters came from Sleeper's players catalog
(`players.team`, `depth_chart_order`), shown by position with each player's
fantasy status. It had no practice reports, inactives or usage data, and the
depth order was Sleeper's, which lags real roles.

#### Multiple sources

- The product must be able to combine information from several sources, for
  projections, NFL rosters and depth charts, injuries, news and usage. Adding
  or removing a source should not require changing the code that uses the
  data.
- When sources disagree, the product should prefer the freshest
  authoritative one (for injuries, the official report), and may show the
  disagreement ("Sleeper still lists him as RB1; his snap share fell to 30%").
- Every source must be legal to use: a documented or public API, a licensed
  feed, or a site whose terms and robots.txt allow it. Candidate sources are
  in section 5.
- A source that is not accessible (blocked, paid, or its terms don't allow
  the use) is not used. The product works without it, and the agent states
  the resulting gap.

### 4.3 Projection engine

- **Pluggable sources:** adding a source (including the owner's own ML model,
  planned) must not require changing the blend or decision code. Each source's
  weight is configurable, and any subset of sources must work.
- Output per player per week: a **mean and a variance**.
- Scoring must use the league's own scoring settings, IDP included.
- League format must be **derived from the league, never assumed:** PPR/half/
  standard, TE premium, superflex, IDP (only when IDP slots exist), team DEF,
  kicker or not.
- Injuries affect projections only when a player will not play: ruled out of
  the next game (Out, PUP, suspended), his projection for that game is 0; on
  IR, 0 until the expected return week, then his healthy projection (0 for all
  remaining weeks when no return date is known). Questionable and Doubtful
  players keep their projection; their status and news are shown, not priced
  in. Backups are not bumped.
- **Supported leagues:** season-long redraft leagues, with these options:
  - defense: IDP (individual defensive players) or team DEF;
  - scoring: PPR, half-PPR or standard.

  Roster settings within those leagues (superflex, TE premium, kicker or not,
  bench, IR and taxi sizes) are read from the league as they are. Every other
  league type (dynasty, keeper, best ball, guillotine) is detected from the
  league settings at setup and reported as not supported yet, by name.
- Scoring uses only the stats a source actually projects. League scoring
  items no source projects (for example, a 100-yard bonus) are listed in the
  tool output, so the agent can state what a projection leaves out.
- Season projections must default to **rest-of-season**, through NFL week 18
  regardless of the league's playoff schedule; full season should stay
  available. Rest-of-season projections must be evaluated like weekly
  ones (section 6), since trades, stash and the rest-of-season waiver horizon
  depend on them.
- The matchup adjustment must stay off until it beats the blend (section 6).

First build: two sources, Sleeper projections and an nflverse-derived one
(recent box scores). FantasyPros was excluded because its robots.txt
disallows its data endpoints; it needs a paid key. Variance had a floor when
only one source applied. Season projections came from Sleeper's season
endpoint, prorated by weeks remaining, with only season-long designations (IR)
applied. IDP slots mapped to granular positions (`DB` accepts CB/S/FS/SS;
DE/DT/NT pooled into `DL`).

### 4.4 Decisions

Each must return structured results plus a human-readable "why" grounded in
facts (projection, variance, injury, matchup, replacement level, news).

- **Lineup:** the best lineup by expected points under the league's slot rules.
  It must start from the lineup already set in Sleeper and not propose
  equal-value reshuffles, respect game locks (locked starters stay, locked
  bench players can't come in), never leave a slot empty when an eligible
  player exists, never start a player in an IR slot, and flag IR-slotted
  players who are no longer IR-eligible. By default it maximizes expected
  points. It must also offer a high-risk, high-reward lineup that uses
  variance, chosen when the manager asks for it or when the agent judges it
  fits the matchup (for example, my fantasy team is a clear underdog). The
  agent judges from numbers a tool provides (both fantasy teams' projected
  totals, their spread and a win probability), never from its own
  arithmetic, and says which kind of lineup it gave and why.
- **Waivers / FAAB:** rank available players by value over replacement, on two
  horizons (this week, rest of season), shown both when the user doesn't say
  which. Weekly targets must exclude players whose game started or who are on
  bye. Bids must respect the league's waiver type and remaining budget.
- **Injured stash:** unrostered injured players ranked by healthy
  rest-of-season value, with the latest news on their return.
- **Trades:** evaluate any proposed trade by value change, default
  rest-of-season. The product should also **find** trade ideas: for a target
  player or a positional need, suggest offers to specific fantasy teams that
  improve my value while plausibly helping theirs.
  - Asked about a specific fantasy team, look only at that one. Asked in
    general, first ask which of my players I am willing to trade.
  - "Plausibly helping theirs" means the trade raises the value of their
    starting lineup, computed by a tool, not only their raw roster value.
  - Respect the league's trade deadline: after it, evaluate on request but
    don't suggest trades, and say the deadline has passed.
  - Uneven trades (2-for-1 and similar) must count roster spots: the side
    receiving more players must drop someone (the lowest-value bench player
    unless told otherwise), and the side receiving fewer gains an open spot
    valued at the best available free agent. Both are shown in the result.
- **Draft assistant:** once activated, follow the live draft and push a ranked
  suggestion when it's my pick, weighted by my open starting slots.
- **Week results:** both scores, result, opponent and their top scorers, and
  points left on my bench. Defaults to the last completed week.
- **Season-to-date leaders:** actual points under league scoring, games played,
  per-game averages.

First build: lineup as an ILP (pulp), with a tiny keep-bonus (no fake
reshuffles) and fill-bonus (a 0.0 player beats an empty slot). VORP
replacement level = Nth-best rostered player, N = fantasy teams x slot demand,
flex slots splitting credit; positions with no slot demand excluded; rostered
0-projection players excluded from the pool (else replacement levels read
0.0). FAAB guide = VOR x 4%. Draft: depth picks counted at half VOR. Leaders
from Sleeper `/stats`, which matched the league's own scores on 807/807
player-weeks. Trade finding was not built.

### 4.5 The agent

- The agent must have the context every answer depends on, refreshed on every
  model turn: today's date, season, week, league, my fantasy team, league
  format, my current roster and lineup as set in Sleeper, recent changes, and
  saved preferences.
- It must cover these question types, each with a tool (or equivalent) so it
  never answers from memory:

  | Need | Notes |
  |---|---|
  | League state | season, week, opponent, standings |
  | My roster as set in Sleeper | "who are my starters" means this, not the recommendation |
  | Another fantasy team's roster | |
  | An NFL team's roster, depth order and injury report | current players in order (WR1, WR2...), injuries and practice status, usage where available, each player's fantasy status (mine starter/bench/IR, other fantasy team, free agent) |
  | Transactions, pending claims, open trade offers | |
  | Weekly and rest-of-season projections; season-to-date actuals | |
  | Recommended lineup, waiver targets, injured stash, trade evaluation and ideas | |
  | News for any NFL player; my roster's news | |
  | Trending adds/drops across Sleeper | context only, never a projection input |
  | Week results | |
  | Sync, switch league | |
  | Remember / forget a preference | |
  | **Moves** | see 4.6 |

- Behavior that must hold:
  - never claim a different year or call the current season unavailable;
  - report tool failures as failures;
  - use no knowledge of its own (principle 2): every fact, reason and
    prediction traces to supplied information, and a gap is stated as a gap;
  - never state an NFL team's players, or a player's NFL team, from memory;
  - say "fantasy team" or "NFL team" wherever it could be ambiguous;
  - for an ambiguous player name, ask or say which player it assumed.
- Chat history must persist across sessions, with a way to start fresh. Each
  league has its own independent chat history.
  Preferences must persist and influence answers.
- A question can take a minute. The UI must show progress (current step,
  elapsed time); silence looks like a hang.

First build: a LangGraph loop (`plan -> tools -> plan`), system prompt rebuilt
every model turn with a MY ROSTER block declared authoritative, last 40
messages sent, about 20 read-only tools whose docstrings said what each
covers, what it doesn't, and which tool to use instead. Name resolution
ranked exact name, then rostered in the league, then on an NFL team, and
returned candidates instead of guessing.

### 4.6 Moves: the agent acts in Sleeper

The agent must be able to carry out moves itself, from chat, in every surface:

| Move | Must support |
|---|---|
| Lineup | set starters for the current week, respecting locks |
| Roster slots | move players into and out of IR (and taxi, where the league has it) |
| Free-agent add/drop | add, drop, or both in one move |
| Waiver claims | submit with optional drop and FAAB bid; list and cancel pending claims |
| Trades | propose a trade to another fantasy team; list incoming and outgoing offers; accept, reject or counter an incoming offer; withdraw my own |
| Draft | may: make my pick when explicitly asked |

Confirmation (must):

- Each move is shown to the manager as a concrete change (who goes in, who
  goes out, which slot, which bid, which fantasy team) and runs only after the
  manager confirms **that exact change**. If anything changes after it was
  shown, it must be confirmed again.
- The model must not be able to confirm on the manager's behalf. The check must
  live in code the model cannot reach, and tests must try to get around it.
- Before asking for confirmation, the product must check the move is still
  possible (locks, roster limits, waiver type, budget, the player still
  available) and warn about side effects (a drop that leaves a starting slot
  empty, a trade that executes immediately because the league has no review
  period).
- After a move, the product must read the result back from Sleeper and tell
  the manager whether it took effect, failed, or couldn't be verified.
- **Trades** reach another manager and may be irreversible. A trade offer must
  show both sides, the value change for me (and should show it for them), and
  the league's review rules before confirmation.

Account safety (must):

- Moves use the manager's own Sleeper credentials, stored locally, never
  logged, never passed through the model, sent only to Sleeper.
- The product must only be able to call the Sleeper operations it needs.
  Betting and payment operations must be unreachable.
- Missing credentials must give a clear message naming how to add them.

Autonomy (may, later): the team may propose an opt-in mode for low-risk moves
(for example, swapping out a starter ruled Out before lock) under rules the
manager sets. That needs its own design and the manager's sign-off; v1 confirms
every move.

First build: writes went through Sleeper's undocumented GraphQL API with the
user's token (from sleeper.com local storage, valid about a year, full account
access), CLI only, behind a typed `APPROVE` prompt bound to a hash of the
exact payload, with 11 adversarial tests. Mutations used:
`update_matchup_leg` and `roster_update_starters` (lineup; both sent because
it was unknown which one scores), `roster_update_reserve` (IR, full list),
`league_create_transaction` (free agent), `submit_waiver_claim`,
`cancel_waiver_claim`, and `league_transactions_filtered` to read pending
claims. Each write was read back through a different query and reported as
VERIFIED / MISMATCH / SENT, could not verify. Trade mutations
(`propose_trade`, `accept_trade`, ...) exist in the schema but were not mapped
or tested. Browser automation was tried first and abandoned (section 7).

### 4.7 Weekly digest and email

- A weekly report (recommended lineup, waiver board, last week's result, a
  watch list of decision-relevant news) must be producible without the model,
  so it works unattended, and be deliverable by email on a schedule.
- The watch list must flag only news that suggests a decision (injury,
  practice status, role change), erring toward false positives.

First build: Gmail SMTP with an app password, validated before connecting;
scheduled by cron / Task Scheduler. Keyword matching needed stems ("practi"
for "practicing").

### 4.8 Observability

The team must be able to see, for any question, what the model saw, each tool
call with arguments and results, tokens, latency and retries. Off by default;
tests never send traces; credentials never appear in traces.

First build: LangSmith via env vars, tagged with command, model, league,
season and week; the pre-question refresh as its own span.

### 4.9 Packaging and release

- Desktop: one download per OS, per-user install without admin rights, data in
  the user's app-data folder, a log file for support.
- Docker: one image, start/stop scripts, data in a volume, port reachable only
  from the local machine (there is no login, and credentials are stored).
- Android: an installable app for current Android versions. Where the agent
  and data run is the team's decision; options include:
  - a backend the user runs elsewhere (their desktop app or Docker), which
    the phone connects to securely;
  - a small hosted backend per user;
  - everything on the phone (hard: the backend is Python, and the
    optimizer and data volume must fit a phone).
  Whatever the choice, credentials (model key, Sleeper token) must stay
  protected in transit and at rest, and a backend reachable from outside the
  user's machine must require authentication, which the local-only design
  never needed.
- A short user guide for non-technical users: how to install, how to create
  an OpenRouter account and key (free tier, and how to choose a paid model),
  first setup, and example questions.
- Releases built by CI.
- Code signing and auto-update are should-haves.

First build: PyInstaller one-folder bundle, Inno Setup on Windows, dmg on Mac
(never run by hand), GHCR images for amd64 and arm64, a `v*` tag building the
installers and release.

### 4.10 Model access and failures

- The user must be able to choose the model in Settings, with a recommended
  default and short notes on each option.
- Transient provider errors must be retried. A daily quota must fail fast with
  a clear message (wait, add credits, or choose another model), and the user's
  question must not be lost.

First build: OpenRouter free models. OpenRouter reports upstream failures as
HTTP 200 with an error body, so retries matched on the body. The free tier
allows about 50 requests a day. Windows consoles needed forced UTF-8.

---

## 5. Data sources and API facts

| Source | Use | Facts |
|---|---|---|
| Sleeper REST v1 (documented, keyless) | league, users, players, drafts, past weeks, trending | Rosters/transactions cached `s-maxage=300` + stale 300; matchups 60s |
| Sleeper `/projections`, `/stats/nfl/<season>/<week>`, `/schedule/nfl/regular/<season>` (undocumented) | projections, actual stats, game state | Stats use the projection keys. Schedule gives per-game `status` (`pre_game`, `complete`), date only, so kickoff times must come from another source (candidates: nflverse schedule, ESPN). A canceled game is listed next to its rescheduled replacement: skip it |
| Sleeper GraphQL `sleeper.com/graphql` (undocumented, mapped publicly Sept 2026) | live rosters, news, moves, pending claims, trades | Needs a User-Agent (403 without). Use variables, never string interpolation. Introspection open (~240 queries, ~350 mutations). References: `MicahSapp/sleeper-mcp`, `joscaz/sleeper-mcp` |
| nflverse release assets (read directly) | historical box scores | `nfl_data_py` is unmaintained and stops at 2024. Filter to fantasy positions (raw data includes linemen/punters) |
| ID crosswalk Sleeper <-> nflverse | anything joining the two | `gsis_id` alone matched ~15%. Fall back to name+team+position, then a name unique among active players; normalize suffixes and team codes (`LA`=`LAR`). Coverage ~98% |
| Sleeper news (GraphQL) | per-player title, description, fantasy analysis, url | Surface the prose; parsing it into flags lost the nuance |

Candidates to evaluate for the multi-source requirement (check terms and
access before relying on any): the NFL's official injury reports and game-day
inactives; nflverse's weekly rosters, depth charts, snap counts and
participation data (snap and target share); ESPN's public JSON endpoints
(rosters, depth charts, injuries; undocumented); team websites' depth charts.

Not usable without external access: X/Twitter beat writers (paid API; list in
`Bitwriters.md`; first check whether Sleeper's news already sources them),
Reddit (noisy; sentiment context at most), FantasyPros (paid key), The
Athletic (paywalled).

---

## 6. Evaluation

### Backtest (weeks 3-17, skill positions, MAE vs. Sleeper-only)

| Tier | 2024 | 2025 |
|---|---|---|
| Sleeper only (baseline) | 4.56 | 4.35 |
| + blend with nflverse | 4.40 (-3.6%) | 4.19 (-3.7%) |
| + blend + matchup | 4.58 (+0.4%) | 4.34 (-0.2%) |

N about 4,800-5,200 per tier. Blending earns its place; matchup does not (a
24-combination shrinkage/window sweep never beat plain blend). An earlier
table showed -7.5% for blending, but it was measured on the 15% crosswalk
sample: the harness caught its own bad ground truth twice.

Not backtested: the injury overlay (only current injury state was stored, so
scoring past weeks would leak) and IDP (an IDP's value depends on the opposing
offense, a separate model).

Rest-of-season projections were never evaluated. They must be: from a past
week, project the rest of that season and compare with the points actually
scored over those weeks, against a baseline (Sleeper's season projection,
prorated), in two independent seasons.

### Testing expectations

- The confirmation rule for moves must have tests that try to forge, reuse or
  sidestep a confirmation.
- The backtest must run before any projection change ships.
- Answers should be checked for claims that no supplied information supports
  (for example, every player name, NFL team and role in an answer appears in
  that turn's tool results). This may be automated with tracing data or an
  LLM judge.
- Model behavior must have a live regression set. `TEST_QUESTIONS.md` is the
  starting point; a correct answer reached without the right tool call is a
  fail (it came from memory). Moves (including trades) need entries there,
  ideally in a test league.
- Fixtures should use real text (actual headlines, real API responses); that
  is how the first build caught its keyword bug.

---

## 7. Lessons that should shape the rebuild

- **The free model is the main quality limit.** It sometimes does prose
  arithmetic, contradicts numbers it just quoted, resolves ambiguous names
  without saying so, drops per-line labels from long tool output, and gets
  lost on long multi-step tasks. What helped: summaries first in tool output,
  facts labeled directly ("MY ROSTER (bench)"), one tool per question type.
  The remaining lever is a stronger model. This matters more once the agent
  can act: a confused model proposing a wrong move is caught only by the
  confirmation step.
- **Concurrency:** LangGraph runs tools on worker threads; SQLite connections
  must allow cross-thread use.
- **Lineup diffs:** slots repeat (2 RB, 2 WR), so compare per slot type, never
  via a map keyed by slot name.
- **Locks:** check both sides of a swap (you can't bench a player who played,
  or start one who has), and check before asking for confirmation, not after.
- **Browser automation was a dead end** (bot detection, cookie overlays,
  selectors, surname matching). The GraphQL API replaced it.
- **A write can "succeed" while nothing changes** (wrong lineup mutation). Only
  a read-back proves it.
- **"Starters with news" is everyone.** Filters need a decision-relevance test.
- **Gmail account passwords no longer work for SMTP;** only app passwords.
- **PyInstaller misses modules loaded by name** (pandas loads fastparquet that
  way), and a projection source silently dropped out.
- **Scoring settings must come from the active league,** not the first one
  stored.

---

## 8. Open items and risks

| Item | Status |
|---|---|
| Which lineup mutation Sleeper scores from | Unresolved: two public reverse-engineerings disagree. Test question 23 settles it |
| Trade operations | Not mapped or tested. Needs the query for open offers and the propose/accept/reject/counter/withdraw mutations, verified in a test league |
| Sleeper terms of use for automated moves | Not reviewed. Must be read before shipping moves to other users |
| Live checks needing a token or a game in progress | Test questions 4, 7, 20-26 not yet run |
| In-game schedule `status` value | Capture during a live game |
| IR in season projections | Decided: 0 until the expected return week (from the injury source's return date), healthy projection after it; 0 for all remaining weeks when no return date exists. Not backtestable on stored data |
| Matchup adjustment, IDP matchup model | Off / not built |
| Rebuilding the nflverse source on Sleeper stats | Would remove the crosswalk; ships only if the backtest holds |
| Taxi squads | Not handled in the first build |
| Sources for depth order, practice reports, inactives and usage | Not chosen. Pick by freshness, coverage and terms of use; map their player ids to Sleeper's (the crosswalk lesson in section 5 applies) |
| Mac build, code signing, auto-update | Untested / not done |
| Undocumented API | Sleeper can change or close GraphQL without notice; moves stop working until fixed, while reads can fall back to REST |
| Evaluations from `TEST_QUESTIONS.md` in a tracing tool | Later |
| Android architecture | Not decided: where the backend runs, how the phone authenticates to it, and whether it reuses the Next.js page (for example wrapped as a native shell) or is a native app |
| Decision quality not evaluated | Deferred. Projections are backtested; lineups and waiver picks are not. A cheap start: points left on the bench across past weeks |
| Performance targets | Deferred. No target yet for first sync, app start, or the slowest acceptable answer (only "can take a minute") |
| What a good "why" looks like | Deferred. Every decision must explain itself, but there is no reference example to test against |
| Updates for desktop users | Deferred. Without auto-update, users need a way to learn a new version exists and how to install it |

---

## 9. Fixed technical constraints

From `AGENT.md` (these are not open to change without the owner):

- Next.js frontend; Python FastAPI backend serving the static site at `/`.
- Packaged in Docker, using uv for Python; start/stop scripts in `scripts/` as
  docker compose wrappers (`.sh` and `.bat`).
- OpenRouter for model calls, with a free model that reliably calls tools,
  pinned by id. The originally specified `openai/gpt-oss-120b:free` went
  paid-only mid-project; the first build defaulted to
  `nvidia/nemotron-3-super-120b-a12b:free` and later recommended
  `nvidia/nemotron-3-ultra-550b-a55b:free`. Re-check before choosing.
- SQLite, created if missing.
- Colors: accent yellow `#ecad0a`, blue primary `#209dd7`, purple secondary
  `#753991` (submit and important actions, such as confirming a move), dark
  navy `#032147` (headings), gray `#888888` (supporting text).
- Coding standards in `AGENT.md`: simple, no over-engineering, root cause
  before fixes, no emojis.

Everything else (agent framework, optimizer, desktop and Android packaging,
tracing tool, email provider) is the team's choice. For Android, reusing the
Next.js frontend is preferred but not required. The first build used LangGraph, pulp,
pywebview + PyInstaller, LangSmith and Gmail SMTP; any of these may be kept or
replaced.
