"""The LangGraph graph: plan -> tools -> plan ... -> answer.

Kept separate from orchestrator.py (context, retries, CLI loops) so the
graph's shape can be read, tested and drawn on its own:

    uv run python -m src.interface.cli graph
"""

from datetime import datetime
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition


def recent_messages(messages: list, limit: int) -> list:
    """The last `limit` messages, cut back to start on a user message so a
    tool result is never sent without the tool call that produced it."""
    if len(messages) <= limit:
        return messages
    start = len(messages) - limit
    while start < len(messages) and not isinstance(messages[start], HumanMessage):
        start += 1
    return messages[start:] or messages[-1:]


OUTDATED_RESULT = "[outdated: my roster changed after this result - see MY ROSTER in the system prompt]"


def hide_outdated_results(messages: list, changed_at: str | None, tool_names: frozenset[str]) -> list:
    """Blank out results of `tool_names` from earlier questions asked before
    `changed_at` (when my roster last changed), so the model can't answer from
    a roster that no longer exists. The current question's results are never
    touched, nor is anything when the roster hasn't changed. A question
    without an `asked_at` (stored before it existed) counts as older."""
    if not changed_at or not tool_names:
        return messages
    changed = datetime.fromisoformat(changed_at)
    last_question = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    out, outdated = [], False
    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage):
            asked_at = m.additional_kwargs.get("asked_at")
            outdated = i < last_question and (not asked_at or datetime.fromisoformat(asked_at) < changed)
        elif outdated and isinstance(m, ToolMessage) and m.name in tool_names:
            m = m.model_copy(update={"content": OUTDATED_RESULT})
        out.append(m)
    return out


def build_graph(
    invoke_llm: Callable,
    tools: list,
    system_prompt: Callable[[], str],
    checkpointer=None,
    history_limit: int = 40,
    roster_changed_at: Callable[[], str | None] = lambda: None,
    roster_tools: frozenset[str] = frozenset(),
):
    """`system_prompt` is called on every model turn, so a prompt that depends
    on live state (week, active league) is never stale mid-conversation. It is
    prepended per call, not stored in the conversation state. Only the last
    `history_limit` stored messages are sent to the model, with results of
    `roster_tools` hidden if they predate `roster_changed_at()` (what's sent
    only; the stored conversation keeps them)."""

    def plan(state: MessagesState) -> dict:
        """Decide whether to answer or call a tool."""
        history = hide_outdated_results(
            recent_messages(state["messages"], history_limit), roster_changed_at(), roster_tools
        )
        messages = [SystemMessage(content=system_prompt()), *history]
        return {"messages": [invoke_llm(messages)]}

    graph = StateGraph(MessagesState)
    graph.add_node("plan", plan)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "plan")
    # tools_condition routes to "tools" when the model emitted tool calls,
    # otherwise to END - which is the "explain" exit of the loop.
    graph.add_conditional_edges("plan", tools_condition)
    graph.add_edge("tools", "plan")
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def draw_mermaid() -> str:
    """Mermaid source for the graph. Needs no API key or synced league: the
    tools are only defined, never invoked."""
    from src.agent.tools import build_tools

    return build_graph(lambda _m: None, build_tools(None), lambda: "").get_graph().draw_mermaid()
