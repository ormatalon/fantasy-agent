# Plan: the agent must always answer from my actual roster

Status: implemented. Branch: `sync-on-time`.

## Problem

From `COMMENTS_ignored.md`:

1. I apply a recommended move (e.g. a waiver add/drop) in Sleeper, then ask
   about my roster, and the agent answers with the old roster, even when I
   ask it specifically to check my roster.
2. "Who are my starters?" should list the starters I set in Sleeper, with
   projections, using only `get_my_roster`, not `recommend_lineup`.
3. "Who are my starters?" put Drake Maye at QB and Josh Allen at FLEX. In
   Sleeper, Allen is at QB.

## Causes (verified in code)

- **Rosters are only re-pulled with a full sync.** `sync_if_stale` runs a
  full sync only when `last_synced_at` is more than `AUTO_SYNC_HOURS` (2h)
  old. `last_synced_at` is written in one place, the end of `run_sync`
  (`src/ingestion/sync.py`). A move made a minute ago is invisible until
  then.
- **Freshness depends on the model's choices.** The roster reaches the
  model only if it decides to call `get_my_roster`. In a `chat`, earlier
  `get_my_roster` / `recommend_lineup` results are still in the history, and
  the free model often answers from them instead of calling the tool again.
  Its own earlier answers ("your starters are ...") are in the history too.
  An earlier attempt (a "roster changed" note prepended to the question plus a
  prompt rule) was reverted: both still relied on the model choosing to comply.
- **No tool exposes the lineup set in Sleeper.** Sync already stores
  Sleeper's `starters` for each roster (`storage.save_rosters`), and the CLI
  `roster` command prints it, but `get_my_roster` reads only `players`. So
  the model guesses starters or calls `recommend_lineup` (cause of item 3).
- **IR slots are not stored.** Sleeper returns players in IR slots as a
  separate `reserve` list, which `save_rosters` drops. An IR-slotted player
  currently looks like a bench player, and `recommend_lineup` may start him.

## Approach

Make the correct roster something the model *receives*, not something it
has to decide to fetch; tell it explicitly what changed; and hide the
outdated copies it could repeat.

### 1. Roster refresh before every question

`src/ingestion/sync.py`, `config.py`

- New `config.ROSTER_REFRESH_SECONDS = 0` (refresh before every question).
  Kept as a setting so a minimum interval can be added later. The cost is 3
  small Sleeper requests per question (not per model turn), far below
  Sleeper's ~1000/min limit.
- New `refresh_rosters(conn, max_age_seconds=None, log=log_to_stderr) -> bool`:
  - skip if `rosters_synced_at` is younger than `ROSTER_REFRESH_SECONDS`;
  - otherwise fetch `get_rosters(league_id)` plus `get_matchups` and
    `get_transactions` for `current_week` (3 requests), save them, and set
    `rosters_synced_at`;
  - read the league from `active_league_id` in state (not from the agent
    context), so it follows a `switch_league` made during the chat;
  - never write `last_synced_at`: that key marks a FULL sync only, so the 2h
    clock for injuries, news and past weeks is unaffected;
  - never raise: on failure, log to stderr and keep the stored data (same
    contract as `sync_if_stale`).
- `run_sync` also sets `rosters_synced_at`, so the first question after a full
  sync doesn't pull rosters again.
- New `refresh_before_question(conn)`: `sync_if_stale`; if no full sync ran
  and the data isn't still stale (i.e. a full sync didn't just fail),
  `refresh_rosters`. Returns whether a full sync ran.
- `src/agent/orchestrator.py`: `ask()` and each turn of the `chat()` loop call
  `refresh_before_question` instead of `sync_if_stale`. Call
  `ctx.reload_state()` only after a full sync: the projection caches are
  league-wide, not per roster, so a roster refresh leaves them valid.

### 2. Detect and record roster changes

`src/ingestion/sync.py`

Both `refresh_rosters` and `run_sync` save rosters through one helper that
compares my roster (players, starters, IR) before and after saving. If it
changed, it stores, per league:

- `roster_changed_at:<league_id>`: when the change was detected;
- `roster_change:<league_id>`: a short description, e.g.
  `added Chuba Hubbard; dropped Zach Charbonnet` or `lineup changed`.

The first save of a roster (nothing to compare against) is not a change.

### 3. Store Sleeper's IR slots

`src/ingestion/storage.py`

- Add `("rosters", "reserve", "TEXT")` to `_ADDED_COLUMNS` so `_migrate` adds
  the column to existing databases. Add it to the `rosters` schema too.
- `save_rosters` stores `json.dumps(r.get("reserve") or [])`.
- Taxi squads are out of scope: a league with one would show taxi players
  as bench.

### 4. A roster view that matches Sleeper

A pure function `sleeper_lineup(players, starters, reserve, roster_positions)`
in `src/decisions/lineup.py` (no projections, no DB), plus one formatter,
`format_lineup` in `src/agent/tools.py`, used by both the tool and the
prompt, so the two can never disagree:

- Starters: zip Sleeper's `starters` list with the league's
  `roster_positions` minus `BN`/`IR`/`TAXI` (same order). `"0"` means an
  empty slot; show it as "(empty)".
- Bench: `players` minus starters minus reserve.
- IR: `reserve`.
- Each player has position/team and injury tag; the tool adds this week's
  projection, the prompt does not (see 5).

`get_my_roster` returns this view, grouped by Starters (with slot) / Bench /
IR, keeping the IR-eligibility hints for starters and bench players. Its docstring says to
use it for "who are my starters" / "what's my lineup", and that
`recommend_lineup` is only for what the lineup *should* be.

