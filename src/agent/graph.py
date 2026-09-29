"""The LangGraph graph: plan -> tools -> plan ... -> answer.

Kept separate from orchestrator.py (context, retries, CLI loops) so the
graph's shape can be read, tested and drawn on its own:

    uv run python -m src.interface.cli graph
"""

from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage
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


def build_graph(
    invoke_llm: Callable, tools: list, system_prompt: Callable[[], str], checkpointer=None, history_limit: int = 40
):
    """`system_prompt` is called on every model turn, so a prompt that depends
    on live state (week, active league) is never stale mid-conversation. It is
    prepended per call, not stored in the conversation state. Only the last
    `history_limit` stored messages are sent to the model."""

    def plan(state: MessagesState) -> dict:
        """Decide whether to answer or call a tool."""
        messages = [SystemMessage(content=system_prompt()), *recent_messages(state["messages"], history_limit)]
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
