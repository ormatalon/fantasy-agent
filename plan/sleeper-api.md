# Plan: use Sleeper's APIs directly instead of the workarounds

Status: phases 1-6 implemented, not committed. The live checks that need
`SLEEPER_TOKEN` or a game in progress are still open. Branch: `use-sleeper-api`.

## Implementation notes (2026-10-08)

Where the build differs from the plan below, and why:

- **The lineup mutation is still unresolved, so `set_lineup` sends both.**
  It sends `update_matchup_leg` and `roster_update_starters` with the same
  list, then reads back `matchup_legs` and `league_rosters`. Whichever one
  Sleeper scores from, both views agree. Test question 23 shows whether either
  read-back says MISMATCH. Then drop the mutation that isn't needed and, if
  the matchup leg is the one that scores, point `get_my_roster` at it. As of
  now (no games played in week 5), roster `starters`, live `starters` and
  matchup `starters` agree for every team in both leagues checked.
- **No `player_week_stats` table.** One week of stats is ~1.7 MB and takes
  0.3 s, so a season fetches in seconds. The agent already caches leaders per
  session, so a table would add schema and sync code to save almost nothing.
- **Locks live in the optimizer, not in a post-pass.** `optimize_lineup`
  takes `current_starters` and `locked`. That replaces the old page probing,
  and its keep-bonus replaces the old surname-per-slot-type comparison.
- **All 8 GraphQL documents were validated against Sleeper's schema** without
  a token. A deliberately broken control document returns a validation error.
  Ours return "Unauthorized" or a server 500 from the resolver, both after
  validation. No write was sent with a token.
- **`data/browser_profile/` was left on disk.** It holds a Chrome login
  session and is gitignored. Delete it by hand.
- **Review fixes.** Canceled games are skipped in the schedule (Sleeper
  lists one next to its replacement). `--live`/`--apply` work on the current
  week only. All lineup recommendations keep the lineup already set when it
  scores the same. A lineup write that one endpoint refuses is reported as
  FAILED for that endpoint, without hiding the other one's result.
- **`league_transactions_filtered` (pending claims) needs the token,** so
  `claims` and the agent's `get_pending_claims` say so when it's missing.

## What changed, and what didn't (checked 2026-10-08)

- **The documented API is unchanged.** `docs.sleeper.com` still describes the
  same read-only, keyless `v1` REST API: no auth, no writes, no news, stats or
  schedule. `api.sleeper.app/v2/...` returns 404.
- **What is new is that Sleeper's GraphQL API has been mapped.** It's the API
  the Sleeper web app itself uses (`https://sleeper.com/graphql`). Since September
  2026, several open-source Sleeper MCP servers have published working calls
  against it, including writes (set lineup, IR, add/drop, waiver claims,
  trades). Introspection is open: about 240 queries and 350 mutations.
  References: `MicahSapp/sleeper-mcp` (`API-NOTES.md`, `writes.py`) and
  `joscaz/sleeper-mcp` (`src/sleeper/graphql.ts`).
- **Undocumented endpoints we already rely on still work.** These are
  `/projections` and `/stats/nfl/<season>/<week>` (same stat keys as the
  projections), plus `/schedule/nfl/regular/<season>` (per-game `status`:
  `pre_game` / `complete`, date only, no kickoff time).

If "Sleeper issued an API" refers to a specific announcement, send the link:
this plan assumes the GraphQL API above, and would be revised for an official
one.

## Problems this fixes (verified)

1. **Roster refresh can be up to ~10 minutes stale.** REST is
   Cloudflare-cached: `/league/<id>/rosters` and `/transactions/<wk>` send
   `s-maxage=300, stale-while-revalidate=300`, `/matchups/<wk>` sends
   `s-maxage=60`. So `refresh_rosters` (from `roster-freshness.md`) can save
   the pre-move roster after a move in the app, and our change detection sees
   no change. GraphQL `league_rosters(league_id)` reads Sleeper's live store
   and needs **no token** (tested on our league).
