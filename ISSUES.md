# Issues

Tracker for reported bugs and requests, reviewed against the code on branch
`apply-issues-fix`. Status tags:

- **[FIXED]** — changed in code, covered by tests, and checked live where possible.
- **[VERIFY]** — the code handles it; confirm with the reporter, then close.
- **[OPEN]** — not done; the reason is given.

Tests: 145 passing (up from 93). The live runs are recorded in `TEST_QUESTIONS.md`.

## Summary

| # | Item | Status |
|---|---|---|
| Bug 1 | Missing Gmail may break | VERIFY |
| Bug 2 | Only one league | FIXED |
| Bug 3 | Agent thought it was 2024 | FIXED |
| Bug 4 | Prompt hardcodes IDP | FIXED |
| Bug 5 | Thin tool docstrings | FIXED |
| Bug 6 | `graph.py` | FIXED |
| Issue 1 | Waivers only look one week ahead | FIXED |
| Issue 2 | Waivers ignore already-played / bye | FIXED |
| Issue 3 | Compound tasks (injured stash) | FIXED (tooling); model quality still a factor |
| Issue 4 | No opponent recap | FIXED |
| Issue 5 | Injury / IR status invisible | FIXED |
| Issue 6 | News outside the roster | FIXED |
| Issue 7 | Chat doesn't auto-sync | FIXED |
| New A | Name lookup picks wrong player | FIXED |
| New B | Season value is full-season | FIXED |
| New C | Draft ignores roster need | FIXED |
| New D | Matchup adjustment off | Not a bug |
| New E | Injured players zero out replacement level | FIXED |
| New F | Crosswalk matched ~15% of players | FIXED |
| New G | Agent had no actual season-to-date points | FIXED |
| New H | Daily model quota crashed with a traceback | FIXED |
| Upgrade 1 | Long-term memory | FIXED |
| Additions 1-4 | Beat writers, Reddit, FantasyPros, The Athletic | OPEN (external access) |

---

## Bugs

### 1. If GMAIL doesn't exist it may break — [VERIFY]
`notify.py:_credentials` raises a clear `NotifyError` for a missing or malformed
`GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`, and it is only reached from
`digest --email`. `sync`, `ask`, `chat` and `lineup` never touch Gmail.
Covered by `tests/test_digest.py`. If it happens again, capture the exact
command and traceback.

### 2. The agent can't handle more than a single league — [FIXED]
Data was always stored per league. What was single was the "active league"
pointer, and each `sync` re-prompted.
- `sync --league "name"` (no prompt), `sync --all`, `leagues` (lists them, shows
  which is active and synced) and `use "name"` (switch, syncing first). Shared
  code: `src/ingestion/sync.py`.
- Agent: the prompt lists your other leagues. The new `switch_league` tool
  syncs and switches mid-conversation, and asks if the name is unclear.
- Found along the way: projections read scoring from `leagues LIMIT 1`, i.e.
  whichever league came first. They now use the active league (`engine._scoring_settings`).
- A league name that doesn't exist fails even when you have only one league,
  instead of silently landing on it.

### 3. Assaf's agent decided it's 2024 and couldn't retrieve 2026 — [FIXED]
The cause was the model, not config: Assaf's `.env` had 2026, and no code path
produces 2024. The system prompt never stated the date, so the free model fell
back on its training data.
- The prompt is now built per turn (`prompts.build_system_prompt`) with today's
  date, the season, the week, the league and the team, plus hard rules: never
  claim a different year, never call the current season unavailable, and
  report tool failures as failures.
- Live: "Show me the top 2026 running backs so far" now answers with 2026
  data (test question 1b).
- Separately, `.env.example` no longer ships `SLEEPER_SEASON=2025`. Empty
  means auto-detect.

### 4. The prompt says it's an IDP league — [FIXED]
Format is derived from the league's own slots and scoring: PPR / half /
standard, TE premium, superflex, IDP only if `DL`/`LB`/`DB`/`IDP_FLEX` slots
exist, team DEF, no-kicker. See `prompts.describe_format` and `tests/test_prompts.py`.

