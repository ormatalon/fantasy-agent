# Plan: observability with LangSmith

Status: steps 1-6 implemented, not committed. Not yet checked against a real
LangSmith key (none in `.env`). Branch: `obsavability-langsmith`.

## Implementation notes (2026-10-14)

- **No `wait_for_all_tracers()`.** LangSmith's tracing thread is non-daemon
  and drains its queue on exit. A live `ask` with tracing on and no key showed
  the final upload attempt (401) included the `ask` root run, so short-lived
  commands already send their traces.
- **No explicit `thread_id` in metadata.** LangGraph copies it from
  `configurable` itself; the test checks it's there.
- **`chat` builds its run config per turn**, since a full sync mid-session
  can change the week.
- **The refresh is its own trace** (`refresh_before_question`), next to the
  `ask`/`chat` trace rather than nested in it: it runs before the graph.

## Goal

See what the agent actually does on each question: the full prompt sent to the
model, every tool call with its arguments and result, token counts, latency,
and the free-tier retries. Today the only window into this is the
`... tool_name` lines `ask`/`chat` print to stderr.

## What exists today

- Only `ask` and `chat` call a model. Every other CLI command is deterministic
  and is out of scope.
- `src/agent/graph.py`: a two-node LangGraph loop,
  `START -> plan -> (tools -> plan)* -> END`. `plan` rebuilds the system prompt
  every turn, trims history to `AGENT_HISTORY_MESSAGES`, and hides outdated
  roster results.
- `src/agent/orchestrator.py`: builds the model (`ChatOpenAI` pointed at
  OpenRouter), retries transient free-tier errors in `_invoke_with_retry`,
  calls `refresh_before_question`, and drives `agent.stream(...)` in `_run`.
- `src/agent/tools.py`: about 20 `@tool` closures over `AgentContext`,
  including the write tools (lineup, IR, add/drop, waiver claims).
- `chat` keeps history in `SqliteSaver` (`data/conversations.db`) under thread
  `"chat"`. `ask` uses `MemorySaver` under thread `"cli"`.
- `langsmith` 0.12.5 is already installed as a dependency of `langchain-core`.
  No new package is needed.

LangChain and LangGraph trace to LangSmith on their own once the env vars are
set, so most of this plan is configuration, run labels and testing.

## Steps

### 1. Turn tracing on through env vars

Add to `.env.example`, off by default:

```
# --- Observability (LangSmith) ---
# Traces every ask/chat question: prompts, tool calls, results, tokens.
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=fantasy-agent
```

`config.py` already runs `load_dotenv()` before any graph runs, so nothing
else is needed to start tracing. Each question becomes one trace containing:

- the graph run;
- each `plan` turn: the full prompt, its tool calls, tokens and latency;
- each tool's inputs and outputs;
- failed free-tier attempts, shown as errored LLM runs inside the retry loop.

### 2. Label the runs

In `orchestrator._run`, add these to the config passed to `agent.stream`,
next to `thread_id`:

- `run_name`: `"ask"` or `"chat"`;
- `tags`: `[command, config.OPENROUTER_MODEL]`;
- `metadata`: `league_id`, `season`, `week`, `fantasy_team_name`, `model`.

With these, traces can be filtered by league, week and model. LangSmith's
Threads view groups traces by `metadata.thread_id`. LangGraph normally copies
the `configurable` thread id there; if it doesn't, set it explicitly. All
`chat` traces land in one thread (`"chat"`), and all `ask` traces in another
(`"cli"`).

### 3. Trace the pre-question refresh

Add `@traceable(name="refresh_before_question")` to
`src/ingestion/sync.refresh_before_question`. A Sleeper sync then shows up as
its own span instead of being mixed into model latency. The decorator does
nothing when tracing is off.

### 4. Make sure `ask` sends its trace

`ask` exits right after printing, while traces are sent in the background.
Run `ask` with tracing on and confirm its trace arrives in LangSmith. If it
doesn't, call `langchain_core.tracers.langchain.wait_for_all_tracers()` at the
end of `ask` and `chat`. Only add the call once the missing trace has been
seen.

### 5. Keep tests from sending traces

Add `tests/conftest.py` that sets `LANGSMITH_TRACING=false` before anything is
imported, so `uv run pytest` sends nothing even when `.env` has tracing on.
Add a test that `_run` passes the expected `run_name`, tags and metadata.

### 6. Docs

- README: a short "Tracing" section with the three env vars.
- `TEST_QUESTIONS.md`: point to the LangSmith trace as the way to check which
  tools were called, alongside the stderr lines.

## Privacy

Traces contain everything the model sees and every tool returns:

- league and fantasy team names;
- other managers' names;
- saved preferences;
- the arguments of the write tools.

`SLEEPER_TOKEN` is not traced. It is only used in HTTP headers inside
`src/execution/sleeper_writes.py`, never as a tool argument.
`LANGSMITH_HIDE_INPUTS` and `LANGSMITH_HIDE_OUTPUTS` turn off capture of
inputs and outputs, but doing so removes most of the debugging value.

## Later: evaluations (not in this branch)

Load `TEST_QUESTIONS.md` into a LangSmith dataset and run it with
`langsmith.evaluate()`. The check that the right tool was called can be read
automatically from each trace's tool calls. The "Pass if" column still needs a
person or an LLM judge.

Mind the budget: the free tier allows 50 requests a day, and a full pass uses
about 40. Start this only after tracing has been in use for a while.

## Done when

- With `LANGSMITH_TRACING=true` and a key, `ask` and `chat` traces appear in
  the `fantasy-agent` project, with model turns, tool calls, the refresh span
  and the metadata above.
- With tracing off, or under `pytest`, nothing is sent.
- `uv run pytest` passes.
