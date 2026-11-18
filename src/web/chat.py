"""Chat endpoints (docs/api-contract.md, Chat). The question loop is agent.chat; this is HTTP and SSE only."""

import json
import queue
import threading

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from agent import chat
from storage.db import connect
from .errors import ApiError
from .panel import current_league_id
from .setup import db

router = APIRouter(prefix="/api/chat")

KEEPALIVE_SECONDS = 15
END = object()


class QuestionBody(BaseModel):
    text: str
    retry_of: int | None = None


def _worker(question_id: int, events: queue.Queue) -> None:
    """Answer on its own connection. A closed browser connection does not stop it."""
    conn = connect()
    try:
        chat.run_question(conn, question_id, lambda event, payload: events.put((event, payload)))
    finally:
        conn.close()
        events.put(END)


def _sse(events: queue.Queue):
    while True:
        try:
            item = events.get(timeout=KEEPALIVE_SECONDS)
        except queue.Empty:
            yield ": keepalive\n\n"
            continue
        if item is END:
            return
        event, payload = item
        yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post("")
def post_chat(body: QuestionBody):
    conn = connect()
    try:
        question_id = chat.start_question(conn, body.text, body.retry_of)  # raises the ApiErrors before the stream
    finally:
        conn.close()
    events: queue.Queue = queue.Queue()
    threading.Thread(target=_worker, args=(question_id, events), daemon=True).start()
    return StreamingResponse(_sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/history")
def get_history(detail: str | None = None, question_id: int | None = None, league_id: str | None = None,
                conn=Depends(db)):
    league_id = league_id or current_league_id(conn)
    if detail == "trace":
        return chat.trace(conn, league_id, question_id)
    if detail:
        raise ApiError("bad_request", 422, "detail can only be 'trace'.")
    return chat.history(conn, league_id)


@router.delete("/history", status_code=204)
def delete_history(league_id: str | None = None, conn=Depends(db)):
    chat.start_fresh(conn, league_id or current_league_id(conn))
    return Response(status_code=204)