2. **Execution scrapes the web UI.** `src/execution/browser.py` (349 lines)
   drives Chrome through Playwright, which brings bot-detection workarounds,
   CDP attach, a cookie-banner remover, CSS selectors, surname matching
   ("D Swift") and `wait_for_timeout` sleeps. The lineup ends up as one
   mutation with player ids.
3. **Only lineups can be enacted.** Add/drop, waiver claims and IR moves
   still have to be done by hand in the app.
4. **Game state comes from a third party and a clock.** `schedule.py`
   downloads nflverse's `games.csv` and compares kickoff times to now. That
   breaks on delays and flexed games, and needs a team-code map
   (`LA` -> `LAR`). Sleeper's schedule gives each game's status directly, in
   Sleeper's own team codes.
5. **Actual points go through nflverse and the crosswalk.**
   `decisions/leaders.py` loads nflverse parquet, maps ids through
   `id_crosswalk` (~98% coverage) and translates IDP columns
   (`idp_stats.py`). Sleeper's `/stats` returns the same keys as
   `/projections`, keyed by Sleeper id, IDP included, so `score_stats` scores
   it unchanged.
6. **Pending waiver claims are invisible.** `roster-freshness.md` notes that
   claims "stay pending until the waiver run and the API doesn't show them".
   GraphQL `league_transactions_filtered(status_filters: ["pending"])` does.
   It may need the token; to be checked.

## Approach

Six phases, each one a commit, in order. Writes come after reads, because
reads carry no account risk.

### 1. One GraphQL client

New file `src/ingestion/sleeper_graphql.py`:

- `gql(operation, query, variables, auth=False) -> dict`, POST to
  `https://sleeper.com/graphql`. It always sends a `User-Agent`, because
  Sleeper returns 403 without one. It sends `Authorization: Bearer <token>`
  only when `auth=True`, and raises `SleeperError` on a non-empty `errors`.
- Use GraphQL variables, never string interpolation. That removes the
  `_SAFE_ID` regex in `news.py`.
- `config.SLEEPER_TOKEN = os.environ.get("SLEEPER_TOKEN", "")`. Only writes
  and authenticated reads need it, so everything else keeps working without
  it.
- **Hard boundary:** this module has no way to call the betting and payment
  operations (`parlay`, `my_balances`, `my_payment_methods`, `tax_forms`,
  ...). Only named operations defined in our code are sent.
- Move `news.py` onto it (`get_player_news`). The behavior is the same.

### 2. Live rosters

`src/ingestion/sync.py`, `src/ingestion/sleeper.py`

- `SleeperClient.get_live_rosters(league_id)` calls GraphQL `league_rosters`,
  with the same fields `save_rosters` reads (`roster_id owner_id players
  starters reserve taxi settings metadata`).
- `refresh_rosters` and `_sync_one_league` use it. REST stays for league
  settings, users, players, drafts and past weeks, where a few minutes of
  cache doesn't matter.
- **Check before relying on `rosters.starters`:** MicahSapp reports that the
  scoring lineup lives in the matchup leg, not the roster
  (`roster_update_starters` "changes nothing that scores"). joscaz uses
  `roster_update_starters`. So test it: change a starter in the app, then
  compare `league_rosters.starters`, REST `matchups/<wk>.starters` and
  `matchup_legs` (auth). If they differ, `get_my_roster` / `sleeper_lineup`
  must read the matchup-leg starters. That would also be a root cause for the
  QB/SUPER_FLEX mix-up in `roster-freshness.md`.
- `ROSTER_REFRESH_SECONDS = 0` stays. One GraphQL call replaces the REST
  rosters call.

### 3. Sleeper schedule for game state

`src/ingestion/schedule.py`

- Read `/schedule/nfl/regular/<season>`, then filter by week. A team with no
  game is `BYE`. A game whose `status` is not `pre_game` counts as `PLAYED`
  (started or finished).
