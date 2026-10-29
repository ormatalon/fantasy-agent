"""Agent runtime: context, model wiring, retries, and the ask/chat loops.

Deliberately thin. The graph itself lives in agent/graph.py and only routes;
all computation lives in the tools (see agent/tools.py). Per PLAN.md §2,
LangGraph is here for the stateful multi-turn case — the deterministic CLI
subcommands still call the modules directly and do not route through this.
"""

import sqlite3
import sys
import time

import openai
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

import config
from src.agent.graph import build_graph
from src.agent.prompts import build_system_prompt
from src.agent.tools import ROSTER_TOOLS, AgentContext, build_tools, format_lineup, my_lineup
from src.ingestion import storage
from src.ingestion.sync import log_to_stderr, refresh_before_question


class AgentError(RuntimeError):
    pass


# OpenRouter reports upstream provider failures as HTTP 200 with an error
# object in the response body, which langchain surfaces as a plain
# ValueError - so the OpenAI SDK's own max_retries never sees them. Free
# tiers hit these often enough that retrying is normal operation.
_TRANSIENT_MARKERS = (
    "temporarily overloaded",
    "provider_unavailable",
    "rate-limited",
    "rate limited",
    "timeout",
    "502",
    "503",
    "429",
)


# Kept to one line: it is shown as-is in the web page and the terminal. The
# provider's own error is the AgentError's __cause__ (logged by the web app).
_PICK_ANOTHER = "Pick another model (Settings, or OPENROUTER_MODEL)."


def _invoke_with_retry(llm, messages, attempts: int = 5):
    delay = 2.0
    for attempt in range(attempts):
        try:
            return llm.invoke(messages)
        except (ValueError, openai.APIError) as e:
            text = str(e).lower()
            # A daily quota won't recover within any retry window - say so now.
            if "per-day" in text or "per day" in text:
                raise AgentError(f"Daily limit reached for {config.OPENROUTER_MODEL}. {_PICK_ANOTHER}") from e
            if not isinstance(e, openai.RateLimitError) and not any(marker in text for marker in _TRANSIENT_MARKERS):
                # An API error that retrying won't fix, e.g. the model's only
                # provider is down (OpenRouter passes its 404 through).
                if isinstance(e, openai.APIError):
                    raise AgentError(f"Model not available: {config.OPENROUTER_MODEL}. {_PICK_ANOTHER}") from e
                raise
            if attempt == attempts - 1:
                raise AgentError(f"Model busy: {config.OPENROUTER_MODEL}. Retry, or pick another model.") from e
            time.sleep(delay)
            delay *= 2


def build_context(conn: sqlite3.Connection) -> AgentContext:
    league_id = storage.get_state(conn, "active_league_id")
    user_id = storage.get_state(conn, "active_user_id")
    season = storage.get_state(conn, "active_season")
    week = storage.get_state(conn, "current_week")
    if not (league_id and user_id and season and week):
        raise AgentError("No local league data - run `sync` first.")
    return AgentContext(
        conn=conn, league_id=league_id, user_id=user_id, season=season, current_week=int(week)
    )


def roster_changed_at(ctx: AgentContext) -> str | None:
    return storage.get_state(ctx.conn, f"roster_changed_at:{ctx.league_id}")


def system_prompt_for(ctx: AgentContext, in_app: bool = False) -> str:
    lineup = my_lineup(ctx)
    changed_at = roster_changed_at(ctx)
    change = storage.get_state(ctx.conn, f"roster_change:{ctx.league_id}")
    return build_system_prompt(
        season=ctx.season,
        week=ctx.current_week,
        league_name=ctx.league_name(),
        fantasy_team_name=ctx.fantasy_team_name(),
        roster_positions=ctx.roster_positions(),
        scoring_settings=ctx.scoring_settings(),
        other_league_names=ctx.other_league_names(),
        preferences=storage.get_preferences(ctx.conn),
        my_roster=format_lineup(ctx.conn, lineup) if lineup else None,
        roster_refreshed_at=storage.get_state(ctx.conn, "rosters_synced_at"),
        roster_change=(changed_at, change) if changed_at and change else None,
        in_app=in_app,
    )


def build_agent(conn: sqlite3.Connection, ctx: AgentContext | None = None, checkpointer=None, in_app: bool = False):
    """Returns (compiled_graph, tools). Tools are returned too so callers
    (and tests) can inspect what was exposed."""
    if not config.OPENROUTER_API_KEY:
        raise AgentError("Set OPENROUTER_API_KEY in .env first (see .env.example).")

    ctx = ctx or build_context(conn)
    tools = build_tools(ctx)

    llm = ChatOpenAI(
        model=config.OPENROUTER_MODEL,
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
        temperature=0,
        timeout=120,
    ).bind_tools(tools)

    graph = build_graph(
        lambda messages: _invoke_with_retry(llm, messages), tools, lambda: system_prompt_for(ctx, in_app),
        checkpointer, history_limit=config.AGENT_HISTORY_MESSAGES,
        roster_changed_at=lambda: roster_changed_at(ctx), roster_tools=ROSTER_TOOLS,
    )
    return graph, tools


def run_config(ctx: AgentContext, command: str, thread_id: str) -> dict:
    """Graph config: the checkpointer thread, plus the name, tags and
    metadata LangSmith shows on the trace when LANGSMITH_TRACING is on."""
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": command,
        "tags": [command, config.OPENROUTER_MODEL],
        # LangGraph adds thread_id to the metadata itself (LangSmith's Threads view).
        "metadata": {
            "league_id": ctx.league_id,
            "season": ctx.season,
            "week": ctx.current_week,
            "fantasy_team_name": ctx.fantasy_team_name(),
            "model": config.OPENROUTER_MODEL,
        },
    }


