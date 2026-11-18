"""Chat history per league in chat_message (docs/data-contract.md section 1), and the messages sent to the model.

One question is a run of rows: the user row, then per model turn an assistant row and its tool rows, then the
final assistant row (meta has `model_turns`). Rows never hold a key or the model's reasoning text.
"""

import json
import sqlite3

from storage.db import now_iso, tx
from .render import when

WINDOW = 40  # messages sent to the model, cut at question boundaries
ROSTER_TOOLS = {"get_my_roster", "recommend_lineup", "get_waiver_targets", "evaluate_trade", "find_trade_ideas"}


def add(conn: sqlite3.Connection, league_id: str, role: str, content: str, meta: dict) -> int:
    with tx(conn):
        cur = conn.execute(
            "insert into chat_message(league_id, role, content, created_at, meta) values (?,?,?,?,?)",
            (league_id, role, content, now_iso(), json.dumps(meta)),
        )
    return cur.lastrowid


def update_meta(conn: sqlite3.Connection, row_id: int, **changes) -> None:
    meta = json.loads(conn.execute("select meta from chat_message where id = ?", (row_id,)).fetchone()[0])
    with tx(conn):
        conn.execute("update chat_message set meta = ? where id = ?", (json.dumps(meta | changes), row_id))


def delete_question(conn: sqlite3.Connection, question_id: int) -> None:
    with tx(conn):
        conn.execute(
            "delete from chat_message where id = ? or json_extract(meta, '$.question_id') = ?",
            (question_id, question_id),
        )


def clear(conn: sqlite3.Connection, league_id: str) -> None:
    """Start fresh: this league's messages only."""
    with tx(conn):
        conn.execute("delete from chat_message where league_id = ?", (league_id,))


def _rows(conn: sqlite3.Connection, league_id: str) -> list[dict]:
    return [
        {**dict(r), "meta": json.loads(r["meta"] or "{}")}
        for r in conn.execute("select * from chat_message where league_id = ? order by id", (league_id,))
    ]


def public(conn: sqlite3.Connection, league_id: str, running: dict | None) -> dict:
    """GET /api/chat/history: user rows and final assistant rows."""
    messages = []
    for r in _rows(conn, league_id):
        m = r["meta"]
        if r["role"] == "user":
            item = {"id": r["id"], "role": "user", "text": r["content"], "created_at": r["created_at"],
                    "status": m["status"]}
            if m["status"] == "failed":
                item["error"] = m["error"]
            messages.append(item)
        elif r["role"] == "assistant" and "model_turns" in m:
            messages.append({"id": r["id"], "role": "assistant", "text": r["content"], "created_at": r["created_at"],
                             "status": "ok", "sources": m["sources"], "refresh": m["refresh"]})
    return {"league_id": league_id, "running": running, "messages": messages}


def trace(conn: sqlite3.Connection, league_id: str, question_id: int | None = None) -> dict:
    """GET /api/chat/history?detail=trace: every row with its meta."""
    rows = _rows(conn, league_id)
    if question_id is not None:
        rows = [r for r in rows if r["id"] == question_id or r["meta"].get("question_id") == question_id]
    return {"league_id": league_id, "rows": [
        {k: r[k] for k in ("id", "role", "content", "created_at", "meta")} for r in rows
    ]}


def previous_activity_at(conn: sqlite3.Connection, league_id: str, before_id: int) -> str | None:
    """When the chat's previous question last wrote a row (RECENT CHANGES lists roster moves since then)."""
    return conn.execute(
        "select max(created_at) from chat_message where league_id = ? and id < ?", (league_id, before_id)
    ).fetchone()[0]


def summary_line(content: str) -> str:
    return content.splitlines()[0] if content.startswith("SUMMARY:") else content


def model_messages(conn: sqlite3.Connection, league_id: str, question_id: int, roster_changed_at: str | None) -> list[dict]:
    """Earlier answered questions plus the current question so far, as OpenAI chat messages.

    Every tool result of the current question stays in full ("A or B?" calls one tool twice). A result from an
    earlier question is cut to its SUMMARY line when a later result of the same tool follows it.
    A roster-dependent result older than my last roster change is replaced by an outdated marker.
    Failed questions are left out.
    """
    questions, current = [], None
    for r in _rows(conn, league_id):
        if r["role"] == "user":
            current = [r] if r["meta"]["status"] == "ok" or r["id"] == question_id else None
            if current is not None:
                questions.append(current)
        elif current is not None and r["meta"].get("question_id") == current[0]["id"]:
            current.append(r)
    done = [q for q in questions if q[0]["id"] == question_id or "model_turns" in q[-1]["meta"]]
    while len(done) > 1 and sum(len(q) for q in done) > WINDOW:
        done.pop(0)

    rows = [r for q in done for r in q]
    seen, out = set(), []
    for r in reversed(rows):
        if r["role"] == "tool":
            name, content = r["meta"]["name"], r["content"]
            earlier = r["meta"]["question_id"] != question_id
            if earlier and roster_changed_at and name in ROSTER_TOOLS and r["created_at"] <= roster_changed_at:
                content = f"[outdated: my roster changed at {when(roster_changed_at)}; call the tool again]"
            elif earlier and name in seen:
                content = summary_line(content)
            seen.add(name)
            out.append({"role": "tool", "tool_call_id": r["meta"]["tool_call_id"], "content": content})
        elif r["role"] == "assistant":
            msg = {"role": "assistant", "content": r["content"] or None}
            if r["meta"].get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in r["meta"]["tool_calls"]
                ]
            out.append(msg)
        else:
            out.append({"role": "user", "content": r["content"]})
    return out[::-1]
