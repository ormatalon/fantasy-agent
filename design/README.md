# Design: Fantasy Agent v1

Static mockups, no build step. Open the HTML files in a browser. Each has a
dark bar at the top (the "state bar") that switches between states; the bar is
mockup-only and not part of the app. A state can also be opened directly, for
example `chat.html#quota`. Sample data is invented (an IDP league, week 9).

| File | Screen | States on the state bar |
|---|---|---|
| `setup.html` | Setup and Settings (same screen) | first run, finding leagues, username not found, Sleeper unreachable, one league, several leagues (with unsupported types), key rejected, saving, settings (already set up) |
| `chat.html` | Main window: chat plus the my fantasy team panel | loading history, empty, answered, working (progress), refresh failed, daily quota, error, too many steps, missing key, key refused, no league, switch league, start fresh |
| `team-panel.html` | The panel on its own | loading, before games, live, final, no matchup this week, refresh failed, error with nothing stored, no league chosen |

The CSS is inlined in every file (the same `:root` tokens and component rules),
so each opens alone. If a token changes, change it in all three.

The chat-screen missing-key and no-league banners in the mockups can't be reached in the NiceGUI app, because setup gating comes first; a missing key only shows as an error when a key is removed while a chat is open.

## Window

pywebview window. Default 1280 x 800, minimum 960 x 640. Below 960 wide the
window scrolls sideways; there is no phone layout in v1.

- Top bar: 52px, white, 3px yellow line under it. Brand, league switcher,
  league facts (season, week, IDP or team DEF, scoring), Start fresh, Settings.
- Main area: chat fills the remaining width; the fantasy team panel is fixed at
  360px on the right and scrolls on its own. Chat messages are capped at 760px
  and centered, so wide windows stay readable.
- Setup: one column, 640px wide, centered, scrolls. Four numbered steps:
  username, league, OpenRouter key, model. Settings is this same screen with
  the saved values filled in.
- The user can read the panel while a question is running. Only Send is
  disabled while working.

## Tokens

Colors (the first five come from `AGENT.md`):

| Token | Value | Use |
|---|---|---|
| `--yellow` | `#ecad0a` | accent lines (top bar, agent message edge, leader score underline), warning banner edge |
| `--blue` | `#209dd7` | key sections (matchup top edge, progress edge), focus ring, example chips |
| `--purple` | `#753991` | Send, Save and start, Start fresh confirm, any primary action, selected option |
| `--navy` | `#032147` | headings, user messages, secondary buttons |
| `--gray` | `#888888` | supporting text and labels, large or non-essential only (see below) |
| `--gray-strong` | `#5c6670` | supporting text at 12 to 14px |
| `--blue-dark` | `#0e6f9e` | link text on white |
| `--text` | `#1c2733` | body text |
| `--bg` / `--surface` / `--line` | `#f4f6f9` / `#fff` / `#dde3ea` | page, cards, borders |
| `--danger`, `--ok` | `#b42318`, `#1a7f4b` | errors, saved or free (with tints) |

Contrast note: `#888888` on white is 3.5:1 and `#209dd7` on white is 3.0:1, both
below WCAG AA (4.5:1) for small text. The mockups use `--gray-strong` and
`--blue-dark` for small text and keep the exact brand colors for fills, lines,
large text and borders. Red and green are not in the brand palette; they are
added only for error and success.

Type: system stack `"Segoe UI", system-ui, ...` (no font download; works
offline). Sizes: 12 (labels, meta), 13, 14 (body), 16 (h3), 20 (h2), 26 (h1).
Weights: 400, 600, 700. Scores use tabular numerals.

Spacing: 4px scale: 4, 8, 12, 16, 24, 32, 48. Radius 8 (cards), 4 (inputs,
buttons). Buttons and inputs are at least 36px tall.

## Components and rules

- **Buttons:** primary = purple fill, white text (one per view). Secondary =
  white with navy border. Quiet = blue text. Disabled = gray fill.
- **Links:** blue-dark, always underlined.
- **Banners:** 4px left edge. Yellow = something is off but the answer or screen
  still works (refresh failed, quota, missing key). Red = it did not work (no
  answer, nothing stored). Blue = information. Bold line says what happened,
  next line says what it means or what to do.
- **Injury flags:** a chip with letters, never color alone. Q questionable, D
  doubtful, OUT out, IR injured reserve, BYE bye week. Position and NFL team
  abbreviation sit on the line below the player name.
- **Agent messages:** white card, yellow left edge, and a small gray line naming
  the sources and how fresh they are. User messages: navy, right-aligned.
- **Progress:** card with blue left edge while a question runs: the steps done so
  far (check mark), the current step with a spinner, and an elapsed timer
  (m:ss). No guessed percentage and no list of future steps, because the number
  of steps is not known in advance.
- **A failed question is never dropped.** It stays in the thread as a dashed
  card and its text is put back in the input, with Ask again.
- **Secrets:** the key field is a password field. After saving, only "Key saved
  on this computer" is shown, with Replace key. The key is never returned.
- **Not supported leagues:** listed, disabled, with the type named
  (dynasty, keeper, best ball, guillotine) and "season-long redraft only".
