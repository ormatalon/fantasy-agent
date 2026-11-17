"""The question loop and the functions the chat endpoints call (docs/api-contract.md, Chat).

    question_id = start_question(conn, text, retry_of)   # checks before the stream opens; raises ApiError
    run_question(conn, question_id, emit)                # emit(event, payload): start, step, answer, error
    history(conn, league_id) / trace(conn, league_id, question_id) / start_fresh(conn, league_id)

run_question never raises: a failure is stored on the question and sent as the `error` event. Run it in a
worker thread with its own connection, so a closed browser connection does not stop the question.
"""

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Callable

from langsmith import traceable

from ingestion import refresh, sync
from ingestion.sleeper import SleeperError
from storage.db import get_setting, now_iso
from web.errors import ApiError, sleeper_unreachable
from web.key_check import base_url
from web.models import DEFAULT_MODEL
from . import data, history as chat_history, llm, tools
from .context import build_context
from .prompt import system_prompt

log = logging.getLogger(__name__)

MAX_MODEL_TURNS = 6
Emit = Callable[[str, dict], None]

_lock = threading.Lock()
_running: dict | None = None


def running() -> dict | None:
    """The question in progress, {"question_id", "started_at"}, or None."""
    return _running and {"question_id": _running["question_id"], "started_at": _running["started_at"]}


def error_object(e: ApiError) -> dict:
    return {"code": e.code, "message": e.message, **e.extra}


def internal_error() -> ApiError:
    return ApiError("internal_error", 500, "Something went wrong, so there is no answer. Nothing was changed in Sleeper.",
                    question_kept=True)


def _fail(conn: sqlite3.Connection, question_id: int, e: ApiError) -> None:
    chat_history.update_meta(conn, question_id, status="failed", error=error_object(e))


def start_question(conn: sqlite3.Connection, text: str, retry_of: int | None = None) -> int:
    """Store the question and run the checks that come before the stream (HTTP errors).

    On a check failure the question is stored as failed (kept) and the ApiError is raised; any other failure is
    stored and raised as internal_error. Either way no question is left running.
    """
    global _running
    league_id = get_setting(conn, "current_league_id")
    if not league_id:
        raise ApiError("no_league", 409, "Choose a league first.", question_kept=True)
    with _lock:
        if _running:
            raise ApiError("question_in_progress", 409, "A question is already running.")
        question_id = chat_history.add(conn, league_id, "user", text, {"status": "ok"})
        _running = {"question_id": question_id, "started_at": now_iso(), "t0": time.monotonic()}
    checked = False
    try:
        fmt = data.league_format(conn, league_id)
        if not fmt["supported"]:
            reason = fmt["unsupported_reason"]
            raise ApiError("league_unsupported", 409,
                           f"This is a {reason.replace('_', ' ')} league. Only season-long redraft is supported.",
                           unsupported_reason=reason, question_kept=True)
        key = get_setting(conn, "openrouter_api_key")
        if not key:
            raise ApiError("key_missing", 409, "Add your OpenRouter key to ask questions.", question_kept=True)
        llm.check_quota(base_url(conn), key, get_setting(conn, "model", DEFAULT_MODEL))
        checked = True
    except ApiError as e:
        _fail(conn, question_id, e)
        raise
    except Exception as exc:  # e.g. an HTML page where JSON was expected (captive portal)
        log.exception("checks for question %s failed", question_id)
        e = internal_error()
        _fail(conn, question_id, e)
        raise e from exc
    finally:
        if not checked:
            with _lock:
                _running = None
    if retry_of:
        chat_history.delete_question(conn, retry_of)
    return question_id


def run_question(conn: sqlite3.Connection, question_id: int, emit: Emit) -> None:
    """Answer an accepted question, emitting progress. Always ends with an `answer` or `error` event."""
    global _running
    league_id = conn.execute("select league_id from chat_message where id = ?", (question_id,)).fetchone()[0]
    model = get_setting(conn, "model", DEFAULT_MODEL)
    season = data.league_format(conn, league_id)["season"]
    try:
        _answer(conn, question_id, league_id, model, emit,
                langsmith_extra={"metadata": {"model": model, "league_id": league_id, "season": season,
                                              "week": data.weeks(conn)[0]}})
    finally:
        with _lock:
            _running = None