### 5. Tool descriptions are too thin — [FIXED]
Every tool docstring now says what it covers and what it doesn't, and points
to the right alternative. For example: `get_week_results` defaults to the last
completed week, `get_waiver_targets` explains the horizon and the
already-played filter, `sync_league` says what it refreshes, and name-taking
tools explain the ambiguity behavior. Player lines now include the injury
designation.

### 6. Add `graph.py` to create the graph — [FIXED]
`src/agent/graph.py` holds `build_graph` (the system prompt is rebuilt every
turn, and history is trimmed). `orchestrator.py` keeps the context, retries and
ask/chat loops. `cli.py graph` prints the Mermaid diagram with no API key needed.

---

## Issues

### 1. Waivers only consider next week's projection — [FIXED]
`waivers --season` and the agent's `get_waiver_targets(horizon="season")` rank
on rest-of-season value. FAAB bids for that horizon are sized on per-week VOR;
otherwise every stash bid 100%. The tool docstring tells the agent to show
both horizons when the user doesn't say which.

### 2. Waivers don't check if the player hasn't played yet this week — [FIXED]
`src/ingestion/schedule.py` loads kickoff times from nflverse's schedule.
Weekly waivers exclude players whose game has kicked off or whose team is on
bye, and say how many were excluded. If the schedule can't be fetched, the
list says so rather than failing. "Held by another team" was already handled.

### 3. Struggles with compound tasks (injured stash) — [FIXED in tooling]
Injured players projected to ~0 and their status was invisible, so no answer
was possible. There's now a `get_injured_stash_candidates` tool: unrostered
players with a long-term designation, ranked by their *healthy*
rest-of-season projection, each with the latest news for the timeline. Live,
it produced a reasoned stash pick (test question 6). The model still does the
final weighing. A stronger model than the free Nemotron would reason better,
but it no longer lacks the data.
Still a modeling choice, not done: season projections treat IR as 0 for the
rest of the season rather than 0 until a return week, which would need a
return-week estimate.

### 4. Can't get last week's results or opponent for a recap — [FIXED]
`WeekResult` now carries the opponent's name, total, top scorers and the
win/loss/tie. `get_week_results` and `cli.py results` default to the last
completed week, and the digest shows the result line. Live: "WIN 164.2 -
155.9 vs. Ravid's RAMS".

### 5. Doesn't consider player status (IR, owned by another team) — [FIXED]
Every player line from the agent carries its designation (`[IR]`, `[Out]`,
`[Questionable]`...), and the CLI tables have a Status column. Roster and
lineup output flag IR-slot moves: definite for IR, "may be, per league
settings" for Out/Sus/etc. Live: the agent named Garrett [IR] with an
IR-slot recommendation.

### 6. Can't get news for players outside the roster — [FIXED]
`get_player_news` already worked for any player. The remaining failure was
name resolution picking the wrong player (New A), now fixed. The docstring
says it covers any NFL player.

### 7. Chat doesn't auto-sync, so it misses fresh injuries — [FIXED]
- `ask` and `chat` call `sync_if_stale` when the last sync is older than
  `config.AUTO_SYNC_HOURS` (2h). `chat` re-checks before every question, and
  the week rollover is picked up mid-session. A failed auto-sync is reported,
  and the answer proceeds on stored data.
- Injury designations come with the players catalog. Its refresh TTL dropped
  from 24h to 6h (`PLAYERS_CACHE_TTL_HOURS`), a compromise with Sleeper's
  "about once a day" guidance. Roster news, the other injury signal, is
  refreshed on every sync.
- `sync_league` now runs the full sync, and it clears every cache, including
  season projections.
- Live: with the last sync backdated 3h, `ask` printed "Local data is 3.0h
  old - syncing from Sleeper..." and answered with fresh news.

---

## Found in review

### A. Player-name lookup silently picked the wrong player — [FIXED]
`storage.resolve_player` ranks exact full-name matches, then players rostered
in the league, then players on an NFL team. It returns a candidate list
instead of guessing, and accepts the "Name (POS/TEAM)" form to pick one. Used
by news, trades and the CLI.