- **Motion:** spinner and skeleton shimmer only; both slow or stop under
  reduced-motion.
- **Words:** say "fantasy team" or "NFL team", never "team" alone. No emojis.
  Plain, short sentences.

## Accessibility

Visible focus ring (3px blue). Controls are real buttons, inputs and radios.
Progress and errors use `role="status"` / `role="alert"`; the thread is an
`aria-live` region. Flags carry text, not only color.

## Copy for states (summary)

| State | Message |
|---|---|
| Username not found | No Sleeper user named that. Check the spelling. |
| Sleeper unreachable (setup) | Could not reach Sleeper. Check your internet connection and try again. |
| Key rejected | OpenRouter did not accept this key. Check that you copied all of it, or create a new one. Nothing was saved. |
| Missing key (chat) | Add your OpenRouter key to ask questions. Your league and fantasy team still load. |
| Refresh failed | Could not refresh from Sleeper. This answer uses data stored on [time]. A move you made after that is not included. |
| Daily quota | Daily limit reached for this model. Wait, choose another model, or add credits to your OpenRouter account. (No reset time is shown: `resets_at` is always null in v1.) |
| Error | Something went wrong, so there is no answer. Nothing was changed in Sleeper. |

## API mapping (checked against `docs/api-contract.md`)

| Screen part | Contract |
|---|---|
| Setup: Find leagues | `GET /api/leagues?username=`; errors `username_not_found`, `sleeper_unreachable` |
| League option | `name`, `num_fantasy_teams`, `defense` (idp, team_def, none), `reception_points` (1.0 PPR, 0.5 half PPR, 0 standard), `supported`, `unsupported_reason` (dynasty, keeper, best_ball) |
| League not selectable | `supported: false` shows the named type; `status: pre_draft` shows "Not playable yet". Both are disabled radios |
| Save | `PUT /api/settings`, then `PUT /api/league`; `key: saved`, `422 key_rejected` (nothing saved), `model_unknown` |
| Several leagues, none chosen | `needs_league_choice: true` in `GET /api/state`; page never preselects |
| Key field | `key_set` only; after saving show "Key saved on this computer" and Replace key |
| Model options | `GET /api/models`: `name`, `id`, `free`, `recommended`, `note` |
| Top bar | `current_league`: `name`, `season`, `this_week`, `format` (defense, reception_points) |
| Panel | `GET /api/panel`: `fantasy_team` (name, record, rank), `slots` (empty slot = `player: null`), `matchup` (opponent, scores, `state`), `refresh.stored_at` for "Updated" and "data stored on" |
| Panel refresh | `POST /api/refresh`; failure comes back as `refresh.failed`, not an HTTP error |
| Progress | SSE `step` events: `label`, `status` (running, done, error), `elapsed_ms`. Timer starts from `elapsed_ms` so it survives a reload; `GET /chat/history` `running` brings the progress card back |
| Answer sources line | `sources[].label`, `sources[].as_of` |
| Refresh failed in chat | `answer.refresh.failed` non-empty: answer shown with the yellow banner and `refresh.stored_at` |
| Failed question | `status: failed` plus `error.code`; dashed card, text back in input, Ask again sends `retry_of` |
| Start fresh | `DELETE /api/chat/history` |

Error code to chat state: `key_missing` -> Missing key; `key_invalid` -> Key
refused; `no_league` -> No league; `quota_exceeded` -> Daily quota;
`provider_error`, `internal_error` -> Error; `too_many_turns` -> Too many steps;
`question_in_progress` -> Send stays disabled (a second window shows "A question
is already running."). `question_kept` is true on every chat error, so each
shows the question as kept (dashed card, text in the input; in Missing key,
Key refused and No league the input is disabled but keeps the text).

Quota copy gives no reset time and no counts, because `resets_at` is always
null in v1.

`GameRef.status` `bye` shows the BYE chip; `kickoff` is shown in local time.

`matchup` null (fantasy playoff bye, or an eliminated fantasy team): the panel
shows "No matchup this week" in place of the score card; roster, flags and
the warning still show.

### Injury chips

From `PlayerRef.injury_status`. Letters or short words, never color alone;
the full words are the chip's tooltip.

| Value | Chip | Color | Meaning shown |
|---|---|---|---|
| null | none | | healthy |
| Questionable | Q | yellow | might play |
| Doubtful | D | orange | unlikely to play |
| Out | OUT | red | will not play |
| IR | IR | red | injured reserve |
| PUP | PUP | red | physically unable to perform |
| Sus | SUS | red | suspended |
| DNR | DNR | red | did not report |
| NA | NA | gray | not active |

A starter whose chip is OUT, IR, PUP, SUS, DNR or NA gets the warning above the
roster. A player on BYE does not (the lineup slot is a plain bye, not an injury).

## Decisions and open questions

Decided by the owner:
- Small text uses the darker shades `--gray-strong` (`#5c6670`) and
  `--blue-dark` (`#0e6f9e`); the exact brand colors stay for fills, lines,
  borders and large text.
- Red and green are allowed for error and success, outside the brand palette.
- No Stop button in chat (a closed connection does not cancel a question).
- No Taxi section in the panel.

Still open:
- Which paid model ids Settings offers. The mockup shows a placeholder.