class _Steps:
    def __init__(self, emit: Emit, t0: float):
        self.emit, self.t0, self.n = emit, t0, 0

    def elapsed(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    @contextmanager
    def step(self, kind: str, label: str, tool: str | None = None, model_turn: int | None = None):
        self.n += 1
        event = {"id": self.n, "kind": kind, "label": label, "tool": tool, "model_turn": model_turn}
        self.emit("step", {**event, "status": "running", "elapsed_ms": self.elapsed()})
        outcome = {"status": "done"}
        try:
            yield outcome
        except BaseException:
            self.emit("step", {**event, "status": "error", "elapsed_ms": self.elapsed()})
            raise
        self.emit("step", {**event, "status": outcome["status"], "elapsed_ms": self.elapsed()})


def _merge_sources(into: dict, sources: list[dict]) -> None:
    """One entry per label, the oldest as_of kept."""
    for s in sources:
        if s["as_of"] and (s["label"] not in into or s["as_of"] < into[s["label"]]):
            into[s["label"]] = s["as_of"]


def _refresh_object(conn: sqlite3.Connection, league_id: str) -> dict:
    stored = [data.league_row(conn, league_id)["fetched_at"]]
    week, _ = data.weeks(conn)
    mine = conn.execute(
        "select fetched_at from matchup where league_id = ? and week = ? and roster_id = ?",
        (league_id, week, data.my_roster_id(conn, league_id)),
    ).fetchone()
    if mine:
        stored.append(mine["fetched_at"])
    return {"stored_at": min(stored), "failed": sync.failures(conn)}


def _roster_changed_at(conn: sqlite3.Connection, league_id: str) -> str | None:
    return conn.execute(
        "select max(detected_at) from roster_diff where league_id = ? and roster_id = ?",
        (league_id, data.my_roster_id(conn, league_id)),
    ).fetchone()[0]


@traceable(name="tool", run_type="tool")
def _tool(ctx: tools.Ctx, name: str, arguments: dict) -> tools.ToolResult:
    return tools.run_tool(ctx, name, arguments)


@traceable(name="chat_question", run_type="chain")
def _answer(conn: sqlite3.Connection, question_id: int, league_id: str, model: str, emit: Emit) -> None:
    started = _running or {"started_at": now_iso(), "t0": time.monotonic()}
    steps = _Steps(emit, started["t0"])
    emit("start", {"question_id": question_id, "started_at": started["started_at"], "max_model_turns": MAX_MODEL_TURNS})
    try:
        with steps.step("refresh", "Refreshing data"):
            try:
                refresh.before_question(conn, league_id)
            except SleeperError:
                e = sleeper_unreachable()
                raise ApiError(e.code, e.status, e.message, question_kept=True) from None
        key, base = get_setting(conn, "openrouter_api_key"), base_url(conn)
        client = llm.client(base, key)
        ctx = tools.Ctx(conn, league_id)
        since = chat_history.previous_activity_at(conn, league_id, question_id)
        changed = _roster_changed_at(conn, league_id)
        sources, usage = {}, {}
        # the context block (MY ROSTER) is supplied data too, so an answer from it still names its sources
        _merge_sources(sources, [tools.rosters_source(conn, league_id), tools.catalog_source(conn)])
        for turn in range(1, MAX_MODEL_TURNS + 1):
            context = build_context(conn, ctx.league_id, since)
            messages = [{"role": "system", "content": f"{system_prompt()}\nCONTEXT\n{context}"},
                        *chat_history.model_messages(conn, league_id, question_id, changed)]
            last = turn == MAX_MODEL_TURNS
            with steps.step("model", "Thinking", model_turn=turn):
                msg, turn_usage = llm.complete(client, model, messages, None if last else tools.specs(), base, key)
            for k, v in (turn_usage or {}).items():
                usage[k] = usage.get(k, 0) + (v or 0)
            if not msg["tool_calls"] and (not msg["content"] or "<tool_call>" in msg["content"]):
                continue  # no answer (empty, or a tool call written as text on the no-tools turn)
            meta = {"question_id": question_id, "model": model, "turn": turn, "context": context, "tool_calls": []}
            row_id = chat_history.add(conn, league_id, "assistant", msg["content"], meta)
            if not msg["tool_calls"]:
                answer_sources = [{"label": k, "as_of": v} for k, v in sources.items()]
                refresh_state = _refresh_object(conn, ctx.league_id)
                chat_history.update_meta(conn, row_id, sources=answer_sources, refresh=refresh_state, model_turns=turn,
                                         usage=usage or None)
                emit("answer", {"message_id": row_id, "text": msg["content"], "sources": answer_sources,
                                "refresh": refresh_state, "model": model, "model_turns": turn})
                return
            calls = []
            for call in msg["tool_calls"]:
                tool = tools.TOOLS_BY_NAME.get(call["name"])
                with steps.step("tool", tool.label if tool else "Using a tool", tool=call["name"]) as outcome:
                    try:
                        arguments = json.loads(call["arguments"] or "{}")
                        result = _tool(ctx, call["name"], arguments)
                    except json.JSONDecodeError:
                        arguments = {"unparsed": call["arguments"]}
                        result = tools.error("the tool arguments were not valid JSON; nothing was run")
                    if not result.ok:
                        outcome["status"] = "error"
                _merge_sources(sources, result.sources)
                tool_row = chat_history.add(conn, league_id, "tool", result.text, {
                    "question_id": question_id, "tool_call_id": call["id"], "name": call["name"],
                    "arguments": arguments, "ok": result.ok})
                calls.append({"id": call["id"], "name": call["name"], "arguments": arguments, "result_id": tool_row})
            chat_history.update_meta(conn, row_id, tool_calls=calls)
        raise ApiError("too_many_turns", 502, "This question needed too many steps. Try asking a narrower question.",
                       max_model_turns=MAX_MODEL_TURNS, question_kept=True)
    except ApiError as e:
        _fail(conn, question_id, e)
        emit("error", error_object(e))
    except Exception:
        log.exception("question %s failed", question_id)
        e = internal_error()
        _fail(conn, question_id, e)
        emit("error", error_object(e))


def history(conn: sqlite3.Connection, league_id: str) -> dict:
    return chat_history.public(conn, league_id, running())


def trace(conn: sqlite3.Connection, league_id: str, question_id: int | None = None) -> dict:
    return chat_history.trace(conn, league_id, question_id)


def start_fresh(conn: sqlite3.Connection, league_id: str) -> None:
    chat_history.clear(conn, league_id)
