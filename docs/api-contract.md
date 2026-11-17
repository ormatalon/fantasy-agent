# API contract (v1)

HTTP API of the local FastAPI server, used by the Next.js page (`design/`).
Only what v1 needs. Terminology (`AGENT.md`): **fantasy team** is a manager's
roster in a Sleeper league; **NFL team** is a real franchise.

Shapes named `PlayerRef`, `GameRef`, `LeagueFormat` are defined in
`docs/data-contract.md` (sections 9 and 8) and reused as is. Data freshness
rules: section 5 there.

## Conventions

- Base path `/api`, JSON, UTF-8. Times are ISO 8601 UTC; the page localizes.
  The server binds to `127.0.0.1` only and rejects a `Host` header that is not
  `localhost` or `127.0.0.1`. No CORS (same origin, the page is served at `/`).
- **The OpenRouter key is never in any response, event, log or trace.** The API
  only says whether one is set (`key_set`).
- Errors outside a chat stream: HTTP status plus
  `{"error": {"code": "...", "message": "...", ...extra}}`. `message` is the
  user-facing sentence (copy in `design/README.md`); the page keys its
  states on `code`. The same object is the payload of a chat stream `error`
  event.
- One league is "current" (stored in settings). Endpoints that need it return
  `409 no_league` when none is chosen.

## Error codes

| Code | HTTP | Where | Extra fields | Meaning |
|---|---|---|---|---|
| `username_not_found` | 404 | league discovery | | no Sleeper user with that name |
| `sleeper_unreachable` | 502 | league discovery, settings | | Sleeper could not be reached (setup) |
| `key_rejected` | 422 | `PUT /settings` | | OpenRouter refused the key at save time; **nothing was saved** |
| `model_unknown` | 422 | `PUT /settings` | | id not in `/models` |
| `league_not_found` | 404 | `PUT /league` | `available: [names]` | unknown league name; lists the real ones |
| `league_unsupported` | 409 | `PUT /league`, chat | `unsupported_reason`; `question_kept: true` from `POST /chat` | dynasty, keeper or best_ball, named |
| `league_not_playable` | 409 | `PUT /league` | `reason`: `pre_draft`, `drafting` or `complete` | league status has no current week to play |
| `no_league` | 409 | panel, chat, history | `question_kept: true` from `POST /chat` | no league chosen yet |
| `key_missing` | 409 | chat | `question_kept: true` | no OpenRouter key stored |
| `key_invalid` | 401 | chat | `question_kept: true` | OpenRouter returned 401 for the stored key (it uses 401 for missing and invalid alike; the server tells them apart because it knows whether a key is stored) |
| `quota_exceeded` | 429 | chat | `remaining`, `limit`, `model`, `resets_at` (always null in v1: `GET /api/v1/key` returns only `used`, `limit`, `remaining`, spikes Model; the quota message must not promise a reset time), `question_kept: true` | daily free-model limit (OpenRouter `GET /api/v1/key` `free_model_daily_requests`, which costs no request) or a 429 mid-question. The `/key` pre-check applies only when the chosen model id ends in `:free` |
| `provider_error` | 502 | chat | `question_kept: true` | model provider failed after 2 retries (network, 5xx, HTTP 200 with an `error` body) |
| `too_many_turns` | 502 | chat | `max_model_turns: 6`, `question_kept: true` | the 6th model turn, sent with no tools, still returned no answer text |
| `question_in_progress` | 409 | chat | | one question at a time |
| `internal_error` | 500 | any | | bug; nothing was changed in Sleeper (v1 never writes) |

`refresh_failed` is **not** an HTTP error: the request succeeds on stored data
and carries a `refresh` warning object (below), so the answer is still
delivered.

Guillotine leagues cannot be detected: Sleeper exposes no setting for them
(spikes, O2). Such a league is listed as supported until a key is found; this
is a stated gap, not a promise.

## Refresh object

Returned in `/state`, `/panel`, `/refresh` and in the chat `answer` event.

```json
{ "stored_at": "2026-11-03T12:00:00Z",
  "failed": [ { "code": "refresh_failed", "source": "sleeper_graphql",
                "message": "Could not refresh from Sleeper.",
                "since": "2026-11-03T14:29:00Z" } ] }
```

`stored_at` is when the oldest piece the page shows (roster, matchup, game
states) was fetched. `failed` is empty when all is well.

## Setup and settings

### `GET /api/state`

First call of the page. Everything the setup screen and top bar need.