### B. Season projections were full-season, not rest-of-season — [FIXED]
`build_season_projection_table(from_week=...)` prorates by regular-season
weeks remaining (uniform: ignores where byes fall, at most one week of error).
Trades, waivers `--season`, `projections --season` and the agent default to
rest-of-season. `full_season=True` is still available.

### C. Draft assistant ignores roster need — [FIXED]
`draft.suggest_pick(my_positions=...)` works out which starting slots your
picks so far leave open (specific slots before FLEX). A player who would fill
one keeps his full VOR; one who would only be depth gets half
(`DEPTH_VOR_WEIGHT`), so a 4th RB no longer outranks a first QB. It is used in
both the live assistant and `draft --replay`. On the replay of the real draft,
the top suggestion now changes round to round instead of repeating.

### D. Matchup adjustment is off by design — not a bug
Re-confirmed with the corrected backtest (New F): +0.4% (2024) and -0.2% (2025)
against the Sleeper-only baseline, both worse than plain blend.

### E. Injured rostered players zeroed out replacement level — [FIXED]
Found live: TE replacement level showed 0.0 because the Nth-best rostered TE
was an injured 0.0, so every free-agent TE looked like pure value (Gesicki
+10.5 VOR, 42% bid). Rostered 0-projection players (Out/IR/bye) are now left
out of the replacement pool (`waivers._rank_available`). Live DL baseline:
0.0 became 4.4.

### F. The nflverse crosswalk matched only ~15% of players — [FIXED]
Sleeper's catalog has a `gsis_id` for a minority of players (26 of 187 active
RBs), and matching used nothing else. So the nflverse projection source, the
backtest and the new season-to-date tool silently covered mostly veterans.
`id_crosswalk.resolve` now falls back to name+team+position, then to a
name+position or name that belongs to exactly one active player. It normalizes
suffixes ("Kenneth Walker III") and team codes (nflverse `LA` = Sleeper `LAR`).
Coverage for 2026 went from 191 to 1,231 of 1,250 players.
Per PLAN.md's rule, the backtest was re-run on both seasons before keeping it.
The blend still beats Sleeper-only: -3.6% (2024) and -3.7% (2025), over about
4.5x the sample. `PLAN.md` Stage 2 is updated with the new table and the note
that the original -7.5% was measured on the narrow sample.

### G. No tool for actual points scored this season — [FIXED]
Found live: asked for "top RBs so far", the agent subtracted rest-of-season
from full-season projections itself, which breaks "tools compute". New
`get_season_to_date_leaders` gives actual league-scored points from nflverse box
scores, with games and per-game averages. The prompt now forbids combining
tool outputs arithmetically.

### H. Hitting the free model's daily quota crashed with a traceback — [FIXED]
Found live: after about 50 requests in a day, OpenRouter's free tier returns
a 429 `free-models-per-day`. The retry wrapper only caught `ValueError`, so
this surfaced as a raw stack trace. A daily quota now fails fast with a clear
message (reset, credits, or another `OPENROUTER_MODEL`), and short-term 429s
are retried. See `tests/test_retry.py`.

---

## Additions — [OPEN, need external access]

1. **Twitter beat writers.** The list is in `Bitwriters.md`. X's API is paid
   and scraping breaks its ToS. Check whether Sleeper's news feed (already
   integrated) already sources these writers before building a pipeline.
2. **Reddit.** Noisy per Assaf. If built, use it only as sentiment context,
   like trending adds, never as a projection input.
3. **FantasyPros.** Its data endpoints are `robots.txt`-disallowed. It needs
   a paid API key, after which it can plug straight into `ProjectionSource`.
4. **The Athletic.** Paywalled, no API.

## Upgrades

### 1. Long-term memory — [FIXED]
- `chat` saves the conversation to `data/conversations.db` (LangGraph
  `SqliteSaver`, new dependency `langgraph-checkpoint-sqlite`) and resumes it
  next run. `chat --new` starts over. Only the last 40 messages
  (`AGENT_HISTORY_MESSAGES`) are sent to the model, so history can't bloat the
  prompt.
- Durable facts: `remember_preference` / `forget_preference` tools store
  preferences in the league DB, and every system prompt includes them.
- Live: a preference saved in one `chat` was recalled in the next.
