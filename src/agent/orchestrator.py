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
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

import config
from src.agent.graph import build_graph
from src.agent.prompts import build_system_prompt
from src.agent.tools import AgentContext, build_tools
from src.ingestion import storage
from src.ingestion.sync import sync_if_stale


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


def _invoke_with_retry(llm, messages, attempts: int = 5):
    delay = 2.0
    for attempt in range(attempts):
        try:
            return llm.invoke(messages)
        except (ValueError, openai.APIError) as e:
            text = str(e).lower()
            # A daily quota won't recover within any retry window - say so now.
            if "per-day" in text or "per day" in text:
                raise AgentError(
                    f"The daily request limit for {config.OPENROUTER_MODEL} is used up. It resets daily; "
                    "adding OpenRouter credits raises it, or set OPENROUTER_MODEL in .env to another model. "
                    "The non-agent commands (lineup, waivers, results, digest...) still work."
                ) from e
            if not isinstance(e, openai.RateLimitError) and not any(marker in text for marker in _TRANSIENT_MARKERS):
                raise
            if attempt == attempts - 1:
                raise AgentError(
                    f"{config.OPENROUTER_MODEL} is unavailable after {attempts} attempts "
                    f"({e}). Free-tier providers overload frequently - retry, or set "
                    f"OPENROUTER_MODEL in .env to a different model."
                ) from e
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


def system_prompt_for(ctx: AgentContext) -> str:
    return build_system_prompt(
        season=ctx.season,
        week=ctx.current_week,
        league_name=ctx.league_name(),
        team_name=ctx.team_name(),
        roster_positions=ctx.roster_positions(),
        scoring_settings=ctx.scoring_settings(),
        other_league_names=ctx.other_league_names(),
        preferences=storage.get_preferences(ctx.conn),
    )


def build_agent(conn: sqlite3.Connection, ctx: AgentContext | None = None, checkpointer=None):
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
        lambda messages: _invoke_with_retry(llm, messages), tools, lambda: system_prompt_for(ctx),
        checkpointer, history_limit=config.AGENT_HISTORY_MESSAGES,
    )
    return graph, tools


def _run(agent, question: str, thread: dict, show_progress: bool = True) -> str:
    """Drive the graph, echoing tool calls as they happen.

    A question can take half a minute across several model round-trips (more
    when the free tier makes us retry), and silence for that long is
    indistinguishable from a hang. Progress goes to stderr so piping stdout
    still yields just the answer.
    """
    final = ""
    for chunk in agent.stream(
        {"messages": [HumanMessage(content=question)]}, config=thread, stream_mode="updates"
    ):
        for update in chunk.values():
            for message in (update or {}).get("messages", []) or []:
                for call in getattr(message, "tool_calls", None) or []:
                    if show_progress:
                        print(f"  ... {call['name']}", file=sys.stderr, flush=True)
                content = getattr(message, "content", "") or ""
                if content and not getattr(message, "tool_calls", None) and message.type == "ai":
                    final = content
    return final


def ask(conn: sqlite3.Connection, question: str, thread_id: str = "cli", show_progress: bool = True) -> str:
    """One-shot question. Re-syncs first if local data is stale, so the
    answer doesn't come from yesterday's rosters and injury designations."""
    sync_if_stale(conn)
    agent, _tools = build_agent(conn)
    return _run(agent, question, {"configurable": {"thread_id": thread_id}}, show_progress)


CHAT_THREAD = "chat"


def chat(conn: sqlite3.Connection, new: bool = False) -> None:
    """Interactive multi-turn session. The conversation is saved to
    config.CONVERSATIONS_DB_PATH, so the next `chat` picks up where this one
    left off (`new=True` starts over). Staleness is re-checked every turn,
    since a session can stay open for hours."""
    sync_if_stale(conn)
    ctx = build_context(conn)
    config.CONVERSATIONS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    checkpointer = SqliteSaver(sqlite3.connect(config.CONVERSATIONS_DB_PATH, check_same_thread=False))
    if new:
        checkpointer.delete_thread(CHAT_THREAD)
    agent, _tools = build_agent(conn, ctx=ctx, checkpointer=checkpointer)
    thread = {"configurable": {"thread_id": CHAT_THREAD}}

    earlier = len(agent.get_state(thread).values.get("messages", []))
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
        if sync_if_stale(conn):
            ctx.reload_state()
        answer = _run(agent, question, thread)
        print(f"\n{answer}\n")