```json
{
  "username": "OrMatalon",
  "key_set": true,
  "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
  "setup_complete": true,
  "needs_league_choice": false,
  "leagues": [ LeagueInfo, ... ],
  "current_league": { "league_id": "...", "name": "Or Test League", "season": 2026,
                      "this_week": 9, "last_completed_week": 8, "format": LeagueFormat },
  "refresh": Refresh
}
```

`setup_complete` = username, current league and key all set. Before that the
page shows the setup screen. Settings is the same screen with these values.

### `PUT /api/settings`

Partial update; takes effect at once, no restart.

```json
{ "sleeper_username": "OrMatalon", "openrouter_api_key": "sk-or-...", "model": "...",
  "openrouter_base_url": "https://openrouter.ai/api/v1" }
```

- `openrouter_base_url` is optional and not shown in the setup screen. It
  defaults to `https://openrouter.ai/api/v1`; when unset, the env var
  `OPENROUTER_BASE_URL` is used. Every OpenRouter call (key check, `/key`
  quota pre-check, chat) uses it, so tests and proxies can point it at a stub
  that returns a 401 or a quota error. It is never echoed.
- A new key is checked against `{base_url}/key` (OpenRouter `GET /api/v1/key`) before it is
  stored. Result is distinct: `200` with `"key": "saved"`, or `422 key_rejected`
  (nothing stored, previous key kept). OpenRouter unreachable: `502
  provider_error`, nothing stored. Omitted key: `"key": "unchanged"`.
- A new username triggers league discovery; if exactly one supported,
  playable league exists it becomes current.
- Response: the `GET /state` body plus `"key": "saved" | "unchanged"`. The key
  value is never echoed.

### `DELETE /api/settings/key`

Clears the stored OpenRouter key (Settings "Remove key", and a clean state for
tests). `200` with the `GET /state` body (`key_set: false`); chat then returns
`key_missing`. Idempotent.

### `GET /api/models`

```json
{ "models": [ { "id": "nvidia/nemotron-3-ultra-550b-a55b:free", "name": "Nemotron 3 Ultra",
                "free": true, "recommended": true,
                "note": "Free, reliable with tools, about 50 questions a day." } ] }
```

Static list in the backend (default pinned per `docs/spikes.md`, Model).

### `GET /api/leagues?username=OrMatalon`

Finds the user's leagues for the season (`username` optional: defaults to the
saved one). Used by the setup screen before saving. Errors: `username_not_found`,
`sleeper_unreachable`.

```json
{ "username": "OrMatalon", "season": 2026,
  "leagues": [ { "league_id": "1412371724632166400", "name": "Or Test League", "season": 2026,
                 "status": "in_season", "num_fantasy_teams": 4,
                 "defense": "idp", "reception_points": 1.0,
                 "supported": true, "unsupported_reason": null,
                 "selectable": true, "not_selectable_reason": null } ] }
```

This item shape is `LeagueInfo`; `/state` `leagues` uses the same shape.

- `defense` is `idp | team_def | none`. `unsupported_reason` is `dynasty |
  keeper | best_ball | null`, named so the page can say it.
- `status` is Sleeper's league status and is one of `pre_draft`, `drafting`,
  `in_season`, `complete`. Only `in_season` is selectable in v1 (the other
  three have no current week to play).
- `selectable` = `supported` and `status == "in_season"`. When false,
  `not_selectable_reason` is one of `dynasty`, `keeper`, `best_ball`
  (unsupported types, named), `pre_draft`, `drafting`, `complete`; the page
  lists the league disabled with that reason and hard-codes none of this.
  `PUT /league` on a non-selectable league returns `409 league_unsupported`
  (type reasons) or `409 league_not_playable` (status reasons, `reason` field).

### `PUT /api/league`

Switch the current league, by id or by name (the chat tool `switch_league`
calls the same function).

```json
{ "league_id": "..." }      or      { "league_name": "FantaSteelers" }
```

`200` with the `GET /state` body. Errors: `league_not_found` (lists real
names), `league_unsupported`. Several leagues and none chosen yet:
`needs_league_choice: true` in `/state`, never a guess. The choice is
remembered. History is per league: switching changes which history
`/chat/history` returns.

## My fantasy team panel

### `GET /api/panel`

For the current league, from stored data (the page polls it; the server keeps
it fresh per section 5 while the app is open).