- `game_status` keeps its return values (`PLAYED` / `BYE` / `UPCOMING`), so
  `waivers.unavailable_this_week` and the agent's tools don't change. They
  pass a `{team: status}` map instead of `{team: kickoff}`.
- Remove `SCHEDULE_URL`, the Eastern-time conversion and `_TO_SLEEPER`.
- Lineup lock checks in phase 5 use the same map.
- Before shipping, capture a schedule response while a game is in progress
  and record the real in-game status value in the module docstring.

### 4. Sleeper stats for actual points

`src/decisions/leaders.py`, `src/ingestion/sleeper.py`

- `SleeperClient.get_week_stats(season, week)` calls
  `/stats/nfl/<season>/<week>?season_type=regular` with the same `position[]`
  list as `sleeper_src.QUERY_POSITIONS`.
- `season_to_date` adds up `score_stats(stats, scoring_settings)` per Sleeper
  id over weeks 1..N, counting a game when `gp > 0`. That means no crosswalk,
  no nflverse download and no IDP translation.
- Cache each completed week's stats in a new `player_week_stats` table
  (season, week, player_id, stats JSON). Past weeks are then pulled once, and
  later corrections are picked up by the full sync re-pulling the last two
  weeks.
- **Projections and backtest are out of scope here.** `nflverse_src` and
  `backtest.py` keep nflverse. Rebuilding the nflverse projection source on
  Sleeper stats would let us delete `id_crosswalk` and `idp_stats`. But it
  changes a blend input, so per PLAN.md it ships only if the backtest still
  beats Sleeper-only in both seasons. That's a follow-up plan, not part of
  this one.

### 5. Writes through GraphQL instead of the browser

New file `src/execution/sleeper_writes.py`. It replaces `browser.py`.

Every write:

- builds a `ProposedWrite` from player **ids** and shows names, then calls
  `require_approval` and `verify`. The approval gate stays exactly as it is,
  still with no `--yes` flag;
- requires `SLEEPER_TOKEN`, and fails with a clear message if it's missing;
- reads the result back through a **different** GraphQL query than the one
  that wrote (never REST, which is cached), and reports `VERIFIED` /
  `MISMATCH` / `SENT, could not verify`. A read-back that fails doesn't mean
  the write failed;
- resolves names against a known pool only (my roster, or unrostered players
  with an NFL team), never the full catalog. That reuses
  `storage.resolve_player`.

| Action | Mutation | CLI |
|---|---|---|
| Set lineup | `update_matchup_leg(round, leg, league_id, roster_id, starters)` (or `roster_update_starters`, whichever phase 2's check proves scores) | `lineup --apply` |
| IR slots | `roster_update_reserve(league_id, roster_id, reserve)` (full list, not a delta) | `ir --set "a,b"` |
| Free-agent add/drop | `league_create_transaction(type: "free_agent", k_adds, v_adds, k_drops, v_drops)` | `add "X" --drop "Y"` |
| Waiver claim | `submit_waiver_claim(k_adds, v_adds, k_drops, v_drops, k_settings: ["waiver_bid"], v_settings: [bid])` | `claim "X" --drop "Y" [--bid N]` |
| Cancel claim | `cancel_waiver_claim(league_id, transaction_id, leg)` | `claim --cancel <id>` |
| Pending claims (read) | `league_transactions_filtered(status_filters: ["pending"], roster_id_filters: [me])` | `claims` |

- `lineup --apply` keeps today's flow (current vs recommended, changes,
  locked-out, approval). "Current" now comes from phase 2's live starters,
  matched by id instead of surname. "Locked" comes from phase 3's game
  status. The write sends the **full** starters list in
  `roster_positions` order, with players already locked left where they are.
- `--bid` is accepted only when the league's `waiver_type` is FAAB (2), and
  is checked against the remaining budget (`waiver_budget_used`).
