# Agent test questions

Manual checks for the agent, each tied to an item in `ISSUES.md`. Run them
after changes to the agent, prompt or tools; they are the regression record
for behavior the unit tests can't cover (what the model does with the tools).

**How to run:** `uv run python -m src.interface.cli ask "..."` (or inside `chat`).
Tool calls print to stderr as `... tool_name`, so you can see what the agent
actually did. A correct answer reached without the right tool call is still a
**fail**: it means the model answered from memory.

**Setup:** replace `[brackets]` with real names from your league. No manual
`sync` is needed: `ask`/`chat` re-sync on their own when data is over 2h old.

**Last run:** 2026-09-29, week 4, FantaSteelers (IDP, superflex, full PPR),
model `nvidia/nemotron-3-super-120b-a12b:free`.

**Budget:** OpenRouter's free tier allows 50 requests a day, and each question
takes 2-6 (one per model turn). A full pass uses roughly 40, so run the list
in one sitting on a day you haven't otherwise used the agent much.

| # | Ask | Tests | Pass if | Last result |
|---|---|---|---|---|
| 1 | "What season and week is it, and who am I playing?" | Bug 3 | Season, week and opponent match the Sleeper app, via `get_league_state`. | **pass** (2026, week 4, correct opponent) |
| 1b | In a fresh `chat`: "Show me the top 2026 running backs so far." | Bug 3, New G | Treats 2026 as current and uses `get_season_to_date_leaders`: actual points, not projections or 2024 data. | **pass** (Gibbs 98.3, Walker 79.2, Bijan 77.7) |
| 2 | "How did I do last week? Did I win, and what was the score?" | Issue 4 | Both teams' scores and win/loss for the last *completed* week. | **pass** (WIN 164.2 - 155.9 vs. Ravid's RAMS) |
| 3 | "Who should I pick up this week, and who's the best stash for the rest of the season?" | Issue 1 | Two different answers: `get_waiver_targets` with horizon week and horizon season, each labeled. | **pass**, with one slip: it computed "+0.7 over my LB" itself |
| 4 | Friday to Sunday morning: "Should I pick up [a player from Thursday night's game]?" | Issue 2 | Says his game is already over this week and judges him on next week or rest of season. He shouldn't appear in weekly waiver targets. | not yet run (needs a game-week Friday) |
| 5 | "Is anyone on my roster injured? Should I move them to IR?" | Issue 5 | Names each player's designation as the Sleeper app shows it, recommends IR for IR-designated players, and caveats Out/Sus eligibility. | **pass** (Garrett [IR]; Okonkwo, Cross [Out]) |
| 6 | "Find me an injured player on waivers worth stashing. Consider his projection when healthy and how serious the injury is." | Issue 3 | Uses `get_injured_stash_candidates`, returns unrostered injured players with healthy value and a timeline from the news. | **pass** (Conner, returning week 4; Mayfield ~3 weeks) |
| 7 | "What's the latest on [a player on another team in your league]?" then the same for [an unrostered player] | Issue 6 | Current news for both, attributed to the right player. | not run: the daily free-model quota ran out first (see note below) |
| 8 | "Any news on Williams?" | New A | Asks which Williams, **or** states in the first line which one it assumed and why. | **partial**: chose Jameson Williams (on the roster) and named him, but didn't say it had assumed. A model-discretion limit, since the tool returns candidates. |
| 9 | "Does my league start individual defenders or a team defense, and is it PPR?" | Bug 4 | Matches league settings. Also run it in a non-IDP league (Assaf's): it must not say IDP there. | **pass** in IDP league. Non-IDP: covered by `tests/test_prompts.py`, live run pending. |
| 10 | "Show me my roster in [a league name you are NOT in]." | Bug 2 | Says no such league and names the real ones. With a second real league, it switches (`switch_league`) and answers from it. | **pass** (refused "Work League", named FantaSteelers) |
| 11 | Backdate the last sync (below), then: "Any injury news on my roster I should act on?" | Issue 7 | Stderr shows `Local data is 3.0h old - syncing from Sleeper...` before any tool call, and the answer reflects the fresh news. | **pass** |
| 12 | In one `chat` session: get a lineup, then "Why did you bench [a starter-quality player]?" (asking about a player who actually *starts* makes a good trap) | general | Consistent with the lineup it gave, names the factor (projection, injury, matchup), and invents no numbers. | **partial**: caught the trap ("Kelce is not benched"), but its reasoning contradicted itself (13.2 vs 11.9) and it did prose arithmetic ("edges out by 0.2") |
| 13 | `chat --new`: "Remember that I'm risk-averse at FLEX." Exit. Then `chat`: "What do you remember about how I play?" | Upgrade 1 | First run calls `remember_preference`. Second run prints "Resuming your conversation" and recalls it. Afterwards, clean up with "forget preference 1". | **pass** |
| 14 | "Who are the top 5 WRs in actual points so far, and who are the top 5 projected for the rest of the season?" | New F, New G | Two tools (`get_season_to_date_leaders`, `get_season_projections`), two different lists, and young players (rookies, second-years) present in both. | **pass** (JSN leads actual; Nacua [Out] leads rest-of-season) |
| 15 | "Who are my starters?" | Roster freshness | Lists the lineup set in Sleeper by slot (QB / SUPER_FLEX match the app), from MY ROSTER or `get_my_roster`, **not** `recommend_lineup`. | **pass** 2026-10-07, Or Test League: only `get_my_roster`; QB Allen, SUPER_FLEX empty, matching Sleeper |
| 16 | In one `chat`: get a waiver recommendation, make a **free-agent** add/drop in Sleeper (not a waiver claim: claims stay pending until the waiver run and the API doesn't show them), then "Who's on my roster?" | Roster freshness | The answer reflects the move without being asked to sync. | **pass**, simulated 2026-10-07 (pre-move roster restored locally, then the refresh pulled the real one): answered with Goff, without Maye, and named the change |
| 17 | Remove a player from the local copy of my roster (below), then ask about that player. | Roster freshness | The refresh before the question restores him, and the answer includes him. | **pass** 2026-10-07 (same run as 16) |
| 18 | "Who is on the Seattle roster?" | NFL team roster | Calls `get_nfl_team_roster`. No DK Metcalf (PIT since 2025). Any of my players is labeled starter/bench exactly as in Sleeper. | **pass** 2026-10-08, Or Test League: Metcalf absent, Jones bench / Love starter. (Before round 2, it called Jones a starter.) |
| 19 | "What NFL team is DK Metcalf on, and who are the Seahawks' top wide receivers?" | NFL team roster | PIT, from a tool, not memory. WRs from `get_nfl_team_roster`. | **pass** 2026-10-08: PIT via `get_player_news`; Smith-Njigba, Shaheed, Kupp |
| 20 | "Show me [another manager]'s roster." then "Who is on the Steelers?" | Terminology | First uses `get_fantasy_team_roster`, second `get_nfl_team_roster`. Each answer says "fantasy team" or "NFL team" where it matters. | not yet run: free-model daily limit reached 2026-10-08. Tools checked directly against live data: pass |
| 21 | "Which team is [a player on another fantasy team] on?" | Terminology | Either asks which kind of team is meant, or answers both (NFL team and fantasy team) and labels each. | not yet run (same reason) |

### Backdating the last sync (for question 11)

```
uv run python -c "import config; from datetime import datetime, timedelta, timezone; from src.ingestion import storage; c = storage.get_connection(config.DB_PATH); storage.set_state(c, 'last_synced_at', (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())"
```

## Known model-side limits (not tool bugs)

- The free model sometimes does small arithmetic in prose (questions 3 and 12),
  and its reasoning can contradict the numbers it just quoted (question 12). The
  prompt forbids prose arithmetic. A stronger `OPENROUTER_MODEL` is the lever if it matters.
- When a name is ambiguous, it may resolve it from context rather than ask
  (question 8). The tool returns candidates; the model chooses what to do.

## Covered by unit tests instead

Name resolution, prompt contents (date, season, IDP/DEF/PPR, preferences),
auto-sync staleness rules, league selection, kickoff and bye detection, the
waiver exclusions and replacement-level fix, opponent recap, rest-of-season
proration, crosswalk fallbacks, season-to-date scoring, and chat persistence
and history trimming. See `tests/`.

### Removing a player from the local roster (for question 17)

Replace `NAME` with a player on your roster. The next `ask`/`chat` question
re-pulls rosters from Sleeper, which puts him back.

```
uv run python -c "import json, config; from src.ingestion import storage; c = storage.get_connection(config.DB_PATH); lg, u = storage.get_state(c, 'active_league_id'), storage.get_state(c, 'active_user_id'); r = storage.get_roster(c, lg, storage.get_roster_id_for_user(c, lg, u)); pid = storage.resolve_player(c, 'NAME', lg)[0]; c.execute('UPDATE rosters SET players = ? WHERE league_id = ? AND roster_id = ?', (json.dumps([p for p in json.loads(r['players']) if p != pid]), lg, r['roster_id'])); c.commit()"
```