```json
{
  "league_id": "...", "season": 2026, "week": 9,
  "fantasy_team": { "name": "OrMatalon", "record": "5-3", "rank": 2 },
  "slots": [
    { "slot": "QB", "role": "starter", "player": PlayerRef | null, "points": 0.0 },
    { "slot": "SUPER_FLEX", "role": "starter", "player": null, "points": null },
    { "slot": "BN", "role": "bench", "player": PlayerRef, "points": 0.0 },
    { "slot": "IR", "role": "ir", "player": PlayerRef, "points": null }
  ],
  "matchup": { "opponent_fantasy_team": "Ravid's RAMS", "my_score": 112.4, "opponent_score": 98.0,
               "state": "not_started" | "live" | "final" } | null,
  "as_of": { "rosters": "...", "matchup": "...", "games": "...", "injuries": "..." },
  "refresh": Refresh
}
```

- `slots` follow Sleeper's order, repeats kept (`slot` is Sleeper's label,
  `BN` for bench, `IR` and `TAXI` for those). An empty starting slot is
  `player: null`.
- Per player the page reads from `PlayerRef`: `name`, `position`, `nfl_team`,
  `injury_status` (the injury designation for the flag chip; `null` = healthy),
  `game` (`GameRef`: `status` includes `bye`, `kickoff` UTC, `opponent_nfl_team`).
  `points` is the player's points so far this week in league scoring
  (`null` when the game has not started or for IR).
- `injury_status` values, in Sleeper's vocabulary: `Questionable`, `Doubtful`,
  `Out`, `IR`, `PUP`, `Sus`, `NA`, `DNR`, or `null`. Seen in the Sleeper
  catalog on 2026-11-03: `IR` 163, `Out` 137, `Questionable` 42, `PUP` 13,
  `Sus` 2, `NA` 1, `DNR` 1, `Doubtful` 1. ESPN's own words are normalized
  before they reach the page (`Injured Reserve` -> `IR`, `Active` -> `null`;
  ESPN seen: `Active`, `Questionable`, `Injured Reserve`, `Out`, `Doubtful`).
  The design has chips for Q, D, OUT, IR and BYE; `PUP`, `Sus`, `NA` and `DNR`
  need a chip or text label too (they are rare, shown as the value itself).
  An unseen value is passed through as is.
- `matchup` is `null` when my fantasy team has no opponent that week. Sleeper
  confirms this in fantasy playoff weeks: in the 2025 FantaSteelers league
  (`playoff_week_start` 15, 6 playoff teams of 8) the week 15 matchups have
  `matchup_id: null` for 2 fantasy teams, weeks 16 and 17 for 2 and 4, and
  week 18 for all 8 (fixture
  `src/ingestion/tests/fixtures/sleeper/matchups_playoff_week15_2025_fantasteelers.json`).
  A `null` `matchup_id` covers a first-round bye and an eliminated fantasy team
  alike; the API cannot tell which, so the page says "No matchup this week".
  Regular-season weeks always have one.
- `matchup.state` is `not_started` until any starter's game kicks off, `live`
  while any of the two lineups still has a game in progress, `final` after.
- If the stored data is old because a refresh failed, `refresh.failed` says so
  and `refresh.stored_at` is the time to show ("data stored on ...").
