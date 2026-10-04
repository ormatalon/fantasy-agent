import sqlite3

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from src.agent.graph import build_graph, recent_messages
from src.agent.prompts import build_system_prompt
from src.ingestion import storage


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    return conn


def test_preferences_are_remembered_numbered_and_forgettable():
    conn = make_conn()
    storage.add_preference(conn, "Risk-averse at FLEX")
    storage.add_preference(conn, "Never recommend Dallas players")
    storage.add_preference(conn, "Risk-averse at FLEX")  # duplicate ignored

    assert storage.get_preferences(conn) == ["Risk-averse at FLEX", "Never recommend Dallas players"]
    assert storage.remove_preference(conn, 1) == "Risk-averse at FLEX"
    assert storage.remove_preference(conn, 5) is None
    assert storage.get_preferences(conn) == ["Never recommend Dallas players"]


def test_preferences_reach_the_system_prompt():
    text = build_system_prompt("2026", 4, "L", "T", ["QB"], {"rec": 1}, preferences=["Never recommend Dallas players"])
    assert "USER PREFERENCES" in text
    assert "1. Never recommend Dallas players" in text
    assert "USER PREFERENCES" not in build_system_prompt("2026", 4, "L", "T", ["QB"], {"rec": 1})


def test_history_trim_never_orphans_a_tool_result():
    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
        ToolMessage(content="r", tool_call_id="1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
        AIMessage(content="a2"),
    ]
    trimmed = recent_messages(messages, 4)
    assert [m.content for m in trimmed] == ["q2", "a2"]
    assert recent_messages(messages, 10) == messages


def test_conversation_survives_a_restart(tmp_path):
    db = tmp_path / "conversations.db"
    thread = {"configurable": {"thread_id": "chat"}}
    seen = []

    def fake_llm(messages):
        seen.append([m.content for m in messages[1:]])
        return AIMessage(content="noted")

    first = build_graph(fake_llm, [], lambda: "sys", SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
    first.invoke({"messages": [HumanMessage(content="my kicker is Tucker")]}, config=thread)

    # A new process: fresh graph, same database file.
    second = build_graph(fake_llm, [], lambda: "sys", SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
    second.invoke({"messages": [HumanMessage(content="who is my kicker?")]}, config=thread)

    assert seen[-1] == ["my kicker is Tucker", "noted", "who is my kicker?"]
