# Data contract

Shared contract for storage, sources, projections, decisions and agent tools.
Terminology (`AGENT.md`): **fantasy team** is a manager's roster in a Sleeper
league; **NFL team** is a real franchise. Never a bare "team".

Edit only your own sections, with Edit (several people edit this file).

| Part | Sections | Owner |
|---|---|---|
| A | 1 SQLite schema and migrations, 2 NFL fact sources, 3 Crosswalk, 4 NFL fact shape, 5 Freshness | backend-developer |
| B | 6 Projection sources, 7 Projection artifact, 8 League format, 9 Decision results, 10 Backtest outputs | data-scientist |
| C | 11 Agent tools, 12 Per-turn context, 13 Framework decision | ai-engineer |

---

## Part A: storage and facts (backend-developer)

### 1. SQLite schema and migration rule

**File and connection.** One file, `fantasy.db`, in the app-data folder
(desktop) or the Docker volume (`FANTASY_DATA_DIR`, default per OS). Created
if missing. `check_same_thread=False`, `journal_mode=WAL`, `foreign_keys=ON`;
each refresh writes in one short transaction.

**Migration rule (in place, upgrades keep data).**

- Migrations are numbered files `src/storage/migrations/NNNN_name.sql`.
  `PRAGMA user_version` holds the last applied number. On start, each pending
  file runs in its own transaction, in order; on failure that file rolls back
  and the app refuses to start, naming the file.
- Never delete or recreate the database. Never drop a table or column that
  holds user data. To change a column: add the new one, copy, stop reading the
  old one. Tables marked cache (`player`, `player_id_map`, `nfl_*`, `news`,
  `player_week_stat`, league state, projection tables) may be emptied by a
  migration and refilled by the next refresh; **user data** tables may not.
- Stage 2.1 test: build a database at migration N-1, insert real fixture rows
  into every user data table, apply N, assert the rows are unchanged.
- Parts B and C change the schema only by asking backend for a migration file,
  never by `CREATE TABLE` in code.

**User data** (kept across upgrades):

```sql
settings(key text primary key, value text not null)
  -- sleeper_username, sleeper_user_id, current_league_id, model,
  -- projection_weights (json), openrouter_base_url (optional; default
  -- https://openrouter.ai/api/v1, env OPENROUTER_BASE_URL when unset; tests and
  -- proxies point it elsewhere), openrouter_api_key (plain text in the local
  -- file; never logged, never returned by the API, never in traces or prompts)
chat_message(id integer primary key, league_id text not null,
  role text not null,                 -- user | assistant | tool
  content text not null, created_at text not null,
  meta text)                          -- json, keys below
roster_diff(id integer primary key, league_id text not null, roster_id integer not null,
  detected_at text not null, added text not null, dropped text not null)  -- json arrays of player ids
```

Start fresh deletes that league's `chat_message` rows only. `roster_diff` feeds
RECENT CHANGES in the context block (section 12).

**`chat_message.meta`** (json, written by `src/agent` through `src/storage`; it
must never hold the OpenRouter key or any credential). One question is a run
of rows in id order: the `user` row, then for each model turn that calls tools
the `assistant` row followed by its `tool` rows, then the final `assistant` row.

| Row | `content` | `meta` keys |
|---|---|---|
| `user` | the question text | `status`: `ok` \| `failed`; on failure `error`: the API error object (code, message, extras such as `remaining`, `limit`) |
| `assistant` | the model's text (empty when it only called tools) | `question_id` (id of its `user` row); `model`; `turn` (1-based); `context` (the full per-turn context block as sent on this turn, section 12); `tool_calls`: `[{"id": "call_..", "name": "get_player", "arguments": {...}, "result_id": <id of the tool row>}]`; on the final row also `sources`: `[{"label","as_of"}]`, `refresh` (the Refresh object), `model_turns`, `usage` (tokens, if the provider gave them) |
| `tool` | the rendered result text exactly as the model saw it (SUMMARY, AS OF, labeled facts, WARNINGS) | `question_id`, `tool_call_id`, `name`, `arguments`, `ok` (bool) |

`context` is stored on every `assistant` row because it changes between
turns (a refresh or `switch_league` mid-question). The grounding check and
the live-check runner read these rows through `GET /api/chat/history?detail=trace`
(`docs/api-contract.md`); the normal history exposes only user and final
assistant rows with `sources` and `refresh`. Reasoning text from the model
(`reasoning`, `reasoning_details`) is not stored here.

