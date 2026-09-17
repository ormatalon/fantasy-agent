"""LangGraph orchestrator: plan -> call tools -> observe -> explain.

Deliberately thin. The graph's only job is routing; all computation lives
in the tools (see agent/tools.py). Per PLAN.md §2, LangGraph is here for
the stateful multi-turn case — the deterministic CLI subcommands still
call the modules directly and do not route through this graph.
"""

import sqlite3
import sys
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

import config
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools import AgentContext, build_tools
from src.ingestion import storage


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
        except ValueError as e:
            text = str(e).lower()
            if not any(marker in text for marker in _TRANSIENT_MARKERS):
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


def build_agent(conn: sqlite3.Connection):
    """Returns (compiled_graph, tools). Tools are returned too so callers
    (and tests) can inspect what was exposed."""
    if not config.OPENROUTER_API_KEY:
        raise AgentError("Set OPENROUTER_API_KEY in .env first (see .env.example).")

    ctx = build_context(conn)
    tools = build_tools(ctx)

    llm = ChatOpenAI(
        model=config.OPENROUTER_MODEL,
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
        temperature=0,
        timeout=120,
    ).bind_tools(tools)

    def plan(state: MessagesState) -> dict:
        """Decide whether to answer or call a tool."""
        messages = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        return {"messages": [_invoke_with_retry(llm, messages)]}

    graph = StateGraph(MessagesState)
    graph.add_node("plan", plan)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "plan")
    # tools_condition routes to "tools" when the model emitted tool calls,
    # otherwise to END - which is the "explain" exit of the loop.
    graph.add_conditional_edges("plan", tools_condition)
    graph.add_edge("tools", "plan")

    return graph.compile(checkpointer=MemorySaver()), tools


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
    """One-shot question. Returns the agent's final answer."""
    agent, _tools = build_agent(conn)
    return _run(agent, question, {"configurable": {"thread_id": thread_id}}, show_progress)


def chat(conn: sqlite3.Connection) -> None:
    """Interactive multi-turn session; conversation state persists across turns
    via the graph's checkpointer."""
    agent, _tools = build_agent(conn)
    thread = {"configurable": {"thread_id": "chat"}}
    print("Fantasy agent. Ask a question, or Ctrl+C / 'exit' to quit.\n")
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
        answer = _run(agent, question, thread)
        print(f"\n{answer}\n")