- `claim` and `add` warn when a drop leaves a starting slot empty.
- **Trades are not included.** `propose_trade` / `accept_trade` reach another
  manager, and with `trade_review_days = 0` an accepted trade executes
  immediately. If we want them later, that's a separate plan.
- **Agent:** read-only additions only. `get_pending_claims` is added, and the
  `get_waiver_targets` / `recommend_lineup` output ends with the exact CLI
  command to enact it. The agent still has no write path (PLAN.md §2: it
  recommends, I approve).

Remove: `src/execution/browser.py`, `cmd_login` and the `login` subcommand,
`tests/test_browser_locks.py`, the `playwright` dependency, and
`data/browser_profile/`. Update `.env.example` with `SLEEPER_TOKEN` and
how to get it: sleeper.com, DevTools, Local Storage, key `token`. It lasts
about a year and gives full account access, so treat it like a password.

### 6. Docs

- `plan/PLAN.md` §2: replace "Sleeper has NO write API" with "no
  *documented* write API; writes go through the undocumented GraphQL API
  behind the approval gate". Update Stage 5's text to match.
- `README.md`: replace `login` with `SLEEPER_TOKEN`, and add `ir`, `add`,
  `claim`, `claims`.
- `PROJECT_LOG.md`: one entry per phase.

## Tests

- `tests/test_sleeper_graphql.py`: the User-Agent is always sent; the token is
  sent only with `auth=True`; `errors` raises; variables are sent, not
  interpolated.
- `tests/test_sync.py`: `refresh_rosters` reads live rosters (stub client),
  and change detection works the same.
- `tests/test_schedule.py`: rewritten for the status map (bye, `pre_game`,
  `complete`, and the in-game value from phase 3).
- `tests/test_leaders.py`: totals and games from stubbed Sleeper stats under
  league scoring, IDP included.
- `tests/test_sleeper_writes.py`, with a stubbed `gql`:
  - no write without an `Approval`, and a changed payload fails `verify`;
  - lineup sends the full starters list in slot order and keeps locked
    players in place;
  - waiver claim builds the paired `k_`/`v_` arrays, and a bid is refused
    outside FAAB;
  - a missing token fails before any request;
  - a failing read-back is reported as unverified, not failed.
- `tests/test_approval.py` is unchanged and must still pass.

## Live check

Add to `TEST_QUESTIONS.md`. A run passes only if the result matches the
Sleeper app.

1. Swap two starters in the app, then run `roster` within 10 seconds. It
   shows the swap. Before this change it could take up to ~10 minutes.
2. `lineup --apply` on a one-player bench swap, then `APPROVE`. The CLI
   reports `VERIFIED`, and the app shows the change.
3. `ir --set` with an IR-eligible player, then `VERIFIED`.
4. `claim` a free agent with a $0 bid, then `claims` lists it, then
   `claim --cancel <id>`, and `claims` is empty again.
5. During a live game window: `waivers` leaves out players whose game has
   started, and `lineup --apply` lists them as locked.

## Trade-offs and risks

- **Undocumented API.** Sleeper can change or close GraphQL without notice.
  Everything goes through `sleeper_graphql.py`, so a break means fixing one
  file, and a failed read falls back to REST as it does today. Sleeper's
  terms of use haven't been reviewed for this. Read them before phase 5.
- **The token is full account access.** It's kept in `.env` (gitignored),
  never logged, and sent only to `sleeper.com`. The betting and payment
  operations stay out of reach by construction (phase 1).
- **Two sources disagree on the lineup mutation.** Phase 2's check settles it
  before phase 5 is written. Writing with the wrong mutation would report
  success while nothing scores, which is why the read-back is required.
- **Losing the browser fallback.** If GraphQL writes break mid-season,
  nothing can be enacted until it's fixed. Recommendations, digest and agent
  are unaffected. Keeping 349 lines of scraping as a fallback isn't worth it,
  and git history keeps it.