- Errors: `no_league`. If nothing is stored at all and Sleeper cannot be
  reached: `502 sleeper_unreachable` (the page's "error with nothing stored").

### `POST /api/refresh`

Manual refresh button: re-pulls Sleeper (rosters, matchup, league) and ESPN
(games, injuries) now. Returns the `GET /panel` body. A failure is not an HTTP
error: the body carries the stored data and `refresh.failed`.

## Chat

History is per league. `league_id` defaults to the current league.

### `GET /api/chat/history`

```json
{ "league_id": "...",
  "running": null | { "question_id": 41, "started_at": "..." },
  "messages": [
    { "id": 40, "role": "user", "text": "Who should I pick up?", "created_at": "...",
      "status": "ok" },
    { "id": 41, "role": "assistant", "text": "...", "created_at": "...", "status": "ok",
      "sources": [ { "label": "Sleeper rosters", "as_of": "..." },
                   { "label": "official NFL injury report via nflverse", "as_of": "..." } ],
      "refresh": Refresh },
    { "id": 42, "role": "user", "text": "Trade Goff?", "created_at": "...",
      "status": "failed", "error": { "code": "quota_exceeded", "message": "...", "remaining": 0, "limit": 50, "resets_at": null, "question_kept": true } }
  ] }
```

- Only user and assistant messages are returned (tool messages stay
  server-side). `sources` is built in code from the tool results' structured `source_label`
  and `as_of` (one entry per label, oldest `as_of` kept), the same strings as
  the AS OF lines; never parsed from text. It feeds the small gray line under
  the answer.
- A `failed` question stays in the thread; the page shows it as a dashed card,
  puts its text back in the input and offers "Ask again".

`GET /api/chat/history?detail=trace` (for the live-check runner and the
grounding check; local only like everything here) returns every stored row of
the league in id order, tool rows included, in the shape of
`chat_message` (`docs/data-contract.md` section 1, "chat_message.meta"):

```json
{ "league_id": "...",
  "rows": [ { "id": 40, "role": "user", "content": "...", "created_at": "...", "meta": { "status": "ok" } },
            { "id": 41, "role": "assistant", "content": "", "created_at": "...",
              "meta": { "question_id": 40, "model": "...", "turn": 1, "context": "TODAY: ...",
                        "tool_calls": [ { "id": "call_1", "name": "get_player",
                                          "arguments": { "name": "DK Metcalf" }, "result_id": 42 } ] } },
            { "id": 42, "role": "tool", "content": "SUMMARY: ...", "created_at": "...",
              "meta": { "tool_call_id": "call_1", "name": "get_player", "arguments": { "name": "DK Metcalf" }, "ok": true } },
            { "id": 43, "role": "assistant", "content": "He is on PIT ...", "created_at": "...",
              "meta": { "question_id": 40, "turn": 2, "context": "TODAY: ...", "tool_calls": [],
                        "sources": [ ... ], "refresh": Refresh, "model_turns": 2 } } ] }
```

It carries the tool arguments, the rendered tool results and the context block
of each turn, so a check can compare every name in an answer with what the
model was given. It never contains the OpenRouter key (nothing stores it in a
row) or reasoning text. Filters: `?question_id=40` returns that question's
rows only. Built in stage 2.5 with the chat endpoints.

### `POST /api/chat`

```json
{ "text": "Who should I pick up this week?", "retry_of": 42 }
```

`retry_of` (optional) is a `failed` message id; it is removed once the new
question is accepted. Checks before the stream opens, as normal HTTP errors:
`no_league`, `league_unsupported`, `key_missing`, `question_in_progress`, and
`quota_exceeded` (from `GET /api/v1/key`, no model request spent). The
question is stored (status `failed` with the error) so it is not lost.

Otherwise `200 text/event-stream` (the page reads it with `fetch` and a stream
reader, since `EventSource` cannot POST). Events, each `event: <name>` plus a
JSON `data:` line; a `: keepalive` comment every 15 s:

| Event | Payload | When |
|---|---|---|
| `start` | `{ "question_id": 41, "started_at": "...", "max_model_turns": 6 }` | accepted |
| `step` | `{ "id": 3, "kind": "refresh" \| "model" \| "tool", "label": "Reading your roster", "tool": "get_my_roster" \| null, "model_turn": 2 \| null, "status": "running" \| "done" \| "error", "elapsed_ms": 4200 }` | each pre-question refresh, each model turn, each tool call, once when it starts (`running`) and once when it ends (`done`, same `id`) |
| `answer` | `{ "message_id": 43, "text": "...", "sources": [...], "refresh": Refresh, "model": "...", "model_turns": 3 }` | terminal, success. `refresh.failed` may be non-empty: the answer was still given on stored data |
| `error` | the error object (code table above) | terminal, failure; the question is kept (`question_kept: true`) |

- `label` is a short plain phrase from the label table in `docs/data-contract.md`
  section 11 (the refresh step is "Refreshing data"); `elapsed_ms` is since `started_at`
  so the page's timer can resume after a reload. No percentages: the number of
  steps is not known in advance.
- Errors that happen mid-question (`quota_exceeded` on a 429, `key_invalid`,
  `provider_error`, `too_many_turns`) arrive as the `error` event with the
  same object as the HTTP errors above.
- A closed connection does not cancel the question: it finishes and is stored;
  `GET /chat/history` then shows the answer (`running` tells the page one is in
  progress).

### `DELETE /api/chat/history`

Start fresh: deletes the stored messages of one league (`?league_id=`, default
current). `204`. Other leagues keep theirs.

## Health

`GET /api/health` -> `{ "ok": true, "version": "..." }`, for the start scripts
and the CI smoke test.

## Out of v1

Nothing writes to Sleeper (no confirm, token or move endpoints), no
pending claims or trade offers, no preferences, no trace endpoint (LangSmith is
env-controlled), no auth (port is localhost only).