STOPPED = "Stopped."


def describe_call(call: dict) -> str:
    """A tool call in plain words, for progress messages: get_projections
    with position RB -> "checking projections (RB)"."""
    name = call["name"]
    words = f"checking {name.removeprefix('get_')}" if name.startswith("get_") else name
    args = ", ".join(str(v) for v in (call.get("args") or {}).values() if v not in ("", 0, None))
    return words.replace("_", " ") + (f" ({args})" if args else "")


def _close_stopped_turn(agent, thread: dict) -> None:
    """End a stopped turn so the conversation stays valid: answer any tool
    calls left open (the model rejects calls without results), then add a
    final reply, as if the plan node had written it."""
    messages = agent.get_state(thread).values.get("messages", [])
    open_calls = getattr(messages[-1], "tool_calls", None) or []  # never empty: the question is stored first
    closing = [ToolMessage(content=STOPPED, tool_call_id=c["id"], name=c["name"]) for c in open_calls]
    agent.update_state(thread, {"messages": [*closing, AIMessage(content=STOPPED)]}, as_node="plan")


def _run(
    agent, question: str, thread: dict, show_progress: bool = True,
    on_step=lambda _step: None, should_stop=lambda: False,
) -> str:
    """Drive the graph, echoing tool calls as they happen.

    A question can take half a minute across several model round-trips (more
    when the free tier makes us retry), and silence for that long is
    indistinguishable from a hang. Progress goes to stderr so piping stdout
    still yields just the answer, and to `on_step` for the web page.

    `should_stop` is checked after each step (a model call or a round of
    tools), so a stop takes effect once the current step finishes.
    """
    final = ""
    for chunk in agent.stream(
        # asked_at lets the graph tell which earlier results predate a roster
        # change. additional_kwargs on a user message aren't sent to the model.
        {"messages": [HumanMessage(content=question, additional_kwargs={"asked_at": storage.now_iso()})]},
        config=thread, stream_mode="updates",
    ):
        more_to_do = False
        for node, update in chunk.items():
            for message in (update or {}).get("messages", []) or []:
                calls = getattr(message, "tool_calls", None) or []
                for call in calls:
                    if show_progress:
                        print(f"  ... {call['name']}", file=sys.stderr, flush=True)
                if calls:
                    on_step(", ".join(describe_call(c) for c in calls))
                    more_to_do = True
                content = getattr(message, "content", "") or ""
                if content and not calls and message.type == "ai":
                    final = content
            if node == "tools":
                more_to_do = True
        if more_to_do and should_stop():
            _close_stopped_turn(agent, thread)
            return STOPPED
    return final


def ask(conn: sqlite3.Connection, question: str, thread_id: str = "cli", show_progress: bool = True) -> str:
    """One-shot question. Re-syncs first if local data is stale, otherwise
    re-pulls rosters, so the answer reflects moves just made in Sleeper."""
    refresh_before_question(conn)
    ctx = build_context(conn)
    agent, _tools = build_agent(conn, ctx=ctx)
    return _run(agent, question, run_config(ctx, "ask", thread_id), show_progress)


CHAT_THREAD = "chat"


def open_chat_agent(conn: sqlite3.Connection, new: bool = False, in_app: bool = False):
    """Returns (agent, ctx) for the saved `chat` conversation, shared by the
    CLI and the web app. History lives in config.CONVERSATIONS_DB_PATH, so a
    later session picks up where this one left off (`new=True` starts over).
    No refresh here: chat_turn refreshes before every question, and opening
    the web page must not wait on a sync."""
    ctx = build_context(conn)
    config.CONVERSATIONS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    checkpointer = SqliteSaver(sqlite3.connect(config.CONVERSATIONS_DB_PATH, check_same_thread=False))
    if new:
        checkpointer.delete_thread(CHAT_THREAD)
    agent, _tools = build_agent(conn, ctx=ctx, checkpointer=checkpointer, in_app=in_app)
    return agent, ctx


def chat_turn(
    conn: sqlite3.Connection, agent, ctx: AgentContext, question: str, show_progress: bool = True,
    on_step=lambda _step: None, should_stop=lambda: False,
) -> str:
    """One question in the saved conversation. Staleness is re-checked and
    rosters re-pulled every turn, since a session can stay open for hours and
    moves are made in Sleeper between questions. `on_step` gets each
    operation in plain words; `should_stop` can end the turn early."""

    def log(message: str) -> None:
        log_to_stderr(message)
        on_step(message.rstrip("."))

    on_step("syncing rosters from Sleeper")
    # Only a full sync can change league, season or week. A roster refresh
    # leaves the (league-wide) projection caches valid.
    if refresh_before_question(conn, log=log):
        ctx.reload_state()
    on_step("asking the model")
    # Built per turn: a full sync above can change the week.
    return _run(
        agent, question, run_config(ctx, "chat", CHAT_THREAD), show_progress,
        on_step=on_step, should_stop=should_stop,
    )


def chat(conn: sqlite3.Connection, new: bool = False) -> None:
    """Interactive multi-turn session in the terminal."""
    agent, ctx = open_chat_agent(conn, new=new)
    earlier = len(agent.get_state({"configurable": {"thread_id": CHAT_THREAD}}).values.get("messages", []))
    print("Fantasy agent. Ask a question, or Ctrl+C / 'exit' to quit.")
    if earlier:
        print(f"Resuming your conversation ({earlier} earlier messages). `chat --new` starts fresh.")
    print()
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            return
        print(f"\n{chat_turn(conn, agent, ctx, question)}\n")