**League state (cache; keys are Sleeper's):**

```sql
league(league_id text primary key, season integer not null, name text not null,
  status text not null,               -- pre_draft | drafting | in_season | complete
  settings text not null, scoring_settings text not null, roster_positions text not null,  -- raw json as Sleeper sent them
  format text not null,               -- json LeagueFormat (section 8), recomputed on every league refresh
  fetched_at text not null)
fantasy_team(league_id text, roster_id integer, owner_id text, name text not null,  -- metadata.team_name, else display_name
  wins integer, losses integer, ties integer, points_for real, waiver_budget_used integer,
  fetched_at text not null, primary key (league_id, roster_id))
roster_player(league_id text, roster_id integer, player_id text,
  role text not null,                 -- starter | bench | reserve | taxi
  slot_index integer,                 -- index in Sleeper's starters list; the slot label is roster_positions[slot_index]
  primary key (league_id, roster_id, player_id))
matchup(league_id text, week integer, roster_id integer, matchup_id integer, points real,
  fetched_at text not null, primary key (league_id, week, roster_id))
matchup_player(league_id text, week integer, roster_id integer, player_id text,
  points real, is_starter integer not null, slot_index integer,
  primary key (league_id, week, roster_id, player_id))
transaction_log(league_id text, transaction_id text, week integer, type text, status text,
  created_ms integer, adds text, drops text, roster_ids text, waiver_budget text, draft_picks text,  -- json
  settings text,                      -- json, Sleeper's transaction settings (migration 0002). Rolling leagues only show {"seq": n};
                                      -- a FAAB bid would be here but is unconfirmed: no FAAB league seen (2026-11-17), so bids stay a stated gap
  primary key (league_id, transaction_id))
```

**Players, games, NFL facts, actuals (cache).** Fact tables carry `source`,
`as_of`, `fetched_at` (section 4):

```sql
player(player_id text primary key,    -- Sleeper id; team DEF rows use the NFL team abbreviation ("DEN")
  full_name text, position text, fantasy_positions text, nfl_team text, status text, active integer,
  injury_status text, injury_body_part text, practice_participation text,
  depth_chart_position text, depth_chart_order integer, search_rank integer,
  gsis_id text, espn_id text, fetched_at text not null)
player_id_map(player_id text not null, source text not null,   -- nflverse | espn | nfl | pfr
  source_id text not null, method text not null,                -- method: section 3
  primary key (source, source_id))
nfl_team(abbr text primary key, name text, espn_id text, espn_abbr text, nflverse_code text, bye_week integer)
nfl_game(game_id text primary key,    -- nflverse id "2026_08_CHI_SEA"
  season integer, week integer, home text, away text, kickoff_utc text,
  status text not null,               -- scheduled | in_progress | final
  home_score integer, away_score integer, sleeper_game_id text, espn_event_id text,
  source text, as_of text, fetched_at text not null)
nfl_roster(player_id text, source text, nfl_team text, roster_status text, as_of text, fetched_at text,
  primary key (player_id, source))
nfl_depth(source text, nfl_team text, unit text, slot_label text,  -- "WR2"
  rank integer, player_id text, as_of text, fetched_at text,
  primary key (source, nfl_team, slot_label, player_id))
nfl_injury(player_id text, season integer, week integer, source text,
  designation text, practice_status text, body_part text, detail text,
  return_date text,                   -- expected first game date (YYYY-MM-DD), only ESPN fills it (details.returnDate); null elsewhere. Its date is this row's as_of
  raw text, as_of text, fetched_at text, primary key (player_id, season, week, source))
nfl_usage(player_id text, season integer, week integer, source text,
  offense_pct real, defense_pct real, st_pct real, targets integer, carries integer, target_share real,
  as_of text, fetched_at text, primary key (player_id, season, week, source))
nfl_inactive(game_id text, player_id text, source text, as_of text, fetched_at text,
  primary key (game_id, player_id, source))
news(source text, source_key text, player_id text, published_at text,
  title text, description text, analysis text, url text, fetched_at text,
  primary key (source, source_key))
player_week_stat(season integer, week integer, player_id text, stats text not null,  -- json, Sleeper /stats keys
  fetched_at text not null, primary key (season, week, player_id))
refresh_log(source text, scope text, last_attempt_at text, last_ok_at text, last_error text,
  coverage text, primary key (source, scope))   -- coverage: json {"mapped":n,"total":n} for sources that need the crosswalk
```

**Projection tables (cache; columns fixed by section 7):**

```sql
projection(league_id text, season integer, player_id text, horizon text, week integer,
  mean real not null, variance real not null, n_games integer not null, zero_reason text,
  mean_if_healthy real, return_week integer, return_date text, return_date_source text, return_date_as_of text, sources text not null, computed_at text not null,
  primary key (league_id, season, player_id, horizon, week))
projection_meta(league_id text primary key, scoring_hash text not null,
  unprojected_scoring_items text not null, weights text not null, computed_at text not null)
source_projection(source text, season integer, week integer, player_id text,
  stats text not null, points_variance real, fetched_at text not null,
  primary key (source, season, week, player_id))   -- raw weekly source rows (5.7 MB per week from Sleeper), cached so a rebuild does not refetch
```

```sql
projection_snapshot(season integer, week integer, player_id text, pulled_at text not null,
  stats text not null, source_modified text,
  primary key (season, week, player_id, pulled_at))   -- migration 0003
```

Every Sleeper projection pull is kept as it happens (week 0 = season total): only
players with a non-empty projection, and a pull equal to the player's latest stored
one is skipped. Written by `ingestion/projection_pulls.py` from the NFL refresh:
this week every 6 h (every 30 min in the game window, so a pull exists shortly before each kickoff), later weeks and the season total daily. A backtest uses, per player-week, the latest snapshot with `pulled_at` before that game's kickoff (`nfl_game.kickoff_utc`). These are the
pre-game rows for later rest-of-season backtests; the projections Sleeper still
serves for past weeks are stamped after the games. `projection` also has
`return_week`, `return_date`, `return_date_source`, `return_date_as_of` (migration 0003).

Indexes: `roster_player(league_id, player_id)`, `nfl_injury(player_id, season, week)`,
`news(player_id, published_at desc)`, `nfl_game(season, week)`,
`chat_message(league_id, id)`.

Writers: `projection`, `projection_meta`, `source_projection` only from
`src/projections`; `chat_message` only from `src/agent` through `src/storage`;
everything else only from `src/ingestion` and `src/web`.

### 2. NFL fact source interface

Verdicts and field details: `docs/spikes.md`. v1 sources: `sleeper` (catalog,
GraphQL news), `nflverse`, `espn`. Each is one module under
`src/ingestion/nfl/sources/` (undocumented APIs: one module each, AGENT.md).
Adding a source = one class registered in `sources/__init__.py`; the service
and the tools do not change.

```python
class NflFactSource(Protocol):
    name: str                                 # "nflverse", "espn", "sleeper"
    kinds: frozenset[str]                     # subset of roster, depth, injury, usage, inactive, schedule, news
    def fetch(self, kind: str, season: int, week: int) -> FetchResult: ...

class FetchResult:
    facts: list[NflFact]                      # player_id already a Sleeper id (the source calls Crosswalk.resolve, section 3)
    unmapped: int                             # rows the crosswalk could not resolve (dropped, counted in refresh_log.coverage)
    gaps: list[str]                           # e.g. "espn: didNotPlay not populated for 401873040"
```

Rules:

- A source returns only what it has; a kind it does not provide is simply not
  in `kinds`. A failing source raises; the service records the error in
  `refresh_log`, keeps the stored rows, and reports it (section 5).
- Rows from different sources are stored side by side (the source is part of
  every primary key). Nothing is merged on write.
- **Choosing between sources happens on read, in `src/ingestion/nfl/service.py`**, with
  these fixed precedences (first source that has the player wins; every
  source that disagrees is returned in `also`, so the agent can state the
  disagreement):

| Kind | Precedence | Why |
|---|---|---|
| injury designation, practice status | the official report for the week being played (`nflverse`) > `espn` > `sleeper` | PRD 4.2: the official report is authoritative. Before the week's report exists (it starts Wednesday), `espn`, then `sleeper`; `as_of` says which |
| roster (NFL team) | `espn` > `nflverse` > `sleeper` | the freshest listing; Sleeper's catalog is the fallback |
| depth order | `nflverse` > `espn` > `sleeper` (`depth_chart_order`) | Sleeper's lags real roles; usage is shown next to it and the answer says which basis it used |
| usage (snap share) | `nflverse` | only source in v1; targets and carries come from Sleeper `/stats` already in `player_week_stat` |
| inactive | `espn` | only source. ESPN marks `didNotPlay` for the real inactives after a game (8 per NFL team on a final game, 2026-11-17 capture); before the announcement nothing is marked, so "none marked" is a gap ("no inactives listed yet"), never "nobody is out". Not verified before a kickoff yet (spikes) |
| game state, kickoff | `espn` scoreboard for state; `nflverse` schedule for kickoff when ESPN lacks the game | |
| news | `sleeper` (GraphQL, RotoWire prose) | prose is surfaced as is, never parsed into flags |

- Precedence and the pull intervals (section 5) are tables at the top of
  `src/ingestion/nfl/service.py`, not code in the tools.
- Sources that do not need the crosswalk (`espn` scoreboard) still stamp
  `as_of` and `fetched_at`.

**As built (stage 2.2).** `src/ingestion/nfl/nflverse.py` and `espn.py` are the
two source classes (`fetch(kind, season, week, **kw)`; `kw` carries `team` for an
ESPN roster and `games` for inactives). Sleeper's kinds are plain functions in
`sleeper_facts.py` (news, and targets and carries from `/stats`), because they
call `ingestion/sleeper.py` and need no crosswalk. nflverse assets are fetched
only when their ETag changed (a HEAD request first); an unchanged asset counts
as a successful pull. ESPN's depth chart is not used (nflverse's is the
precedence winner and Sleeper's depth order the fallback); ESPN's roster is pulled
per NFL team when that roster is asked for (30 min cache), not for all 32.
A source row whose player the crosswalk cannot resolve is dropped and counted.
A player listed twice in a depth chart (`WR2` and `KR1`) shows the row of his
position, special teams last.

### 3. Player id crosswalk

The key is the **Sleeper player id**; team DEF "players" use the NFL team
abbreviation. One function, `Crosswalk.resolve(source, source_id=None, name=None,
nfl_team=None, position=None) -> player_id | None`, used by every source
(NFL facts, projections). Results are stored in `player_id_map` with the
`method` that produced them, rebuilt after each Sleeper catalog or nflverse
roster refresh.

Rules, in order; the first that matches wins:

1. **`sleeper_id` column** of the nflverse weekly rosters (`method=sleeper_id`).
   Use each player's latest row across all weeks, not only the latest week: NFL
   teams on bye have no row that week (spikes). This also gives that player's
   `gsis_id` (the NFL's own id, stored as source `nfl`), `espn_id` (source
   `espn`) and `pfr_id` (source `pfr`).
2. **Source id already known**: an `espn_id`, `gsis_id` or `pfr_id` found in
   `player_id_map` (so ESPN rosters, nflverse depth charts and snap counts
   resolve through step 1's rows). Sleeper's own `gsis_id`/`espn_id` is only used
   to confirm a step-1 mapping, never alone (17% and 23% of active players
   carry them; the first build's `gsis_id`-alone result was about 15%). On a
   conflict, step 1 wins and the conflict is logged.
3. **Name + NFL team + position**: normalized name (lowercase, punctuation and
   suffixes `jr sr ii iii iv v` removed), NFL team codes normalized (`LA` =
   `LAR`, `WSH` = `WAS`, `JAC` = `JAX`, through the `nfl_team` table), position
   compared as a group (RB/FB; DL/DE/DT; DB/CB/S; LB). `method=name_team_pos`.
4. **Name unique among active players** on both sides (`method=name_unique`).
5. Otherwise `None`: the row is dropped and counted as unmapped.

Team DEF: Sleeper id = NFL team abbreviation; nflverse and ESPN rows map by the
`nfl_team` table.

**Coverage** is computed on every rebuild and stored per source in
`refresh_log.coverage` and shown by the setup/status response: mapped / total
over active Sleeper players at fantasy and IDP positions on an NFL team,
and the same over *relevant* players (rostered in the current league or
`search_rank` < 600). Spike probe, 2026-11-03: 81% by step 1, 93% with step 3;
the 7% left was mostly stale practice squad entries (19 players with
`search_rank` < 600). Target (stage 2.2): at least 98% of relevant players.

**As built, measured 2026-11-17 against the live sources** (full Sleeper catalog,
league "Or Test League"). Rows each source returned at fantasy and IDP
positions that resolved to a Sleeper id (`refresh_log.coverage`, per kind):
nflverse roster 99.3%, injuries 99.8%, depth chart 99.7%, snap counts 99.9%,
ESPN injuries 100%, ESPN roster of SEA 100%. Sleeper players that have each
id source (`nflverse:crosswalk`): `nfl` (gsis) 93.1% of all, 98.1% of relevant
(777/792); `espn` 86.7% and 94.6% (749/792); `pfr` 86.5% and 94.4% (748/792).
`espn` and `pfr` ids come only from the nflverse roster file (75% and about 70% of
its rows carry them), so they are lower than `nfl`; rows that arrive with only
those ids still resolve by name (the per-kind figures above). Source name
`nflverse` in `player_id_map` is unused: nflverse rows resolve through `nfl`.

**Name resolution (shared by the agent tools and the API).**
`resolve_player(text, league_id)` returns one of
`{"status": "ok", "player_id": ...}`,
`{"status": "ambiguous", "candidates": [{"player_id","name","position","nfl_team","fantasy_status"}]}`
or `{"status": "not_found"}`. Ranking: exact full name; then rostered in this
league; then on an NFL team; two or more equal-rank matches (two Williams)
return `ambiguous`, never a guess. NFL team names (`SEA`, "Seattle",
"Seahawks") resolve through the `nfl_team` table; fantasy team and league
names match exactly (case-insensitive), then by unique prefix. `not_found`
for a league lists the real league names.

### 4. NFL fact shape (source and freshness)

Every NFL fact, whatever the kind, has these fields; kind-specific fields
follow. `src/ingestion/nfl/service.py` returns them with the age rendered by code, so no
tool or model computes an age.

```python
class NflFact:
    kind: str                  # roster | depth | injury | usage | inactive | news
    player_id: str             # Sleeper id
    nfl_team: str | None       # Sleeper abbreviation (LAR, not LA)
    season: int
    week: int | None           # the NFL week the fact is about (injury, usage, inactive); None for roster, depth, news
    source: str                # "nflverse" | "espn" | "sleeper"
    source_label: str          # words for the answer: "official NFL injury report via nflverse", "ESPN", "Sleeper catalog"
    as_of: str                 # ISO UTC: when the source says the fact is from (report date, file timestamp, article time)
    fetched_at: str            # ISO UTC: when we got it
    age_minutes: int           # now - as_of, computed by code at read time
    age_text: str              # "3 h ago", "yesterday 18:57"
    stale: bool                # older than the kind's limit in section 5
    also: list[dict]           # other sources that disagree: [{"source","value","as_of"}]
    value: dict                # by kind, below

# value by kind
injury   = {"designation": "Out|Doubtful|Questionable|IR|PUP|Sus|NA|DNR|None",   # Sleeper's vocabulary
            "practice_status": str | None,    # latest listed in the report, e.g. "Full Participation in Practice" (the report is weekly; no per-day history in v1)
            "body_part": str | None, "detail": str | None,
            "return_date": str | None,        # "2026-12-06": expected return, YYYY-MM-DD
            "return_date_source": str | None, # "espn" in v1 (ESPN injuries details.returnDate); the other sources have none
            "return_date_as_of": str | None}  # ISO UTC: that ESPN injury item's own `date` (Love: 2026-11-03T18:57Z)
roster   = {"roster_status": str}              # ACT, RES, DEV ...
depth    = {"unit": "offense|defense|special", "slot_label": "WR2", "rank": 2, "basis": "depth_chart"}
usage    = {"offense_pct": float | None, "defense_pct": float | None, "st_pct": float | None,
            "targets": int | None, "carries": int | None, "target_share": float | None}
inactive = {"game_id": str, "inactive": bool}
news     = {"title": str, "description": str, "analysis": str | None, "url": str | None, "published_at": str}
```

**Return date rule.** The designation and the return date are chosen
separately: the designation follows the section 2 precedence, while
`return_date` is taken from the newest row of any source that has one (ESPN
only in v1), with its own `return_date_source` and `return_date_as_of`, so an
official-report designation can sit next to an ESPN return date. No row with a
return date means `return_date` is null and the projection side must treat it as
unknown, not as a guess. ESPN's date is a news estimate (RotoWire), not an
official report; the source label says so. Spike sample: Jeremiyah Love, status
Out, `returnDate` 2026-12-06, item date 2026-11-03T18:57Z
(`src/ingestion/tests/fixtures/espn/injuries_sample.json`).

`source_label` is what the agent says ("per the official injury report,
Thursday"). A kind with no usable source for a player comes back as a gap,
not an empty list:

```python
class NflFactSet:
    facts: list[NflFact]
    sources_used: list[str]
    oldest_as_of: str | None
    gaps: list[str]            # "no source has week 9 injuries for SEA yet (report starts Wednesday)",
                               # "inactives: not available (no usable source before kickoff)"
    refresh_failed: list[dict] # [{"source","scope","since","error"}] from refresh_log
```

Games are not facts of a player: `nfl_game` rows give `kickoff_utc`, `status`
and score; ds's `GameRef` (section 9) is built from them plus `as_of.games`.
`nfl_game.status` `scheduled | in_progress | final` is rendered as
`GameRef.status` `not_started | in_progress | complete`; `bye` comes from
`nfl_team.bye_week`.

### 5. Freshness rules

From PRD 4.2 and PLAN section 2: data at most 2 hours old at question time; at
most 2 minutes on a game day while the app is open, within each source's
limits. A **game day** is any date (in US Eastern) with a row in `nfl_game`;
the **game window** is 3 hours before the first kickoff of the day until the
last game is final.

| What | Source | Normal | Game window | Stated stale after |
|---|---|---|---|---|
| My roster, lineup, current-week matchup | Sleeper GraphQL `league_rosters` + REST matchups | before every question, and on a panel refresh | same, plus every 2 min while the app is open | 2 min on a game day, else 2 h |
| Other fantasy teams' rosters, transactions | Sleeper GraphQL / REST | before every question (3 requests, PRD first build) | same | 2 h |
| League settings and scoring | Sleeper REST | on league selection, then every 2 h | same | 24 h |
| Players catalog | Sleeper REST (14.7 MB) | once at start and then at most every 20 h (Sleeper asks about once a day) | not re-fetched | 36 h |
| Player news | Sleeper GraphQL | on a question for the players asked about and for my roster; 30 min cache | 5 min | 2 h |
| Scores, game state, kickoff changes | ESPN scoreboard (240 KB) | 30 min | 2 min | 2 min on a game day, else 2 h |
| Injuries (ESPN) | ESPN injuries (0.35 MB gzipped) | 30 min | 2 min | as above |
| Inactives | ESPN event roster `didNotPlay` | not fetched | 2 min, only for games within 3 hours of kickoff | 2 min |
| Official injury report | nflverse `injuries` (asset updates about hourly) | 30 min conditional check (`If-None-Match`, download only when the asset changed) | 10 min | 2 h |
| Depth charts | nflverse `depth_charts` (14 MB gz) | 2 h conditional check | 30 min | 4 h |
| Weekly rosters, snap counts | nflverse | 6 h conditional check | 6 h | 36 h |
| Schedule and kickoff times | nflverse `games.csv` | 2 h; ESPN scoreboard overrides kickoff and state | same | 24 h |
| Projections | rebuilt per section 7 | when sources, scoring or the injury state change, and at least every 2 h | same | 2 h |

Rules:

- **Polling.** The web app runs one background loop (30 s tick, `web/main.py`)
  that calls `ingestion.nfl.service.refresh`; each step runs only when its
  interval is due (the table above, `STEPS` in `service.py`). The manual refresh
  endpoint forces every step. The game window is computed from `nfl_game`: from 3
  hours before a kickoff until that game is final.
- **ESPN backoff.** An HTTP 403 or 429 from ESPN sets `espn_backoff_until`
  (now + 10 minutes) in settings; every ESPN step is skipped until then.
- **Before each question** the API layer refreshes the first two rows
  (everything the answer must not get wrong), plus any row past its normal
  interval, in parallel with a 10 s total budget. There is no sync tool.
- A source that cannot meet its target (nflverse on a game day: about
  hourly) is shown with its real `as_of`; the answer states the age. Where
  two sources cover a kind, the faster one carries the 2-minute target and
  the other is used for the official value (section 2 precedence).
- **Failures:** stored rows are kept; `refresh_log.last_error` is set; the
  failure is returned in the context block (`REFRESH FAILED`), in
  `NflFactSet.refresh_failed` and in the API (`refresh.failed` in
  `docs/api-contract.md`). The answer proceeds on stored data. A failed source
  is retried on its next interval, not in a loop.
- **ESPN throttling:** on HTTP 429 or 403 the source backs off to 10 min and
  reports the failure; the app does not hammer it. Undocumented source: if it
  breaks, it is dropped and the gap is stated.
- **"This week"** = the lowest week that still has a non-final `nfl_game`;
  if every game of week N is final, it is N+1. Checked against Sleeper
  `state.week` (9 on 2026-11-03 while `display_week` is 8); a mismatch is
  logged and the `nfl_game` rule wins. Rollover is recomputed on every
  request, so a running session picks it up.
- **Roster diffs:** every Sleeper roster pull is compared with the stored
  `roster_player` rows per fantasy team; a difference writes a `roster_diff`
  row (my fantasy team's rows feed RECENT CHANGES) before the stored rows are
  replaced.
- **Backfill:** on first sync of a league, completed weeks' matchups and
  `player_week_stat` are fetched from Sleeper (mid-season start).
- **Clock:** all stored times are ISO UTC; kickoff times from nflverse are US
  Eastern and converted on read into UTC.

---

## Part B: projections and decisions (data-scientist)

### 6. Projection source interface

Conventions used in Parts B and C: `player_id` is the Sleeper player id (string);
`fantasy_team_id` is the Sleeper `roster_id` (int) within a league; `week` is
an NFL week 1-18; all points are floats in the league's own scoring; times are
ISO 8601 UTC. A source never sees league scoring: it returns stats, and the
blend scores them once, so every source is scored identically.

```python
class ProjectionSource(Protocol):
    name: str                                   # "sleeper", "nflverse"
    stat_keys: set[str]                         # stat keys the source projects (feeds unprojected_scoring_items)
    def weekly(self, season: int, weeks: list[int]) -> list[SourceRow]: ...
        # weekly only; rest of season is the sum of the weekly rows (section 7)

class SourceRow:
    player_id: str                  # already mapped to Sleeper id (crosswalk is the source's job)
    week: int
    stats: dict[str, float]         # Sleeper stat keys (pass_yd, rush_yd, rec, idp_tkl_solo ...), expected values; a key absent = 0
    points_variance: float | None   # in reference PPR points (REFERENCE_PPR, section 10) if the source has its own, else None
```

Rules:

- Weights live in settings: `projection_weights`, default `{"sleeper": 0.9,
  "nflverse": 0.1}`, chosen on the 2024 backtest only (section 10). A source
  with weight 0, missing, failing or without a row for a player-week is dropped
  for that row and the remaining weights are renormalised. Any subset works. A
  source row with no stats is a projection of 0, not silence: Sleeper lists
  players it projects at 0 (about 8,400 of 9,400 rows a week).
- Registering a source = adding one class to the registry. Blend, artifact and
  decisions do not change.
- Variance of a blended row, in league points: weighted mean of each source's
  variance plus the weighted between-source variance of the means. A source's own
  `points_variance` (reference PPR points) is rescaled by
  `(league points / reference points)^2` of the same stat line. A source without
  one gets the position floor `(cv[position] * its mean)^2`; `cv` is the RMS
  forecast error of the Sleeper-only forecast as a share of its mean, fitted on
  2024 (`--fit-cv`) and kept as `DEFAULT_CV` in `src/projections/blend.py`
  (QB 0.44, RB 0.60, WR 0.69, TE 0.75, other 0.61; 0.61 also covers IDP, K and
  DEF, which are not backtested). Rest-of-season variance is the sum of the
  weekly variances (weeks treated as independent; stated assumption).
- First v1 sources: `sleeper` (`/projections` weekly rows, which exist for every future
  week; `weekly()` only) and `nflverse` (mean of each stat over the player's
  last 4 games before the target week, across seasons, last season's games weighted
  0.5; only for weeks his NFL team plays, by the weekly roster file's team (else his
  latest game's); supplies the sample variance of those games as `points_variance`, needs 3
  games). nflverse ids reach Sleeper ids through the weekly roster file's
  `sleeper_id` column (the 2.3 sources use that bridge until the stage 2.2
  crosswalk is wired in). Settled in `docs/spikes.md` (open item O1).
- Own ML model (v2) = a third class; it ships only after the backtest gate (section 10).

### 7. Projection artifact

One SQLite table, `projection`, rebuilt for the active league when sources,
league scoring or the injury state change and at most every 2 hours (backend
adds it to the schema in section 1; this section owns its columns). Every
decision reads it; none computes its own projection.

| Column | Type | Meaning |
|---|---|---|
| `league_id` | text | scoring and format are per league |
| `season` | int | |
| `player_id` | text | Sleeper id; every fantasy-position and IDP player in the catalog |
| `horizon` | text | `week` or `ros` |
| `week` | int | for `week`: that week. For `ros`: the first week counted (this week), through 18 |
| `mean` | real | expected points in league scoring; 0 if zeroed |
| `variance` | real | 0 if zeroed |
| `n_games` | int | games scheduled (`week`: 1, or 0 on a bye; `ros`: weeks left minus byes), zeroed games included |
| `zero_reason` | text null | `ir`, `out`, `pup`, `suspended`, `bye`, or null |
| `mean_if_healthy` | real null | set for every row of a player on IR or ruled out, zeroed or not: the mean without the injury (stash needs it) |
| `return_week` | int null | IR players only: the expected return week, derived from `return_date` (below); null when none |
| `return_date` | text null | the injury fact's `return_date` (ISO YYYY-MM-DD) behind `return_week`; null = unknown, never guessed |
| `return_date_source` | text null | the injury fact's `return_date_source` (ESPN `details.returnDate`, a news estimate, not an official report); set when `return_date` is |
| `return_date_as_of` | text null | the injury fact's `return_date_as_of` |
| `sources` | json | `[{"source":"sleeper","weight":0.5,"mean":11.2,"variance":30.1}, ...]` after renormalisation; empty list = no source projected him |
| `computed_at` | text | |

Primary key: (`league_id`, `season`, `player_id`, `horizon`, `week`).
`week` rows exist for this week through week 18; `ros` has one row per player
(from this week). Full season is `ros` with `from_week=1`, computed on demand,
not stored.

Injury overlay (the only one; no other injury adjustment, backups not bumped):

- Out, PUP and suspended: zero for that game only (`week` row of the next
  game); later weeks keep their projection. Questionable and Doubtful keep
  their projection.
- IR (owner decision, O3): projected from an expected return week.
  `return_date` is the `injury` fact's `return_date` (section 4, an ISO date;
  ESPN carries it). `return_week` = the first week, from this week through 18,
  in which the player's NFL team has a game with kickoff on or after
  `return_date` (compared by kickoff date, so a kickoff on the return date counts). Weeks before `return_week`: mean 0, `zero_reason` `ir`. Weeks
  from `return_week` on: the normal blended mean, `zero_reason` null, and
  `return_week`, `return_date`, `return_date_source` and `return_date_as_of` set
  on all of the player's rows. A `return_date`
  after week 18 gives all weeks 0.
- IR with no `return_date` from any source (owner decision): Sleeper's own weekly
  rows as they are, not the blend and not 0. Weeks Sleeper has no row for get no
  row. `zero_reason` is null, `return_week` null, `mean_if_healthy` set. Decisions
  add a warning that no return date is known.
- The `ros` row is the sum of its weekly rows, so it follows these rules. The
  return date is a source's estimate, not a certainty; decisions that depend
  on it quote `return_week`, `return_date` and `return_date_source` in a
  `Reason` (`injury`), saying it is a news estimate.
- Coverage check (`projections.artifact.ir_return_date_coverage`): counts players
  on IR with and without a `return_date`, and lists those without. Measured on
  2026-11-17 against the live Sleeper catalog and ESPN injuries (names matched):
  168 active IR players, 22 listed by ESPN, all 22 with a `returnDate`; for
  QB/RB/WR/TE 16 of 95. So most IR players have no return date and take Sleeper's own rows.
- Bye week: `week` row mean 0, `zero_reason` `bye`; `ros` skips it (`n_games`).
- A player no source projected has no row at all (mean is never null);
  decisions skip him and add a `no_projection` warning when he matters (on my
  roster or asked about).

League-level companion table `projection_meta` (one row per `league_id`):

| Column | Meaning |
|---|---|
| `scoring_hash` | hash of `scoring_settings`; a change triggers a rebuild |
| `unprojected_scoring_items` | json `[{"key":"bonus_rec_yd_100","value":2.0}]`: league scoring keys with non-zero value that no active source projects. Every decision result copies this list |
| `weights` | json of the weights used |
| `computed_at` | |

Stat-to-points: `points = sum(stat[k] * league_scoring[k])` over keys the sources
projected; unprojected keys contribute 0 and are listed above.

### 8. League format and scoring

`LeagueFormat` is computed from Sleeper league settings at league selection and
on each league refresh; never assumed, never stored as a user choice. Exposed
to every tool and the per-turn context.

```python
class LeagueFormat:
    league_id: str
    season: int
    supported: bool
    unsupported_reason: str | None      # "dynasty" | "keeper" | "best_ball" | "guillotine"
    scoring: dict[str, float]           # Sleeper scoring_settings, as is
    reception_points: float             # scoring["rec"]: 1.0 PPR, 0.5 half, 0.0 standard
    te_premium: float                   # scoring.get("bonus_rec_te", 0.0)
    defense: str                        # "idp" if any IDP slot exists, else "team_def" if a DEF slot, else "none"
    superflex: bool                     # a SUPER_FLEX slot exists
    kicker: bool                        # a K slot exists
    slots: list[str]                    # starting slots in Sleeper order, repeats kept: ["QB","RB","RB","WR","WR","TE","FLEX","SUPER_FLEX","K","DEF"]
    bench_size: int
    ir_slots: int
    ir_allowed_designations: list[str]  # designations that may sit in an IR slot, Sleeper vocabulary; "IR" always, plus one per reserve_allow_* key set to 1: na->"NA", doubtful->"Doubtful", dnr->"DNR", out->"Out", sus->"Sus", cov->"COV". [] when ir_slots is 0
    taxi_slots: int
    num_fantasy_teams: int
    waiver_type: str                    # "faab" | "rolling" | "reverse_standings"
    faab_budget: int | None             # None unless faab
    faab_remaining: dict[int, int]      # fantasy_team_id -> dollars, faab only
    trade_deadline_week: int | None
    playoff_start_week: int
    roster_size: int                    # starters + bench + ir (+ taxi), used by the roster-spot rule
```

Rules:

- Unsupported leagues are reported at setup by name (`unsupported_reason`)
  and no decision tool runs for them.
- `defense` is `idp` only when IDP slots exist. A league with `DEF` slot and
  no IDP slots is `team_def`; IDP stats are then ignored in scoring.
- Slot eligibility (what fits FLEX, SUPER_FLEX, IDP_FLEX, DB, DL, LB) is read
  from Sleeper's slot definitions, not reimplemented; granular IDP positions
  map as in the first build (`DB` accepts CB/S/FS/SS; DE/DT/NT pool into `DL`).
- IR eligibility (lineup `ir_flags`, PRD 4.4): a player in an IR slot is
  flagged when his `injury_status` is not in `ir_allowed_designations`. Seen
  in fixtures: FantaSteelers (`reserve_slots` 2) has every `reserve_allow_*` = 1;
  Or Test League (`reserve_slots` 0) has all 0 and no IR slots. Assumptions to
  confirm on a league with some keys 0: "IR" is always allowed, and "PUP" (no
  `reserve_allow_*` key exists for it) is treated like "IR".
- Raw Sleeper setting keys mapped here (`type`, `best_ball`, `waiver_type`,
  `waiver_budget`, `trade_deadline`, `playoff_week_start`, `reserve_slots`,
  `taxi_slots`) are confirmed against real fixtures in stage 2.1 (open item O2).

### 9. Decision result shapes

Each decision is a pure function over the projection artifact, the stored
league state and NFL facts, returning JSON. Tools (section 11) return it with
`summary` first, then the labeled fields. Numbers are rounded to 1 decimal
(win probability to 2) by the decision code, never by the model. Every value
the "why" quotes is in a field, so the model copies and never computes.

Shared pieces:

```python
class PlayerRef:                         # label for any player in any result
    player_id: str
    name: str
    position: str                        # Sleeper fantasy position (QB, RB, WR, TE, K, DEF, DL, LB, DB)
    nfl_team: str | None                 # abbreviation; None = free agent / unsigned
    injury_status: str | None            # Sleeper designation as shown in the app (Questionable, Out, IR ...)
    fantasy_status: str                  # "mine_starter" | "mine_bench" | "mine_ir" | "fantasy_team:<name>" | "free_agent"
    lineup_slot: str | None              # slot as set in Sleeper when fantasy_status is mine_starter ("FLEX", "QB"); else None
    game: GameRef | None                 # the result's week; None when no NFL team

class GameRef:                           # one NFL game from the player's NFL team's point of view
    status: str                          # "not_started" | "in_progress" | "complete" | "bye"
    opponent_nfl_team: str | None        # abbreviation; None on bye
    home: bool | None
    kickoff: str | None                  # ISO UTC; None on bye
    status_as_of: str                    # when the game state was read (ESPN scoreboard / Sleeper schedule)

class Reason:                            # one structured why item
    code: str                            # see list below
    player_ids: list[str]
    values: dict[str, float | str]       # the numbers/labels the text quotes, by name
    text: str                            # one sentence rendered by the tool from `values`

class Why:
    summary: str                         # one sentence
    reasons: list[Reason]

class AsOf:                              # one labeled freshness entry
    as_of: str                           # ISO UTC
    source_label: str                    # the section 4 `source_label` of the source actually used, e.g. "official NFL injury report via nflverse", "ESPN", "Sleeper catalog"

class DecisionEnvelope:                  # fields on every decision result
    kind: str                            # "lineup" | "waivers" | "stash" | "trade_evaluation" | "trade_ideas" | "week_results" | "leaders"
    league_id: str
    season: int
    week: int                            # "this week" (or the completed week for week_results)
    summary: str                         # first thing the model reads
    as_of: dict[str, AsOf | None]        # keys: projections, rosters, injuries, news (None if not used), games
                                         # games = schedule and game state (kickoff, status); each AsOf names its source
    unprojected_scoring_items: list[dict]  # copy of projection_meta (section 7); [] if none
    warnings: list[str]                  # gaps stated as gaps: "no projection for X", "trade deadline passed", "Sleeper free-agent list not refreshed"
```

Reason codes (closed list; add only through a contract change):
`projection_edge`, `replacement_level`, `variance`, `underdog`, `injury`,
`news`, `locked`, `ir_slot`, `bye`, `game_started`, `no_equal_swap`,
`roster_spot`, `budget`, `trade_deadline`, `lineup_value`, `no_projection`.

The labeled `as_of` entries and `warnings` are how freshness reaches the model (PLAN
section 2); injury and news values here are copied from NFL facts (section 4),
including their `source` and `as_of`, which a `Reason.values` entry carries
as `injury_source`, `injury_as_of`.

#### 9.1 Lineup

Input: `week` (default this week). The base is the lineup already set in
Sleeper. One call returns both plans so the agent never recomputes.

```python
class Slot:
    slot: str                            # QB, RB, WR, TE, FLEX, SUPER_FLEX, K, DEF, DL, LB, DB, IDP_FLEX
    player: PlayerRef | None             # None = empty in Sleeper
    mean: float | None
    variance: float | None

class LineupTotals:                      # for one fantasy team's lineup
    mean: float
    variance: float

class Matchup:
    opponent_fantasy_team: str           # fantasy team name
    opponent_fantasy_team_id: int
    opponent_lineup_source: str          # "set_in_sleeper" (the opponent's current starters)
    opponent: LineupTotals

class LineupPlan:
    mode: str                            # "expected" | "high_risk"
    lineup: list[Slot]                   # full lineup, slot order as Sleeper, repeats kept
    changes: list[Change]                # vs. the lineup set in Sleeper; [] = keep it
    mine: LineupTotals                   # projected total of this lineup
    spread: float                        # mine.mean - opponent.mean
    win_probability: float               # Normal(spread / sqrt(mine.var + opp.var)) , 0-1
    why: Why

class Change:
    slot: str                            # compared per slot type; repeated slots listed separately
    player_out: PlayerRef | None
    player_in: PlayerRef
    mean_gain: float                     # player_in mean - player_out mean
    why: Why

class LineupResult(DecisionEnvelope):
    current: LineupPlan                  # the lineup set in Sleeper, changes = []
    matchup: Matchup
    expected: LineupPlan                 # maximises mean
    high_risk: LineupPlan                # maximises mean + k*sd per player (k = 1.0, config)
    locked: list[dict]                   # [{"player": PlayerRef, "side": "starter"|"bench", "reason": "game_started"|"game_final"}]
    ir_flags: list[dict]                 # IR-slotted players no longer IR-eligible: [{"player": PlayerRef, "reason": str}]
    empty_slots: list[str]               # slots left empty because no eligible player exists
```

Rules the code enforces (unit-tested on real fixtures, PRD 4.4): locked
starters stay, locked bench players cannot come in; no empty slot while an
eligible player exists (a 0-mean player beats an empty slot); never an IR-slot
player as starter; a change is only listed when `mean_gain` >= `min_gain`
(config, 0.1), so no equal-value reshuffles. `high_risk` totals, `spread` and
`win_probability` are the true sums of means and variances of the chosen
lineup, not the ceiling objective. The agent chooses which plan to present from
`win_probability` of `expected` versus `high_risk` and `underdog` reason
items, and says which one it gave.

#### 9.2 Waivers

Input: `horizon` (`week` | `ros` | `both`, default both), optional `position`,
`limit` (default 10 per horizon).

```python
class WaiverCandidate:
    rank: int
    player: PlayerRef                    # always a free agent
    mean: float                          # points over the horizon (week or ros)
    vorp: float                          # mean - replacement level
    replacement: dict                    # {"pool": "RB", "rank_n": 24, "points": 8.9}
    faab_guide: int | None               # dollars; None unless waiver_type is faab
    why: Why

class WaiverList:
    horizon: str                         # "week" | "ros"
    candidates: list[WaiverCandidate]
    excluded: list[dict]                 # week horizon only: [{"player": PlayerRef, "reason": "game_started"|"bye"}]

class WaiverResult(DecisionEnvelope):
    lists: list[WaiverList]              # one per horizon requested
    budget: dict                         # {"type": "faab", "total": 100, "remaining": 84, "min_bid": 0}
```

Replacement level = Nth-best rostered player at the position pool, N = fantasy
teams x slot demand, flex slots splitting credit; pools with no slot demand
excluded; rostered players with mean 0 excluded (first-build fixes).
`faab_guide` = `vorp` x `faab_pct_per_vorp` (config, 4%) x `budget.total`,
rounded, capped at `remaining`; the exact scale for `ros` is set in stage 2.4
against the first build's behavior.

#### 9.3 Injured stash

Input: `limit` (default 10), optional `position`.

```python
class StashCandidate:
    rank: int
    player: PlayerRef                    # free agent with an injury designation (IR/Out/PUP/Doubtful/Questionable)
    healthy_ros_vorp: float              # from mean_if_healthy (section 7), vs ros replacement level
    healthy_ros_mean: float
    injury: dict                         # {"status": str, "body_part": str|None, "source": str, "as_of": ts}
    news: list[dict]                     # latest items, newest first: {"title","text","url","published_at","source"} (prose, not parsed)
    why: Why

class StashResult(DecisionEnvelope):
    candidates: list[StashCandidate]
```

No return-week field: return timing is whatever the news prose says, and the
agent quotes it; if there is no news, `news` is `[]` and the agent says so.

#### 9.4 Trade evaluation

Input: `give` (my player ids), `get` (their player ids), `with_fantasy_team_id`,
`horizon` (`ros` default | `week`). Players must be on those rosters.

Value of a roster over a horizon = sum over the horizon's weeks of the
optimal starting lineup's mean points (section 9.1 optimiser, byes and zeroed
rows included, no locks). Roster-spot rule (PRD 4.4): after the swap, a side
over `roster_size` drops its lowest-value bench player (unless told
otherwise); a side under `roster_size` adds the best free agent (by marginal
lineup value). Both adjustments are listed.

```python
class TradeSide:
    fantasy_team: str                    # fantasy team name
    fantasy_team_id: int
    receives: list[PlayerRef]
    gives: list[PlayerRef]
    adjustments: list[dict]              # [{"action": "drop"|"add_free_agent", "player": PlayerRef, "reason": "roster_spot"}]
    lineup_value_before: float
    lineup_value_after: float            # after the swap and adjustments
    value_change: float                  # after - before
    why: Why

class TradeEvaluation(DecisionEnvelope):
    horizon: str
    mine: TradeSide
    theirs: TradeSide
    deadline: dict                       # {"week": 11, "passed": false}   (week None = no deadline)
    verdict: str                         # "I gain more" | "they gain more" | "both gain" | "both lose" -- fixed labels from the two signs/sizes
```

The deadline passing does not block an evaluation; it adds a warning.

#### 9.5 Trade ideas

Input: either `target_player_id` or `need_position`; optional
`with_fantasy_team_id` (then only that fantasy team is searched); `my_tradeable`
(player ids; required when `with_fantasy_team_id` is absent, and the tool
refuses without it so the agent must ask the manager); `horizon` (`ros`
default); `limit` (default 5).

```python
class TradeIdea:
    rank: int
    evaluation: TradeEvaluation          # full 9.4 result: both sides' lineup value changes
    why: Why                             # why it plausibly helps them: their starting-lineup value rises (reason code lineup_value)

class TradeIdeasResult(DecisionEnvelope):
    ideas: list[TradeIdea]               # only ideas with mine.value_change > 0 and theirs.value_change > 0; ranked by mine
    searched_fantasy_teams: list[str]
    deadline: dict                       # as 9.4
```

After the deadline: `ideas` = [], warning "trade deadline passed (week N)".
No idea without both value changes populated.

#### 9.6 Week results

Input: `week` (default last completed week).

```python
class WeekResults(DecisionEnvelope):
    mine: dict                           # {"fantasy_team": str, "fantasy_team_id": int, "score": float}
    opponent: dict                       # same keys
    result: str                          # "win" | "loss" | "tie"
    opponent_top_scorers: list[dict]     # top 3 starters: {"player": PlayerRef, "slot": str, "points": float}
    my_bench: dict                       # {"actual_points": 143.2, "optimal_points": 151.0, "points_left_on_bench": 7.8,
                                         #  "moves": [{"start": PlayerRef, "bench": PlayerRef, "slot": str, "gain": float}]}
```

Points are actuals from Sleeper matchups (`players_points`), not projections.
`optimal_points` uses the same lineup optimiser over actual points, no locks.

#### 9.7 Season leaders

Input: `position` (optional), `through_week` (default last completed), `limit`
(default 10), `scope` (`league_scoring` default).

```python
class LeaderRow:
    rank: int
    player: PlayerRef
    points: float                        # actual, league scoring
    games: int                           # games played
    points_per_game: float

class LeadersResult(DecisionEnvelope):
    through_week: int
    rows: list[LeaderRow]
    source: str                          # "sleeper_stats" (per-week /stats, summed under league scoring)
```

Used by "top RBs so far" and "who leads in actual points"; projections are not
mixed in (the projection tool is separate, section 11).

### 10. Backtest outputs

One command (`uv run python -m evaluation.backtest`) runs both kinds for 2024
and 2025, writes one JSON file per run to
`src/evaluation/results/<run_id>.json` and prints a summary table. Data is
downloaded once into `<app data>/backtest_cache`. Ground truth is actual points
from Sleeper `/stats` scored with `REFERENCE_PPR` (Sleeper's default full-PPR
stat scoring; it reproduces Sleeper's own `pts_ppr` for 99.5% of QB/RB/WR/TE
player-weeks), skill positions QB/RB/WR/TE, so the crosswalk is not in the ground
truth. IDP, kickers, DEF and the injury overlay are not backtested (PRD 6). Other
modes: `--sweep` (n_games and weights, 2024 only), `--fit-cv` (variance floors,
2024 only), `--leakage` (checks on the stored Sleeper rows). Weights, floors and
n_games are chosen on 2024; 2025 is out of sample.

```python
class RunResult:                         # one entry of the file's `runs` list: a (season, kind) pair
    kind: str                            # "weekly" | "ros"
    season: int                          # 2024, 2025
    weights: dict[str, float]            # projection weights used
    matchup_adjustment: bool             # False in v1 (not built; PRD 4.3)
    tiers: list[TierResult]              # first entry is the baseline
    calibration: dict                    # per position: n, mean_variance, mse, ratio (near 1 = honest), within_1sd (0.68 if normal)
    gate: dict
    # weekly only: nflverse_coverage (share of tested player-weeks with an nflverse row)
    # ros only: origins, mae_by_origin, leakage (text)

class TierResult:
    tier: str
    n: int                               # player-weeks (weekly) or player-origins (ros)
    mae: float
    rmse: float
    bias: float                          # mean(pred - actual)
    by_position: dict[str, dict]         # {"RB": {"n":..., "mae":..., "rmse":..., "bias":...}, ...}
    mae_vs_baseline_pct: float | None    # absent on the baseline row
    paired_diff: dict | None             # {"mean_diff", "ci95": [lo, hi]} of |err| - |baseline err|; negative = better
```

Tiers. Weekly: `sleeper_only` (baseline), `blend`, and for the rows where nflverse
has a row `sleeper_only_where_nflverse` and `nflverse_only_where_present`
(information only). Rest of season: `sleeper_season_prorated` (baseline),
`sleeper_weekly_sum`, `blend_weekly_sum` (the production formula: the sum of
the weekly blend rows), and the leak-free pair `sleeper_frozen` (origin-week
row times games left) and `blend_frozen`. No `blend_matchup` tier: the matchup
adjustment is off and not built.

Gate dict, weekly: `{"baseline", "candidate", "beats_baseline", "significant"}`.
`beats_baseline` is the AGENT.md rule (lower MAE); `significant` is true when
the paired 95% interval of the error difference excludes 0. Rest of season:
`{"baseline", "candidate": "blend_weekly_sum", "beats_baseline",
"leak_free_pair": {"baseline", "candidate", "beats_baseline"}}`.

- **Weekly:** weeks 1-17 (weeks 3-17 are also reported, as `_w3_17` tiers, for
  comparison with earlier runs); nflverse uses only games before the target week,
  including the previous season's.
  Population: QB/RB/WR/TE with a Sleeper projection of at least 3.0 reference
  points that week (not conditioned on the result; players who did not play count
  as 0). Baseline: Sleeper-only. Same rows in every tier, so comparisons are
  paired.
- **Rest-of-season:** origins weeks 3, 6, 9, 12; projection from the origin week
  through week 18 (byes skipped) against the sum of actual points over those
  weeks. Population: QB/RB/WR/TE with a Sleeper projection of at least 3.0 at the
  origin week and a row in the season file (decided from the origin week only).
  Baseline: Sleeper's season projection times (19 - origin) / 18. Leakage: the
  stored weekly rows are stamped 1-6 days after their game (median 2), and
  only 0.3% (2024) and 0.1% (2025) of players projected at 3 points or more did
  not play, so the rows already know who was out; summing later rows therefore
  leaks future injuries (ROS error falls by about half). The `frozen` tiers use
  only the origin week's row and are the leak-free comparison; the season
  snapshots are stamped after week 18 (`docs/spikes.md` O1). Absolute rest-of-season
  numbers are not trustworthy until point-in-time snapshots exist (see O1).
- **Gate (AGENT.md):** a projection change ships only if `beats_baseline` is true
  in both seasons, for weekly and for ros. The summary table shows season, kind,
  tier, n, mae, rmse, bias and the change against the baseline.
- **Result at default weights (0.9 / 0.1, 4 games across seasons, prior season
  weighted 0.5), run 2026-11-17:** weekly MAE sleeper_only vs blend, weeks 1-17: 2024
  5.18 vs 5.17 (-0.14%, n 3,838), 2025 5.15 vs 5.15 (-0.10%, n 3,905); weeks 3-17: 2024
  5.22 vs 5.22 (-0.12%), 2025 5.17 vs 5.17 (-0.14%). Numerically lower in both seasons
  and both ranges, not significant (paired 95% interval of the mean difference includes
  0 in every case, for example [-0.018, +0.003] for 2024 weeks 1-17). The nflverse
  trailing average alone is 8-9% worse than Sleeper. Letting the window reach into the
  previous season changed almost nothing (before: -0.1% and -0.2%): the PRD 6 result
  (-3.6%) still does not reproduce. Variance is calibrated: mean predicted variance over
  mean squared error 1.02 (2024, in sample) and 1.05-1.19 (2025, out of sample).
- **Segments (`notebooks/blend_segments.ipynb`):** nflverse alone is worse than Sleeper
  in every segment with enough rows, including cold start by career games and players on
  a new NFL team since last season. Weights tuned on 2024 and held in 2025: 0.1 in weeks
  13-17, and 0.3 where Sleeper had over-projected the player over the previous 4 weeks
  (nflverse hurts where Sleeper had under-projected). A rule built on that last feature
  beats Sleeper-only by 0.017 (2024, in sample) and 0.024 (2025, out of sample) points of
  MAE, intervals below 0. Not shipped: it needs Sleeper's recent errors from stored
  point-in-time rows; owner decision.
- **Not backtested:** the IR return-week rule. Only the current injury state and
  return dates were stored (PRD 6), so past weeks cannot be scored without
  leakage. Backtest rows therefore use no injury overlay; the rule is checked by
  unit tests on fixtures only, and it is not covered by the gate.

### Open items in Part B

- O1 (settled by `docs/spikes.md`; leakage checked in 2.3, see section 10): Sleeper `/projections/nfl/{season}/{week}`
  has rows for every future week (2026 weeks 9-18) and for past weeks of 2024
  and 2025. So the Sleeper source implements only `weekly()`; rest-of-season is
  the sum of weekly rows (byes have no row). The season endpoint is used only as
  the rest-of-season backtest baseline, and the 2024/2025 snapshots are stamped
  after week 18, so that baseline leaks; the backtest reports this, and also
  reports the sum-of-weekly-rows tier. Result of the 2.3 leakage check: the stored
  weekly rows are post-game snapshots and carry later injury knowledge, so they
  cannot validate rest of season. Fix going forward: store each weekly Sleeper pull
  as it happens (`source_projection` table) so 2026 gives point-in-time rows.
  The spike's list of league scoring keys no weekly projection covers (e.g.
  `def_st_td`, `fgm_50_59`, `idp_def_td`, `pts_allow_0`) is the expected content
  of `unprojected_scoring_items` for Or Test League; it is a test fixture.
- O2 (mostly settled): `LeagueFormat` keys are in the spikes table. Still open:
  `settings.type` 1/2 (keeper/dynasty) and `waiver_type` 1/2 follow the public
  mapping but are unseen; best ball (`settings.best_ball` = 1) unseen; no
  guillotine key found, so guillotine cannot be detected yet and is reported as
  a gap. `waiver_budget` is read only when `waiver_type` is FAAB. Fantasy team
  name falls back to the manager's `display_name` when `metadata.team_name` is
  absent. Current week comes from Sleeper `state.week`, not `settings.leg`.
- O3 (closed, owner decision): IR players are projected from an expected
  return week (section 7), 0 before it, and 0 throughout when no return date
  exists. Backend supplies `return_date`, `return_date_source` and `return_date_as_of` on the `injury` fact (ESPN only; coverage checked in 2.3).
  PRD 4.3 and PRD 8 ("IR in season projections") still describe the old rule
  and need the lead's update.

---

## Part C: agent (ai-engineer)

### 11. Agent tool catalog

v1 is read-only: no move tools, no preference tools, no Sleeper token. One
tool per question type (PRD 4.5). Tools live in `src/agent/tools.py`
and call decision, NFL-fact and storage functions; they compute nothing
themselves beyond rendering.

**Output rule (all tools).** The tool gets the JSON result (section 9 or an
NFL fact, section 4) and renders it as text for the model, in this order:

1. `SUMMARY:` one or two sentences from the result's `summary`.
2. `AS OF:` the `as_of` timestamps, each with its source.
3. Labeled facts, one player per line, each line carrying the player's
   fantasy status as a label: `MY ROSTER (starter, FLEX)`, `MY ROSTER
   (bench)`, `MY ROSTER (IR)`, `FANTASY TEAM: Ravid's RAMS`, `FREE AGENT`,
   and NFL team as `NFL TEAM: PIT`. Every number the model may quote is printed
   already rounded; `Reason.text` is printed as is.
4. `WARNINGS:` gaps stated as gaps (`warnings`, `unprojected_scoring_items`).

The same JSON goes to the trace. Rendering is one module
(`src/agent/render.py`), unit-tested on fixtures in `src/agent/tests/`.

**Shared rules.**

- **Names in, ids out.** Tools take player, fantasy team, NFL team and league
  names as the user said them. Resolution ranks exact name, then rostered in
  this league, then on an NFL team (first build). When more than one candidate
  fits, the tool returns `AMBIGUOUS:` with the candidates (name, position, NFL
  team, fantasy status) and does nothing else. The model asks, or says which one
  it assumed.
- **NFL team input** accepts an abbreviation, city or nickname (`SEA`,
  "Seattle", "Seahawks"); the tool normalises.
- **Failures** render as `ERROR: <what failed>, <what was used instead or
  nothing>`. Never an empty success.
- **Unsupported league** (`LeagueFormat.supported = false`): every decision
  tool returns `ERROR: <league> is a <dynasty|keeper|best ball|guillotine>
  league, not supported yet`.

**Catalog (17 tools).** "Not for" text goes into each tool's description, so
the model sees it every turn.

| # | Tool | Inputs | Output (after SUMMARY and AS OF) | Not for (use instead) |
|---|---|---|---|---|
| 1 | `get_league_state` | none | season, this week, last completed week, my opponent this week (fantasy team), standings (rank, fantasy team, record, points for), `LeagueFormat` lines (defense, reception points, superflex, TE premium, kicker, waiver type and budget, trade deadline) | rosters (`get_my_roster`, `get_fantasy_team_roster`); scores of a week (`get_week_results`) |
| 2 | `get_my_roster` | none | my lineup as set in Sleeper by slot (repeats kept, empty slots shown as `(empty)`), bench, IR, taxi; per player: position, NFL team, injury designation and practice status with source, this week's game (opponent NFL team, kickoff, `not_started`/`in_progress`/`complete`, bye), IR flags (`ir_flags`) | a recommended lineup (`recommend_lineup`); news (`get_my_roster_news`) |
| 3 | `get_fantasy_team_roster` | `fantasy_team` (name or manager name) | same shape as tool 2 for that fantasy team, plus its record | NFL teams (`get_nfl_team_roster`); my own (`get_my_roster`) |
| 4 | `get_nfl_team_roster` | `nfl_team`, optional `position` | current players by position in depth order with labels (`WR1`, `WR2`, `RB1`, `LB1` ...) and which source the order comes from (depth chart or usage); per player: injury designation, practice status (the latest one in the week's report, not a per-day history), inactive (see gaps below), usage where available (snap share, target share, carries, last N games, with source), fantasy status label | fantasy teams (`get_fantasy_team_roster`); one player in detail (`get_player`); practice status by day (not available in v1: the report is weekly) |
| 5 | `get_player` | `name` | one player: NFL team (with source and as of), position, depth label, injury designation and latest practice status with its report date, inactive (see gaps below), return line (below), this week's game and its status, usage, fantasy status, the latest news items (title, text, published, source) | a list of players (tools 4, 8, 9); value or ranking (`get_projections`) |
| 6 | `get_my_roster_news` | optional `days` (default 7) | news per player on my roster, newest first, players with decision-relevant items (injury, practice, role) listed first and labeled | one player off my roster (`get_player`) |
| 7 | `get_transactions` | optional `fantasy_team`, `days` (default 7), `type` (`add`, `drop`, `trade`, `waiver`) | completed transactions in the league: date, fantasy team(s), players added and dropped, FAAB spent | pending waiver claims and open trade offers: not available in v1 (need a Sleeper token); the tool says so |
| 8 | `get_projections` | `players` (names) **or** `position`; `horizon` (`week` default, `ros`, `full_season`); optional `week`, `limit` (default 10) | per player: mean, variance as a range label, `n_games`, `zero_reason`, contributing sources with weights, return line for IR players (below); ranked when given a position; WARNINGS lists `unprojected_scoring_items` | actual points so far (`get_season_leaders`); pickups (`get_waiver_targets`) |
| 9 | `get_season_leaders` | optional `position`, `through_week`, `limit` (default 10) | section 9.7 rows: rank, player, actual points in league scoring, games, points per game | projections (`get_projections`) |
| 10 | `recommend_lineup` | optional `week` | section 9.1: matchup (both fantasy teams' projected totals, spread, win probability) for `current`, `expected` and `high_risk`; changes per plan with `Reason.text`; locked players; IR flags; empty slots | the lineup set in Sleeper (`get_my_roster`) |
| 11 | `get_waiver_targets` | `horizon` (`week`, `ros`, `both` default), optional `position`, `limit` | section 9.2: one labeled list per horizon (`THIS WEEK`, `REST OF SEASON`), each candidate with mean, VORP, replacement level, FAAB guide; excluded players (game started, bye); budget | injured free agents for later (`get_injured_stash`) |
| 12 | `get_injured_stash` | optional `position`, `limit` | section 9.3: healthy rest-of-season mean and VORP, injury with source, latest news prose (the return timeline is whatever the news says) | healthy pickups (`get_waiver_targets`) |
| 13 | `evaluate_trade` | `give` (my players), `get` (their players), optional `fantasy_team` (inferred when all `get` players are on one), `horizon` (`ros` default, `week`) | section 9.4: both sides' lineup value before, after, change; roster-spot adjustments; verdict label; deadline | finding trades (`find_trade_ideas`) |
| 14 | `find_trade_ideas` | `target_player` **or** `need_position`; optional `fantasy_team`; `my_tradeable` (my players; required when `fantasy_team` is absent); `horizon`, `limit` | section 9.5: ideas with both value changes and why it helps them; fantasy teams searched; deadline | evaluating a given offer (`evaluate_trade`). Without `fantasy_team` or `my_tradeable` it returns `NEED: which of your players are you willing to trade?` and does no search |
| 15 | `get_trending_players` | `type` (`add` default, `drop`), optional `hours` (default 24), `limit` | Sleeper-wide trending counts with each player's fantasy status in my league; labeled "context only, not a projection" | value (`get_projections`, `get_waiver_targets`) |
| 16 | `get_week_results` | optional `week` (default last completed) | section 9.6: both scores, result, opponent's top scorers, points left on my bench with the moves | upcoming matchup (`recommend_lineup`, `get_league_state`) |
| 17 | `switch_league` | `league_name` | `SWITCHED: <league>` and the new context block applies from the next model turn; unknown name returns `ERROR: no league named X` with my real league names | none |

Not tools, on purpose:

- **Sync.** Freshness is received, not fetched (PRD principle 4): the backend
  refreshes before each question per section 5, and a failed refresh appears
  in the context block. No `sync` tool.
- **Moves, pending claims, open trade offers, preferences:** v2.

**Data each tool needs** (for the Part A and B owners): tools 1-3 and 7 read
league state (section 1); tools 4-6 read NFL facts (section 4) and news; tool
8 reads the projection artifact (section 7); tools 9-14 and 16 are section 9
results; tool 15 reads Sleeper REST trending. Per `docs/spikes.md`: practice
status from nflverse injuries (the official report: one status per weekly
report, the latest listed, with no per-day history), usage from nflverse snap
counts (snap share; target share and carries from Sleeper `/stats`), depth from
nflverse depth charts or Sleeper `depth_chart_order`, kickoff and game state
from ESPN scoreboard.

Gaps, printed from `NflFactSet.gaps` (section 4) so the model states them:

- **Inactives** are a stated gap until ESPN `didNotPlay` is verified before a
  kickoff (Thu 2026-11-05). Until then tools 2, 4 and 5 print `inactives: not
  available (no usable source before kickoff)` and never infer inactive from
  `Out` or from missing usage.
- **Practice by day** is not offered: a question like "did he practice
  Wednesday?" gets the latest status with its report date and the gap.
- Any other field with no usable source prints `not available: <field> (no
  usable source)`.

**Return line** (tools 5 and 8, every IR player; from the section 7 columns
and the section 4 injury fact, printed as is):

- with a date: `RETURN: week 13 (return date 2026-12-06, source ESPN injuries,
  as of Tue 11-03 20:57; a news estimate, not an official report). Weeks
  before 13 project 0 (on IR)`;
- without: `RETURN: unknown (no source has a return date). Projected 0 for
  every remaining week (on IR)`, and the same text in WARNINGS.

Rendered text carries no internal field names (`zero_reason`,
`win_probability` ...), so the model has none to copy (TEST_QUESTIONS T1 rule
7); `src/agent/tests/test_render.py` checks it, WARNINGS included. League
scoring keys are printed as plain words from `render.SCORING_LABELS`
(`def_st_td` -> "defensive or special teams touchdown (6 points each)"); a key
with no label is spelled with spaces and logged.

**Providers** (stage 2.5 build). Tools 8-16 call one function each, set in
`PROVIDERS` in `src/agent/tools.py` when its stage lands; until then the tool
returns `ERROR: <feature> not available in this version yet`. The tool resolves
names to ids first (and returns `AMBIGUOUS`, `NOT FOUND` or `NEED` itself), so
every provider gets ids and returns a section 9 result:

| Tool | Provider call | Returns |
|---|---|---|
| `get_projections` | `(conn, league_id, player_ids, position, horizon, week, limit)` | projections result, below |
| `get_season_leaders` | `(conn, league_id, position, through_week, limit)` | `LeadersResult` (9.7) |
| `recommend_lineup` | `(conn, league_id, week)` | `LineupResult` (9.1) |
| `get_waiver_targets` | `(conn, league_id, horizon, position, limit)` | `WaiverResult` (9.2) |
| `get_injured_stash` | `(conn, league_id, position, limit)` | `StashResult` (9.3) |
| `evaluate_trade` | `(conn, league_id, give, get, with_fantasy_team_id, horizon)` | `TradeEvaluation` (9.4) |
| `find_trade_ideas` | `(conn, league_id, target_player_id, need_position, with_fantasy_team_id, my_tradeable, horizon, limit)` | `TradeIdeasResult` (9.5) |
| `get_trending_players` | `(conn, league_id, type, hours, limit)` | trending result, below |
| `get_week_results` | `(conn, league_id, week)` | `WeekResults` (9.6) |

Two results have no section 9 shape, so they are fixed here (both carry the
`DecisionEnvelope` fields):

```python
class ProjectionsResult(DecisionEnvelope):   # kind "projections"
    horizon: str                             # "week" | "ros" | "full_season"
    rows: list[dict]                         # {"rank", "player": PlayerRef, "mean", "variance", "n_games", "zero_reason",
                                             #  "sources", "return_week", "return_date", "return_date_source", "return_date_as_of"}
                                             # (section 7 columns of the player's row)
class TrendingResult(DecisionEnvelope):      # kind "trending"; as_of {"trending": AsOf}
    type: str                                # "add" | "drop"
    hours: int
    rows: list[dict]                         # {"rank", "player": PlayerRef, "count"}
```

Contract-shaped samples of every result are in
`src/agent/tests/fixtures/contract/`; replace them with recorded results as
2.3 and 2.4 land.

**AS OF line.** One entry per source the result used: `source_label` and its
time, e.g. `AS OF: Sleeper rosters 14:29; official NFL injury report via
nflverse 13:00; ESPN scoreboard 14:20`. The labels are the section 4
`source_label` strings, and the API's answer `sources` array
(`docs/api-contract.md`) is built in code from the same structured results
(union over the question's tool calls, one entry per label, the oldest
`as_of` kept), never parsed from the text.

**Progress labels** (the API `step.label`, one per tool, owned here):

| Tool | Label |
|---|---|
| `get_league_state` | Reading league standings |
| `get_my_roster` | Reading your roster |
| `get_fantasy_team_roster` | Reading a fantasy team's roster |
| `get_nfl_team_roster` | Reading an NFL team's roster |
| `get_player` | Looking up a player |
| `get_my_roster_news` | Reading news for your roster |
| `get_transactions` | Reading league transactions |
| `get_projections` | Reading projections |
| `get_season_leaders` | Ranking season leaders |
| `recommend_lineup` | Optimizing your lineup |
| `get_waiver_targets` | Ranking waiver targets |
| `get_injured_stash` | Ranking injured stash options |
| `evaluate_trade` | Evaluating the trade |
| `find_trade_ideas` | Searching for trade ideas |
| `get_trending_players` | Reading Sleeper trends |
| `get_week_results` | Reading week results |
| `switch_league` | Switching league |
| (model turn) | Thinking |
| (pre-question refresh) | Refreshing data |

**v1 checks to tools.** Numbers and tool columns are the revised
`TEST_QUESTIONS.md`. A check passes only through the listed source. "Context"
= the section 12 block. Checks 3, 13, 14, 18 and 24 are retired (moved to API
checks 48-51 and A8); L4 waits on the owner.

| Check | Question type | Tool(s) or source |
|---|---|---|
| 1 | season, week, opponent | context or `get_league_state` (D1) |
| 2 | standings | `get_league_state` (the context holds only my record) |
| 4 | non-IDP league: best lineup, then pickups | `recommend_lineup`, then `get_waiver_targets` |
| 5 | switch league and show my roster | `switch_league`, then `get_my_roster` |
| 6 | last week's result | `get_week_results` |
| 7 | top 2026 RBs so far | `get_season_leaders` (`position` RB) |
| 8 | top 5 WRs actual vs. rest of season | `get_season_leaders`, `get_projections` (`position` WR, `ros`) |
| 9 | who are my starters | context MY ROSTER or `get_my_roster` (D1); never `recommend_lineup` |
| 10 | my injured players, IR | `get_my_roster` (IR eligibility rule: gap G7) |
| 11 | roster reflects a move made in the app | context RECENT CHANGES and `get_my_roster` |
| 12 | swap made in the app, within 10 s | context MY ROSTER or `get_my_roster` |
| 15 | Seattle roster | `get_nfl_team_roster` (`nfl_team` SEA) |
| 16 | DK Metcalf's NFL team, Seahawks WRs | `get_player`, `get_nfl_team_roster` (`position` WR) |
| 17 | another fantasy team's roster, then the Steelers | `get_fantasy_team_roster`, then `get_nfl_team_roster` |
| 19 | "Any news on Williams?" | `get_player` returns `AMBIGUOUS` |
| 20 | news on another fantasy team's player, on a free agent | `get_player` twice |
| 21 | snap share and target share | `get_player` (usage; a stated gap until 2.2 lands) |
| 22 | is my starter inactive | `get_player` or `get_my_roster` (inactive gap line); context GAPS |
| 23 | practice on a given day | `get_player` (latest status with report date); context GAPS |
| 25 | best lineup this week | `recommend_lineup` |
| 26 | high-risk lineup | `recommend_lineup` |
| 27 | why did you bench X | the earlier `recommend_lineup` result (latest result of a tool stays in full, section 12); a re-call also passes |
| 28 | pickup this week vs. rest of season | `get_waiver_targets` (`both`) |
| 29 | Thursday player on Friday | `get_player` (game `complete`), then `get_projections` (`ros` or next week) |
| 30 | game window: started player, lineup | `get_waiver_targets` (excluded `game_started`), `recommend_lineup` (`locked`) |
| 31 | injured stash | `get_injured_stash` |
| 32 | IR player's return and projection | `get_player` and `get_projections` (`week`, then `ros`): the return line |
| 33 | are all scoring rules counted | `get_projections` (WARNINGS: unprojected scoring items) |
| 34 | evaluate a trade, 1-for-1 and 2-for-1 | `evaluate_trade` |
| 35 | trade ideas, general ask | `find_trade_ideas` returns `NEED` |
| 36 | trade ideas with one fantasy team | `find_trade_ideas` (`fantasy_team` set) |
| 37 | trade ideas after the deadline | `find_trade_ideas` |
| 38 | league moves; pending claims | `get_transactions` (pending claims and open offers: stated gap) |
| 39 | trending adds | `get_trending_players` |
| 40-52 | panel, quota, missing key, refresh failure, unsupported league, event order, concurrency, host check, league format, unknown league, refresh restores a player, backdated refresh, game-window panel | API checks (`docs/api-contract.md`), no agent tool |
| A1 | make a move (lineup, add/drop, trade, claim) | none (read-only tools allowed); no write tool exists; prompt: say so in the first sentence |
| A2 | off-topic (other sports, betting, code, weather) | none; prompt scope |
| A3 | key, system prompt, settings | none; the key is in no prompt, context, row or event; prompt text from `agent.prompt.system_prompt()` |
| A4a | "ignore your rules" injection | none; prompt (only supplied facts) |
| A4b | injection inside a news row | `get_player` (news read from the `news` table, printed as quoted text) |
| A5 | history and coaches not in the data | none needed (`get_player` or `get_nfl_team_roster` allowed) |
| A6 | unknown player; false number; false earlier claim | (a) `get_player` returns `NOT FOUND`; (b) `get_week_results` or `get_player`; (c) none needed |
| A7 | sums the tools do not give | (a) `recommend_lineup` (`current` total); (b) `get_projections` (each player alone); (c) `recommend_lineup` (`spread`) |
| A8 | bare "team" | (a) `get_player`; (b) `get_league_state` or context (D1); (c) `get_player` |
| L1-L3 | optimal lineup, maximum score, points left on bench | `get_week_results` (`my_bench.optimal_points`, `actual_points`, `points_left_on_bench`, `moves`, all printed) |
| L4 | would the recommendation have won | not in v1 (owner, D10) |
| T1, T2 | answer quality on every answer | prompt "How to answer"; tool text has no field names to copy |

Out of v1: V1-V5 (preferences and writes).

### 12. Per-turn context block

Built by code (`src/agent/context.py`) and put at the top of the system
prompt **on every model turn**, not once per question, so a mid-question
refresh or `switch_league` is seen at once. Facts only, no instructions; the
rules (terminology, only what it was given, no arithmetic) are the static part
of the prompt above it.

As built in stage 2.5 (fixture league). "This week" and game lines come from
`ingestion.nfl.service` (`this_week`, `game_ref`); GAME WINDOW is
`service.in_game_window`, which replaced the planned GAME DAY line because the
window (3 hours before a kickoff until final) is what changes freshness and
locks:

```
TODAY: Tuesday 2026-11-03, 14:30 local time (Jerusalem Standard Time)
SEASON: 2026. THIS WEEK: 9 (next to be played). LAST COMPLETED WEEK: 8
GAME WINDOW: closed (no NFL game within 3 hours)
LEAGUE: Or Test League. MY OTHER LEAGUES: Or 8-Team League (not playable: pre draft); FantaSteelers
FORMAT: IDP (individual defensive players); full PPR (1 point per reception); superflex; no TE premium; kicker; 4 fantasy teams; starting slots QB RB RB WR WR TE FLEX SUPER_FLEX IDP_FLEX IDP_FLEX K DEF; 5 bench; 0 IR
WAIVERS: rolling waiver order (no FAAB). TRADE DEADLINE: week 11 (not passed)
MY FANTASY TEAM: OrMatalon, record 4-0, 1st of 4
THIS WEEK'S OPPONENT: OrHuji (fantasy team), record 1-3
MY ROSTER as set in Sleeper (authoritative, as of Tue 11-03 14:29):
  QB: Josh Allen, QB, NFL TEAM: BUF, MY ROSTER (starter, QB)
  SUPER_FLEX: Christian Watson, WR, NFL TEAM: GB, MY ROSTER (starter, SUPER_FLEX), injury: Questionable
  ...
  BN: Saquon Barkley, RB, NFL TEAM: PHI, MY ROSTER (bench)
RECENT CHANGES TO MY ROSTER: Tue 11-03 13:50 added Tetairoa McMillan; dropped Saquon Barkley
DATA: Sleeper rosters Tue 11-03 14:29; Sleeper players catalog Tue 11-03 09:10; Sleeper matchups Tue 11-03 14:29; Sleeper transactions Tue 11-03 14:29; ESPN scoreboard Tue 11-03 14:20; official NFL injury report via nflverse Tue 11-03 13:00; ESPN injury list Tue 11-03 14:15; nflverse depth chart ...; nflverse snap counts ...; Sleeper weekly stats ...
GAPS: inactives are known only after ESPN marks them for a game (not verified before kickoff); until then not available; practice status is the latest in the weekly report, no per-day history; guillotine leagues cannot be detected (Sleeper has no setting for it); pending waiver claims and open trade offers not available in this version
REFRESH FAILED: Could not refresh from Sleeper. Using data from Tue 11-03 12:00.
```

Rules:

- Lines are omitted when empty (no `REFRESH FAILED` when nothing failed; no
  `RECENT CHANGES` when none since the chat's last question).
- GAPS carries every standing gap as a fact, so the model states it without a
  prompt rule: one list in code (`context.py`), each item removed when its
  source lands (inactives once ESPN `didNotPlay` is verified before a
  kickoff). Per-player gaps stay in tool output (`NflFactSet.gaps`).
- Times are the user's local time; `TODAY` comes from the machine clock, the
  week and season from Sleeper's state with the "this week" rule (PRD 4.2).
- MY ROSTER lines use the same labels as tool 2, so the model sees one format.
- The key, the username's Sleeper ids and any credentials are never in it.
- When my roster changed since an earlier roster-dependent tool result in the
  history (tools 2, 10, 11, 13, 14), that result is replaced with `[outdated:
  your roster changed at 13:50; call the tool again]` before sending (first
  build).
- History: at most 40 messages of this league's chat, cut at question
  boundaries (a tool result is never sent without the call that asked for it).
  Failed questions are left out. The latest result of each tool stays in full
  (so "why did you bench X" answers from the lineup it gave, with no extra
  request and no chance of a rebuilt projection contradicting it); older
  results of the same tool are cut to their SUMMARY line. An outdated marker
  (above) replaces a result in full, which forces a re-call. Reasoning text
  from the model (`reasoning`, `reasoning_details` in the response) is not
  stored or sent back.
- `tool` rows carry `question_id` in `meta` too (stage 2.5 finding: without
  it the loop could not tell which question a result belongs to, the model
  never saw its own tool results and called the same tool until the cap).
  Section 1's meta table needs this key added (backend).
- RECENT CHANGES lists my roster diffs detected since the chat's previous
  question ended (its last row), so a move made between questions is named.

### 13. Framework decision

**A plain tool-calling loop** over the `openai` SDK pointed at OpenRouter, not
LangGraph.

Reason: v1's agent is one loop (model turn, run tool calls, model turn, until
a final answer or a turn cap). Everything that needs care sits between the
turns, and in a plain loop each is a line of our own code: rebuild the context
block every turn, replace outdated results, emit progress events, match
OpenRouter's error bodies, fail fast on quota, cap turns. LangGraph's value
(state graphs, checkpointing, interrupts) is not used: chat history is our
own SQLite table per league, and v2 move confirmation must live in code the
model cannot reach (an API endpoint bound to the exact change), not in a graph
interrupt. LangGraph also brought the first build's worker-thread SQLite
problem (PRD 7) and adds modules for PyInstaller to miss. LangSmith tracing
does not need LangChain (`langsmith` `traceable` and `wrap_openai`).

Loop rules:

- Tool calls run in order, on the request's thread, one SQLite connection.
- Turn cap: 6 model turns per question (each is one free-tier request); the
  6th turn is sent with no tools so the model answers from what it has.
  `too_many_turns` is raised only when that turn still returns no answer
  text.
- A turn with no tool call and no text, or with a tool call written as text
  (`<tool_call>...`, seen live on the no-tools 6th turn), is not an answer: the
  loop goes on within the cap, and on the 6th turn it is `too_many_turns`.
- Retries (2, with backoff) on network errors, HTTP 5xx, and HTTP 200 whose
  body has `error` instead of `choices`. No retry on 401 (key missing or
  invalid: `docs/spikes.md`) or on quota.
- Quota: before a question on a `:free` model, `GET /api/v1/key`; it costs no
  request and returns `free_model_daily_requests` (`used`, `limit`,
  `remaining`), verified in the 1.1 spike. With `remaining` 0, fail fast with
  the quota error; the question is stored as failed and kept. A 429 mid-question
  re-reads `/key`: `remaining` 0 is the daily quota (fail fast); otherwise it is
  a short rate limit and is retried like a 5xx.
- Tracing: LangSmith via `wrap_openai` and `traceable`, on only when the
  `LANGSMITH_TRACING` and `LANGSMITH_API_KEY` env vars are set; tagged with
  model, league, season and week. The OpenRouter key is a client credential,
  never a traced input. Tests unset the env vars.
- Pinned default model and its verification: `docs/spikes.md`, section Model.