`recommend_lineup` no longer considers IR-slotted players for starting slots
(Sleeper won't start a player in an IR slot). If an IR-slotted player has no
IR-eligible designation any more, it says he can be moved out of IR.

### 5. My roster in the system prompt

`src/agent/prompts.py`, `src/agent/orchestrator.py`

The system prompt is rebuilt on every model turn (`graph.py`, `plan`
node), so it always reads the latest stored roster.

- `build_system_prompt` takes the formatted roster lines from step 4 (no
  projections: computing them on every turn would force the projection build
  even for unrelated questions, and numbers must come from tool calls anyway),
  the `rosters_synced_at` time, and the last recorded change from step 2.
- New section:
  `MY ROSTER right now, from Sleeper (refreshed YYYY-MM-DD HH:MM UTC). Authoritative: it overrides any roster, lineup or availability mentioned earlier in this conversation, including your own earlier answers.`
  followed by `Last change detected YYYY-MM-DD HH:MM UTC: added X; dropped Y`
  when there is one. A concrete "Y was dropped" is much harder for the model
  to ignore than a general override rule.
- A rule: "who are my starters" means the lineup set in Sleeper (MY ROSTER /
  `get_my_roster`); `recommend_lineup` is only for what it should be.
- `system_prompt_for(ctx)` passes it in.

### 6. Hide roster-dependent results produced before a roster change

`src/agent/graph.py`, `src/agent/orchestrator.py`

- Each question's `HumanMessage` carries `additional_kwargs["asked_at"]`
  (an ISO time; `langchain_openai` does not send it to the model).
- In `plan`, before sending messages to the model: for every earlier question
  asked **before** the latest `roster_changed_at` of the active league, replace
  the content of its `ToolMessage`s from roster-dependent tools
  (`get_my_roster`, `get_team_roster`, `recommend_lineup`, `get_waiver_targets`,
  `get_injured_stash_candidates`, `get_trending_players`) with
  `[outdated: my roster changed after this result - see MY ROSTER in the system prompt]`.
- If the roster hasn't changed, nothing is hidden, so follow-ups like "why did
  you start X over Y?" keep the numbers they need.
- The current question's results are never hidden. Messages without
  `asked_at` (saved before this change) count as older than any change.
- The tool calls themselves stay, so every tool result still follows the
  call that produced it (same requirement `recent_messages` already respects).
- This is applied to what's sent, not to the stored checkpoint.
- The agent's own earlier answers are not rewritten; step 5's change line is
  what counters them.

## Tests

- `tests/test_sync.py`
  - `refresh_rosters` saves new rosters, makes exactly 3 requests, sets
    `rosters_synced_at`, and leaves `last_synced_at` unchanged.
  - Skipped within `ROSTER_REFRESH_SECONDS`; runs after it (and always at 0).
  - A failing client is logged, not raised; stored roster unchanged.
  - A roster change is recorded with added/dropped names; an unchanged roster
    and a first save are not.
  - `refresh_before_question` does a full sync instead when stale, and skips
    the roster refresh when that full sync fails.
- `tests/test_storage.py`: `reserve` is saved, and `_migrate` adds the
  column to a database created without it.
- `tests/test_lineup.py`: `sleeper_lineup` maps starters to slots in order,
  shows `"0"` as empty, separates bench and IR.
- `tests/test_agent.py`
  - `get_my_roster` shows Allen at QB and Maye at SUPER_FLEX when Sleeper has
    them that way, and lists IR separately from bench.
  - `recommend_lineup` never starts an IR-slotted player.
  - The system prompt contains the MY ROSTER section with the current
    starters and the last change, and changes when the stored roster changes.
  - The graph hides earlier roster results only when the roster changed after
    them, never the current question's (stub-LLM pattern from
    `test_graph_rebuilds_the_system_prompt_on_every_model_turn`).
- `tests/test_prompts.py`: the roster section renders, and is absent when no
  roster is given.

## Live check

Added to `TEST_QUESTIONS.md` as questions 15-17. A run passes only if the answer matches
the Sleeper app.

1. "Who are my starters?": lists the Sleeper lineup by slot, uses only
   `get_my_roster` (or the prompt), not `recommend_lineup`. QB/SUPER_FLEX
   match the app.
2. In one `chat`: get a waiver recommendation, make a **free-agent** add/drop
   in Sleeper (not a waiver claim: claims stay pending until the waiver run and
   the API doesn't show them), ask "who's on my roster?". The answer reflects
   the move.
3. Simulated version of 2 (no real move needed): remove a player from the
   local copy of my roster, ask about that player. The refresh restores him
   and the answer includes him.

## Trade-offs and risks

- **Requests per question.** 3 extra Sleeper requests before each question.
  Raise `ROSTER_REFRESH_SECONDS` if that ever matters.
- **Prompt size.** About 25 roster lines are added to every model turn. That's
  small next to the 40-message history, but check that free-tier requests
  don't hit context limits.
- **Model quality.** Steps 5 and 6 are meant to make roster answers correct
  even with the free model. If wrong answers still slip through, the
  remaining lever is a stronger `OPENROUTER_MODEL`.
- **Pending waiver claims** are invisible to the agent until processed.

## Out of scope

- The other league (`Or Test League`) is only refreshed after `switch_league`,
  as today.
- No change to the full-sync interval (`AUTO_SYNC_HOURS`) or the players
  catalog TTL.
- Taxi squads.
- News for a player added between full syncs arrives with the next full sync.
