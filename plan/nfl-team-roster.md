# Plan: answer NFL team roster questions from synced data

Status: implemented (two rounds). Branch: `nfl-team-roster-update`.

## Problem

Asked in `chat` for Seattle's roster, the agent listed DK Metcalf. Metcalf
has been on Pittsburgh since the 2025 offseason.

## Causes (verified)

- **The local data is correct.** In `data/fantasy.db`, `players` has
  Metcalf (`5846`) as `team = 'PIT'`, synced 2026-10-08. The `SEA` rows are
  current (Darnold, Smith-Njigba, Kupp, Shaheed, ...). So this is not a
  sync or freshness bug.
- **No tool answers "who plays for NFL team X".** The 20 tools in
  `src/agent/tools.py` cover my roster, other managers' rosters, projections,
  waivers, news, and so on. `get_team_roster` sounds right, but it matches
  *fantasy managers* in my league (`storage.find_roster_by_team_query` searches
  `league_users.team_name` / `display_name`). The projection tools filter by
  position, not by NFL team.
- **So the model answers from memory.** With nothing to call, the free model
  answers from training data, where Metcalf is still a Seahawk. The prompt's
  "do not invent players / tool output beats your knowledge" rules don't
  prevent this. The model only follows them reliably when a tool gives it
  something to follow. That was the same lesson as `roster-freshness.md`.

I couldn't read the stored chat (loading `conversations.db` via `SqliteSaver`
timed out), so I haven't seen which tools, if any, the model called. Step 4
covers that.

## Approach

Give the model a tool that returns the real NFL roster, then point the prompt
at it. Fix this in the tool, not with more prompt rules.

### 1. `get_nfl_team_roster(team)` tool (`src/agent/tools.py`)

- `team` accepts an abbreviation (`SEA`), a city (`Seattle`) or a nickname
  (`Seahawks`), case-insensitive. Resolve it with a fixed 32-team map in a
  new `src/ingestion/nfl_teams.py` (abbr -> city, nickname). Also accept
  Sleeper's abbreviations as they appear in `players.team` (e.g. `JAX`, `WAS`,
  `LAR`). If the input is unknown or ambiguous (e.g. `Los Angeles`, `New York`),
  return a message that lists the candidates, the same way `resolve` does for
  players.
- Read from `players WHERE team = ?`, limited to fantasy-relevant positions:
  QB/RB/WR/TE/K, plus DL/LB/DB when the league has IDP slots (`IDP_SLOTS` in
  `prompts.py`). Group by position and order by `depth_chart_order` (nulls
  last). Show injury status in [brackets] and this week's projection where one
  exists, using the existing `_fmt_player`.
- Mark each player's league status: on my roster, rostered by <team label>,
  or free agent. This makes "anyone on Seattle I can pick up?" answerable in
  one call.
- Put a source line in the header ("from Sleeper players catalog, synced
  <synced_at>") so the answer can say how fresh the data is.
- Add a storage helper, `storage.players_on_nfl_team(conn, team, positions)`,
  so the SQL lives in `storage.py` like the rest.

### 2. Disambiguate `get_team_roster`

Rewrite its docstring to say it is "a fantasy manager's team in my league,
NOT an NFL team; for NFL teams use get_nfl_team_roster". When it finds no
match and the query resolves to an NFL team, return a pointer to the new
tool instead of "No team matching 'Seattle'".

### 3. Prompt (`src/agent/prompts.py`)

Add one line under HOW TO ANSWER: questions about an NFL team's roster or
depth chart go through `get_nfl_team_roster`. Never list which players are
on an NFL team, or which team a player is on, from memory.

### 4. Tests

- `tests/test_nfl_team_roster.py`: with a seeded `players` table, `Seattle`,
  `seahawks` and `SEA` all resolve. A player whose `team` is `PIT` is not
  listed under SEA. Order follows the depth chart. IDP positions show only in
  IDP leagues. Ownership labels are correct. Unknown and ambiguous inputs
  return the expected messages.
- `get_team_roster("Seattle")` with no matching manager points to the new tool.
- `tests/test_prompts.py`: the new rule is in the prompt.
- Manual check: `uv run python -m src.interface.cli ask "who is on the
  Seattle roster?"` calls `get_nfl_team_roster` and doesn't list Metcalf.
  Then `ask "what team is DK Metcalf on?"` should get PIT, either via the new
  tool or via `get_player_news` / `resolve`, which already print `(WR/PIT)`.

## Round 1 result (2026-10-08)

Steps 1-4 implemented. 179 tests pass. Live `ask "who is on the Seattle
roster?"` called only `get_nfl_team_roster` and did not list Metcalf, so the
original bug is fixed. The tool output was checked directly and is correct.
Two new problems were in the model's answer:

- **Wrong claim about my lineup.** It said "Ernest Jones (LB) and Julian
  Love (DB) are starters for your team". Jones is on my bench. The tool only
  said `MY ROSTER`, so the model invented the slot.
- **Ownership dropped.** It labeled free agents "FA" but left out the manager
  for players on other fantasy rosters (Darnold, Smith-Njigba -> Ravid's
  RAMS, Myers -> orimetz). The info was in the tool output, one label per line
  across about 70 lines, and got lost when the model compressed it.

Also noise: IR players printed `[IR] ... (status: Inactive)`, which is
redundant.

## Plan, round 2

Same principle: give the model the fact directly instead of a rule.

1. **Say where my players are.** Label my players `MY ROSTER (starter)`,
   `(bench)` or `(IR)`, using `my_lineup(ctx)` (the same lineup source as
   `get_my_roster`).
2. **Ownership summary up front.** After the header, one line per fantasy
   team that rosters any of these players, mine first. Example: `Rostered in
   my league: MY ROSTER: Julian Love (starter), Ernest Jones (bench); Ravid's
   RAMS: Jaxon Smith-Njigba; ...`. Everyone else is a free agent. A short
   block is much harder to lose than 70 per-line labels.
3. **Less noise.** Show Sleeper's `status` only when there is no injury
   designation to explain it.
4. **Tests.** Starter/bench labels and the summary line. Then re-run the
   live question and the regression questions in `TEST_QUESTIONS.md`.

## Round 2 result (2026-10-08)

Implemented. 180 tests pass (1 live test skipped by default). Live:
- `who is on the Seattle roster?`: only `get_nfl_team_roster`, no Metcalf,
  Ernest Jones `MY ROSTER (bench)`, Julian Love `(starter)`, and other
  managers named (Emmanwori -> orimetz).
- `what NFL team is DK Metcalf on ...`: PIT via tools, with the right SEA WRs.
- `TEST_QUESTIONS.md` regression set (1, 1b, 2, 3, 5, 6, 7, 8, 9, 10, 14,
  15): all behaved as specified, each via the expected tool. Not run: 4
  (needs Friday-Sunday), 11, 13, 16, 17 (manual setup or `chat` sessions).
  New questions 18 and 19 were added there.

## Out of scope

- Full 53-man rosters, practice squads, and OL: the catalog has them, but
  they're not fantasy-relevant. Easy to add later with a `positions` argument.
- Detecting memory-based answers in general (e.g. checking every player name
  in an answer against the catalog). That's worth considering if this happens
  again on a different question type.
