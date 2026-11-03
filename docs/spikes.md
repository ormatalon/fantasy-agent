# Phase 1 spikes

Findings from task 1.1. Run on 2026-11-03 (Tuesday after the week 8 Monday game;
Sleeper `state` says `week: 9`, `display_week: 8`, so "this week" = 9).
Fixtures are real responses trimmed to a few rows, under
`backend/tests/fixtures/<source>/`.

## Model

**Verdict: keep `nvidia/nemotron-3-ultra-550b-a55b:free` as the pinned
default.** Still free and routes tools correctly.

- Catalog (`GET /api/v1/models`, keyless): listed, prompt and completion
  price 0, `tools` in `supported_parameters`, 1M context. 13 free models list
  `tools` today, among them `nvidia/nemotron-3.5-super-vl:free`,
  `nvidia/nemotron-3.5-lightning:free`, `google/gemma-4-31b-it:free`
  (untested; Settings candidates only).
- Routing: 5 prompts against 7 mock tools (fantasy team vs. NFL team names,
  "not for" lines in descriptions, a context block in the system prompt).
  5 of 5 picked the expected tool with correct arguments, both traps included:
  "Who are my starters?" -> `get_my_roster` (not `recommend_lineup`); "What
  NFL team is DK Metcalf on?" -> `get_player` (not memory); "Seattle" ->
  `get_nfl_team_roster {"nfl_team": "SEA"}`; "Ravid's roster" ->
  `get_fantasy_team_roster {"fantasy_team": "Ravid's RAMS"}` (resolved from
  the context's opponent line); "pick up this week" ->
  `get_waiver_targets {"horizon": "week"}`. A follow-up turn with a tool
  result answered only from it ("PIT"). 1.8-4.0 s per turn, about 1,000
  prompt tokens, all served by provider `Nvidia`.
- Response shape: `finish_reason` `tool_calls`, `content: null`, arguments as
  a JSON string. The message also carries `reasoning` and `reasoning_details`
  (the model's own reasoning text): store for traces, never send back to the
  model.
- Quota: `GET /api/v1/key` returns `free_model_daily_requests: {used, limit:
  50, remaining}` and does not count as a request (verified: 18 -> 24 after
  6 chat calls; repeated `/key` reads unchanged). Use it before a question to
  fail fast. The body of an exhausted-quota response was not seen (we didn't
  burn the quota to see it); match it on HTTP 429 plus `remaining` 0 and capture
  the real body on the first hit.
- Errors seen: invalid key -> HTTP 401 `{"error":{"message":"User not
  found.","code":401}}`; no key -> HTTP 401 `{"error":{"message":"No cookie
  auth credentials found","code":401}}`. No HTTP-200 error body appeared in
  this run; the retry rule (200 with `error` and no `choices`) stays from the
  first build, unverified here.
- Fixtures (`backend/tests/fixtures/openrouter/`): `tool_call_{starters,
  seattle,fantasy_team,waivers,metcalf}.json`, `final_answer_after_tool.json`,
  `error_401_invalid_key.json`, `error_401_missing_key.json`,
  `key_info_free_tier.json` (trimmed to the quota fields; account ids removed).

## Sources

### Verdicts

| Source | Verdict | Reason |
|---|---|---|
| NFL.com official injury report (`nfl.com/injuries`) | Not usable | Page parses (server HTML, `Practice Status`, `Game Status`), but NFL.com Terms 1.3 allow "individual non-commercial" use and prohibit "systematic retrieval ... to create or compile a database". Our SQLite store is that. robots.txt allows `/injuries`; the terms are the blocker. Also still showed Week 8 on Tuesday |
| NFL.com game-day inactives | Not usable | Same terms (Game Center pages). No separate public endpoint found |
| ESPN public JSON (`site.api.espn.com`, `sports.core.api.espn.com`) | Usable, with risk | Unauthenticated, 200 with any User-Agent, stamped today (injuries 18:57Z, roster 19:29Z). Undocumented, no published terms for these hosts; `www.espn.com/robots.txt` covers the website only and does not disallow `/apis/`; `site.api.espn.com/robots.txt` returns 403. Treat as best-effort, one module, drop on break |
| nflverse release assets (CC-BY-4.0) | Usable | Weekly rosters, depth charts, snap counts, injuries, schedule all updated today. Participation is the exception, below |
| nflverse `pbp_participation` | Not usable in season | Only `pbp_participation_2025.*` (last update 2026-02-10); no 2026 asset. Snap counts cover the need |
| Sleeper GraphQL `league_rosters`, `get_player_news` | Usable | Needs a normal User-Agent (Python's default `Python-urllib` gets 403 / Cloudflare 1010). Variables work. `league_rosters` 0.25 s, same starters as REST right now (the freshness gain over REST only shows right after a move; not provable in a read-only spike) |
| Sleeper REST discovery (user, leagues, league, users, rosters, matchups, transactions, state, players) | Usable | Keyless, current |
| Sleeper `/projections`, `/stats`, `/schedule` (undocumented) | Usable | Details under O1 |

### ESPN

- Injuries: `GET site.api.espn.com/apis/site/v2/sports/football/nfl/injuries`.
  One call, all 32 NFL teams, per player: `status` (Out, Questionable ...),
  `date`, `details` (type, side, `returnDate`), `shortComment`/`longComment`
  (RotoWire prose, source named). 8.7 MB raw, 0.35 MB gzipped; poll it
  sparingly (every 10-15 min) and keep the 2-minute cycle for the scoreboard.
- Scoreboard: `.../scoreboard` (240 KB): `events[].date` (UTC kickoff, ISO),
  `status.type.name` (`STATUS_SCHEDULED`, `STATUS_IN_PROGRESS`, `STATUS_FINAL`),
  `week.number`, `teamsOnBye`. This answers kickoff time and game state.
  Note ESPN `week.number` was 8 while Sleeper `week` is 9.
- Rosters: `.../teams/{id}/roster` (ESPN team ids, 26 = SEA). Depth charts:
  `.../teams/{id}/depthcharts` (by formation, with each player's injury) and
  core `.../seasons/2026/teams/{id}/depthcharts`.
- Inactives: core `.../events/{event}/competitions/{event}/competitors/{team}/roster`
  has `didNotPlay` per player (9 of 59 for CHI @ SEA). Whether it is populated
  before kickoff is NOT verified (all `active` flags were false after the
  game). Check on the next game day (Thu 2026-11-05 BAL vs JAX, 20:15 ET;
  inactives are due about 90 minutes before). Until then inactives are a gap.
- Ids: ESPN ids appear in the nflverse rosters (`espn_id`, 75%) and depth charts
  (100%); Sleeper's catalog carries `espn_id` for only 23% of active players.

### nflverse (`github.com/nflverse/nflverse-data/releases`, tag per dataset)

| Asset | Latest | Notes |
|---|---|---|
| `weekly_rosters/roster_weekly_2026.csv.gz` | 2026-11-03 15:06Z, through week 8 | NFL team per player per week. NFL teams on bye have no row for that week (a "latest week only" filter drops them: HOU, NO, NYG, SF in week 8), so take each player's latest row. Metcalf is not on SEA. Carries `gsis_id` (100%), `sleeper_id` (70% of rows, mostly linemen missing), `espn_id`, `pfr_id` |
| `depth_charts/depth_charts_2026.csv.gz` | snapshot 2026-11-03T14:59Z | 275 snapshots since March, about 2,300 rows each; filter to the max `dt`. Has `espn_id`, `gsis_id`, `pos_abb`, `pos_slot`, `pos_rank`. 14 MB gzipped: download once, not every cycle |
| `snap_counts/snap_counts_2026.csv.gz` | 2026-11-03 12:01Z, week 8 | `offense_pct`, `defense_pct`, `st_pct`; keyed by `pfr_player_id` only (bridge through roster `pfr_id`) |
| `injuries/injuries_2026.csv.gz` | 2026-11-03 14:59Z, week 8 | The official practice and game-status reports: `report_status`, `practice_status`, body parts. Week 9 does not exist yet (reports start Wednesday). `gsis_id` keyed. This is the usable path to the official report |
| `schedules/games.csv` | 2026-11-03 19:16Z | `gameday` + `gametime` in US Eastern (CAR@GB Thu 20:15 = ESPN 00:15Z), scores, `espn` game id. Gives kickoff times for every week now. No game status other than scores |
| `pbp_participation` | 2025 only | not usable in season |

Cadence seen: injuries and depth charts refreshed within the hour, rosters and
snap counts daily. So nflverse meets the 2-hour rule, not the 2-minute one.
The 2-minute game-day cycle rests on ESPN scoreboard and Sleeper only.

### Sleeper

- Discovery: `GET /v1/user/OrMatalon` -> `user_id` `1268547099817943040`;
  `GET /v1/user/{user_id}/leagues/nfl/2026` returns 3 leagues:
  "Or 8-Team League" (`status: pre_draft`), "Or Test League" (in_season, IDP,
  superflex, 4 fantasy teams, id `1412371724632166400`), "FantaSteelers"
  (in_season, 10 fantasy teams, `reserve_slots: 2`). Several leagues, so the
  picker is exercised; a `pre_draft` league must be shown but is not playable.
- User object has `token: null`, `email: null`; nothing secret returned.
- GraphQL: introspection fields are snake_case (`__schema { query_type {
  fields { of_type } } }`), 245 query fields. Used:
  `league_rosters(league_id: Snowflake!)` -> `Roster {roster_id owner_id
  starters players reserve taxi settings metadata}`;
  `get_player_news(sport, player_id, limit)` -> `{player_id source published
  source_key metadata{title description analysis topic_id}}` (source
  `rotowire`, `published` epoch ms).
- Players catalog (`/v1/players/nfl`, 14.7 MB, fetch about daily): has
  `team`, `position`, `depth_chart_order`, `injury_status`, `injury_body_part`,
  `practice_participation`, `gsis_id`, `espn_id`. `gsis_id` only on 17% of
  active players and `espn_id` on 23%, so the nflverse `sleeper_id` column is
  the primary bridge (see Crosswalk in the data contract).
- Crosswalk probe (Sleeper active, fantasy and IDP positions, on an NFL team,
  2,142 players): 81% by nflverse `sleeper_id` (latest row per player), +12%
  by name + NFL team (`LAR` = `LA`), 7% unmatched (mostly stale practice squad
  entries; 19 with `search_rank` < 600).

### O1: Sleeper projections for future weeks and past seasons

`GET api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular`
(all players, about 5.7 MB, 9,400 rows, about 1,000 with nonzero points).

- Future weeks exist: 2026 weeks 9, 12 and 18 are all present (week 9
  refreshed today 19:30Z, week 18 at 00:45Z). Row: `player_id`, `week`,
  `opponent`, `date` (date only), `game_id`, `company: rotowire`, `stats` with
  about 95 keys (pass_*, rush_*, rec_*, idp_*, fg*, xp*, `pts_allow` buckets,
  plus precomputed `pts_ppr`/`pts_half_ppr`/`pts_std` which we ignore and score
  ourselves). Team DEF rows exist (`DEN`).
- League scoring keys of Or Test League that no weekly projection projects:
  `def_st_ff`, `def_st_fum_rec`, `def_st_td`, `fgm_50_59`, `fgm_60p`,
  `fgmiss`, `fum_rec_td`, `idp_blk_kick`, `idp_def_td`, `pts_allow_0`,
  `pts_allow_1_6`, `pts_allow_7_13`, `pts_allow_35p`, `st_fum_rec`, `st_ff`.
  These go in `unprojected_scoring_items`. Only the middle points-allowed
  buckets (`pts_allow_14_20`, `21_27`, `28_34`) are projected, so team DEF
  points allowed is partly covered.
- Past weeks of 2024 and 2025 exist for every week tried (2025 weeks 1, 3, 9,
  18; 2024 week 9). `last_modified` is after the games (2025 week 9:
  2025-11-04, the Tuesday after; 2025 week 1 and 3: both 2025-10-06; 2025
  week 18: 2026-01-05). The file does not prove the values are the last
  pre-game projection; ds must check for leakage (compare players who left
  injured or were ruled out).
- Season endpoint `.../projections/nfl/{season}?season_type=regular` exists
  for 2024, 2025, 2026 (`week: null`, `gp` 18). 2026 refreshes daily
  (2026-11-03 08:50Z) and is still a full-season total mid-season, not a
  rest-of-season number. The 2024 and 2025 snapshots are stamped after
  week 18 (2025-01-05, 2026-01-04), not at season start. So a prorated
  season baseline for origins in weeks 3-12 must use that end-of-season
  snapshot (leakage risk, note it in the backtest), or the sum of the stored
  weekly rows. Season rows lack some weekly keys (`rec_tgt`, `idp_pass_def`,
  `idp_qb_hit`, `idp_tkl_loss`, `def_td`, `pts_allow` buckets...).
- Actuals: `/stats/nfl/{season}/{week}` works for 2026 week 8 and 2024 week 9
  (same keys as projections).
- Schedule: `/schedule/nfl/regular/2026` -> 272 games, `status` `pre_game` or
  `complete`, `date` only. In-game status value still not captured.

### O2: raw Sleeper setting keys behind LeagueFormat

From `GET /v1/league/{id}` on the three leagues (fixtures:
`sleeper/league_or_test_league.json`, `sleeper/user_leagues_2026.json`).

| LeagueFormat field | Sleeper key | Seen |
|---|---|---|
| unsupported kind | `settings.type` (0 redraft, 1 keeper, 2 dynasty) | 0 in all three; 1 and 2 are the public mapping, not seen |
| best ball | `settings.best_ball` | absent in two leagues, `0` in FantaSteelers; `1` unseen |
| guillotine | no key found | unconfirmed; needs a real guillotine league fixture |
| waiver type | `settings.waiver_type` | 0 in all three; public mapping 0 rolling, 1 reverse standings, 2 FAAB (unseen) |
| FAAB budget | `settings.waiver_budget` (100), `waiver_bid_min` (0) | present even when `waiver_type` is not FAAB, so read it only when FAAB. Spent: roster `settings.waiver_budget_used` |
| trade deadline | `settings.trade_deadline` (week; 11, 13) | |
| playoffs | `settings.playoff_week_start` (15) | |
| IR / taxi slots | `settings.reserve_slots` (0, 2), `settings.taxi_slots` (0) | |
| fantasy teams | `settings.num_teams` (also `total_rosters`) | |
| current week | `settings.leg`, `last_scored_leg` | `leg` 8 while Sleeper state `week` is 9 (the Monday game done) |
| slots | `roster_positions` (QB RB WR TE FLEX SUPER_FLEX IDP_FLEX K DEF DL LB DB BN) | bench = count of `BN` |
| scoring | `scoring_settings` | `rec` 1.0 in all three; `bonus_rec_te` absent when 0 (absent key = 0) |
| league status | `status` | `pre_draft`, `in_season` |

Fantasy team names: `league_users[].metadata.team_name` is absent for both
managers of Or Test League (fall back to `display_name`).

### Not done / open

- ESPN inactives before kickoff: verify Thu 2026-11-05 (see ESPN).
- nflverse injuries for week 9 appear after Wednesday practice reports.
- Sleeper GraphQL freshness over REST after a roster move: verify in stage 2.1
  by making a move in the Sleeper app.
- Guillotine and best-ball setting values: need example leagues.
