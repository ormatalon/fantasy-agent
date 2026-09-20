# Project log — NFL Fantasy Engine

A record of what was built, what broke, and what changed along the way.
Companion to `PLAN.md` (the build directive) — this is the retrospective.

**At a glance:** ~4,000 lines across 40 modules, 93 tests, 12 commits, stages 0–5 complete.

**Build session:** the whole project was built in one Claude Code session —
`session_01XHXQJnXDWzip55FVspzyAU`. The full transcript, including the
debugging detours that produced §5, is at
<https://claude.ai/code/session_01XHXQJnXDWzip55FVspzyAU>. Every commit
carries the same reference in its `Claude-Session:` trailer, so any single
change can be traced back to the conversation that produced it.

---

## 1. Starting point

An empty repository containing exactly one thing of substance: `PLAN.md`, a
build directive specifying an LLM-orchestrated agent to play a Sleeper
fantasy football league. It defined six stages, a mandatory stop-and-approve
checkpoint between each, and a set of hard constraints ("agent orchestrates,
tools compute"; "brain before hands"; "mean and variance, always").

No code. No data. The plan was the spec.

---

## 2. What exists now

```
Ingestion  ->  Projections  ->  Decisions  ->  Interface  ->  Execution
(Sleeper,      (blend, mean     (lineup,      (CLI, agent,   (browser,
 nflverse,      + variance)      waivers,      email)         approve-gated)
 news)                           trades,
                                 draft)
```

| Layer | Modules |
|---|---|
| Ingestion | `sleeper.py`, `nflverse_client.py`, `news.py`, `storage.py`, `id_crosswalk.py` |
| Projections | `engine.py`, `blend.py`, `matchup.py`, `injury.py`, `scoring.py`, `sources/` |
| Evaluation | `backtest.py`, `metrics.py` |
| Decisions | `lineup.py`, `waivers.py`, `trades.py`, `draft.py`, `vorp.py`, `results.py` |
| Agent | `orchestrator.py` (LangGraph), `tools.py` (11 tools), `prompts.py` |
| Interface | `cli.py` (16 commands), `digest.py`, `notify.py` |
| Execution | `approval.py`, `browser.py` |

---

## 3. Stage timeline

| Stage | Built | Outcome |
|---|---|---|
| **0** | Sleeper read client, SQLite store, CLI | League discovered from username alone |
| **1** | Pluggable projection sources, blend, matchup + injury overlays | One artifact: mean + variance per player |
| **2** | Backtest vs. baseline | Proved blending helps and matchup doesn't |
| **3** | Lineup (ILP), waivers (VORP), trades, draft assistant | Each prints a ranked recommendation + "why" |
| **3.5** | LangGraph agent, tool registry, guardrails | *Added to the plan — it had no stage* |
| **4** | Email notifier, weekly digest | Digest delivered end-to-end to a real inbox |
| **5** | Approve gate, browser automation | Lineup set on Sleeper, gate provably unbypassable |

---

## 4. Decisions that changed the plan

| Change | Why |
|---|---|
| LangGraph adopted as agent framework | Requested; scoped to stateful flows, not single-shot calls |
| League discovery by username, not a hardcoded ID | Requested — agent should work for any user/league |
| OpenRouter instead of a direct Anthropic key | Requested |
| Model swapped to `nemotron-3-super-120b-a12b` | `gpt-oss-120b:free` went paid-only mid-project |
| FantasyPros dropped as a source | Its data endpoints are robots.txt-disallowed |
| **Stage 3.5 inserted** | The plan called the agent "central" but gave it no stage |
| IDP support added | The league starts DL/LB/DB, not a team defense |
| Season-long projections added | Closed the trades module's weekly-only limitation |
| News, trending, results added | Signals the projection math cannot produce |
| Matchup adjustment disabled by default | The backtest said it makes things worse |

---

## 5. Bugs found and fixed

### Data correctness

| Bug | Cause | Fix |
|---|---|---|
| 2025+ backtests all 404'd | `nfl_data_py` points at an nflverse release that stops at 2024; the package is unmaintained | Read nflverse's current asset directly (`nflverse_client.py`) |
| Backtest error rates were distorted | nflverse data includes *every* roster position (linemen, punters); thousands of trivial "0 predicted vs 0 actual" pairs inflated the sample | Filter to fantasy-relevant positions everywhere nflverse data is consumed |
| Every team defense looked infinitely valuable | A position with no roster-slot demand (`DEF` in an IDP league) defaulted to a `0.0` replacement baseline, so its whole projection read as "value" | Exclude positions with no computed demand rather than defaulting |
| IDP replacement levels were meaningless | DE/DT/NT each got their own baseline from a pool too sparse to be informative | Pool granular positions into the slot's group (DE/DT/NT → one `DL` baseline) |

> **The second bug changed a conclusion.** The matchup adjustment was first judged "worse than doing nothing." After the position filter was fixed, it beat the naive baseline but still lost to plain blending — so it stayed disabled, on better evidence.

### Agent layer

| Bug | Cause | Fix |
|---|---|---|
| Tools crashed on every agent call | LangGraph runs tool nodes on worker threads; SQLite connections are thread-bound | `check_same_thread=False` at the connection source |
| Retries never fired on provider failures | OpenRouter returns upstream errors as **HTTP 200 with the error in the body**, so the OpenAI SDK never sees a retryable status | Explicit retry wrapper matching on the body's error text |
| Console crashed printing answers | Model output contains arbitrary Unicode; Windows consoles default to cp1252 | Force UTF-8 on stdout/stderr — can't sanitise text the *model* writes |
| A question looked like a hang | 7 sequential model calls ≈ 27s of silence | Stream each tool call to stderr, so stdout still pipes cleanly |

### Decisions and interface

| Bug | Cause | Fix |
|---|---|---|
| Lineup slots showed `(empty)` with a player available | The ILP maximises points under a `≤1` slot constraint, so a 0.0-projected player is worth exactly as much as nobody | Tiny per-assignment bonus breaking ties toward filling a slot |
| Email failed with an opaque `535` | A Gmail *account* password was used; Google stopped accepting those for SMTP in 2022 | Validate app-password shape before connecting, and say what to fix |
| `notify.py` read config that didn't exist | `GMAIL_ADDRESS` was in `.env` but never exposed through `config.py` | Caught by tests before it ever ran |
| The digest "watch list" listed the entire lineup | Every starter has news every week, so "starters with news" is everyone | Filter to decision-relevant headlines only |
| A ruled-out starter was silently missed | The keyword `"practice"` doesn't match `"practi**cing**"`, which is how status is actually reported | Match on stems; bias toward false positives |

### Browser execution

| Bug | Cause | Fix |
|---|---|---|
| Lineup diff showed the same player twice | Slots repeat (two RB, two WR, two TE); a dict keyed by slot name collapses them | Match positionally |
| Diff reported 6 changes where 2 were real | Two RBs swapping *which* RB slot they occupy isn't a change | Compare per slot type, not per position |
| Every click was intercepted | Cookie-consent overlay; clicking its accept button doesn't stick because the dialog re-renders | Remove the nodes outright |
| Sleeper's login blocked automation | Bot detection on a Playwright-launched browser | Attach to the user's own running Chrome over CDP |
| **An approved change failed mid-write** | The incoming player's game had already kicked off. The refusal was correct, but fired *after* the human approved | Probe Sleeper for eligibility *before* proposing; check **both** sides of a swap |

> The last one is the most instructive. The first attempt at a fix checked the
> slot's *current* occupant — the wrong side. The blocker was the *incoming*
> player. You can't bench someone who has played, and you can't start someone
> who has; both needed checking.

---

## 6. Design decisions worth noting

**The approve gate is structural, not conventional.** Stage 5's definition of
done is "the gate cannot be bypassed", so: `Approval` can't be constructed
outside its module, each approval is bound to a hash of the exact payload
shown to the human, typing `y` is rejected in favour of `APPROVE`, and no
`--force` exists — with a test inspecting signatures to keep it that way.
Eleven adversarial tests each try to get a write authorised without a human
confirming that exact payload. A consequence: **the write path cannot run
unattended**, which is the requirement rather than a limitation of it.

**Eligibility is read from Sleeper, not reimplemented.** Selecting a slot makes
Sleeper mark each row valid/invalid — which already accounts for kickoff locks
and position rules. The code reads that marking rather than modelling the
league's rules a second time, and refuses to click anything not marked valid.

**The digest is deliberately deterministic.** It's what an unattended Sunday
job sends, so it can't depend on a flaky free-tier model being available. The
agent narrates the same data on demand; the digest has to work alone.

**News is surfaced as prose, not parsed into signal.** The plan imagined
extracting structured facts. But the source's `analysis` field is already
written for fantasy managers, and flattening it to `starter_out: bool` discards
the nuance that makes it worth reading. With a 262k-token context window,
compression bought nothing.

---

## 7. Known gaps

| Gap | Note |
|---|---|
| Draft picks ignore roster need | Ranks purely by VORP — will suggest a 4th RB with no QB rostered. Fix is deterministic and belongs in the tool, not the LLM |
| Season projections are full-season, not rest-of-season | Mid-season use should prorate; that's a modelling decision, not a mechanical one |
| Matchup adjustment disabled | Doesn't beat plain blending. Code and scoring retained so a reworked version gets an immediate answer |
| IDP excluded from matchup/backtest | An IDP's value depends on the opposing *offense*, not "points allowed by position" — a separate model |
| Injury overlay isn't backtested | Local data holds only *current* injury state; scoring it against a past week would leak information |
| Terminal-only interaction | Email is push-only. A Telegram bot would add two-way access away from a desk |

---

## 8. Testing

93 tests, plus one opt-in live test that hits the real model (skipped by
default — it needs network and a rate-limiting free tier).

The tests that earned their keep weren't the happy paths:

- **Adversarial gate tests** — 11 attempts to forge, reuse, or sidestep an approval
- **Regression-by-evidence** — the backtest is a standing guard; it's what disabled the matchup adjustment and what caught that the first verdict was based on flawed data
- **Real-text fixtures** — lock detection and news filtering are tested against actual headlines (`"Not practicing Wednesday"`), which is how the stem-matching bug surfaced

---

## 9. What this looks like in use

```bash
uv run python -m src.interface.cli sync            # refresh league state + news
uv run python -m src.interface.cli digest --email  # weekly report, printed and mailed
uv run python -m src.interface.cli lineup --live   # current vs recommended
uv run python -m src.interface.cli lineup --apply  # enact it, after typing APPROVE
uv run python -m src.interface.cli ask "any injury news I should act on?"
```
