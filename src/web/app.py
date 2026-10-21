"""Web app: a FastAPI backend for the setup form, league picker and chat,
plus the static Next.js page (web/out) at /.

Single user, local only: one SQLite connection and one agent, used one
request at a time under a lock.

    uv run uvicorn src.web.app:app --port 8000
"""

import threading

from dotenv import set_key
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

import config
from src.agent.orchestrator import CHAT_THREAD, AgentError, chat_turn, open_chat_agent
from src.ingestion import storage
from src.ingestion.sleeper import SleeperClient
from src.ingestion.sleeper_graphql import SleeperError
from src.ingestion.sync import log_to_stderr, run_sync

app = FastAPI(title="Fantasy Agent")
_lock = threading.Lock()
# conn, and once built: agent and ctx (dropped when settings or league change).
_session: dict = {}
# Read and set without the lock, so the page can stop a question or see its
# last step while it runs.
_stop = threading.Event()
_progress = {"step": ""}


def _conn():
    if "conn" not in _session:
        _session["conn"] = storage.get_connection(config.DB_PATH)
    return _session["conn"]


def _agent(new: bool = False):
    if new or "agent" not in _session:
        _session["agent"], _session["ctx"] = open_chat_agent(_conn(), new=new, in_app=True)
    return _session["agent"], _session["ctx"]


def _forget_agent() -> None:
    _session.pop("agent", None)
    _session.pop("ctx", None)


@app.exception_handler(AgentError)
@app.exception_handler(SleeperError)
def _user_error(_request: Request, e: Exception) -> JSONResponse:
    if e.__cause__:
        log_to_stderr(f"{e} Cause: {e.__cause__}")
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(Exception)
def _unexpected_error(_request: Request, e: Exception) -> JSONResponse:
    # The traceback still goes to the server log; the page gets the reason.
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong: {e}"})


@app.get("/api/status")
def status() -> dict:
    with _lock:
        conn = _conn()
        league_id = storage.get_state(conn, "active_league_id")
        league = conn.execute("SELECT name FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
        return {
            "configured": bool(config.SLEEPER_USERNAME and config.OPENROUTER_API_KEY),
            "sleeper_username": config.SLEEPER_USERNAME,
            "openrouter_model": config.OPENROUTER_MODEL,
            "has_openrouter_key": bool(config.OPENROUTER_API_KEY),
            "has_sleeper_token": bool(config.SLEEPER_TOKEN),
            "league": {"league_id": league_id, "name": league["name"]} if league else None,
            "last_synced_at": storage.get_state(conn, "last_synced_at"),
        }


class Settings(BaseModel):
    sleeper_username: str
    # Secrets are never sent back to the page, so blank means "keep".
    openrouter_api_key: str = ""
    sleeper_token: str = ""
    openrouter_model: str = ""


@app.post("/api/settings")
def save_settings(s: Settings) -> dict:
    with _lock:
        values = {
            "SLEEPER_USERNAME": s.sleeper_username.strip(),
            "OPENROUTER_MODEL": s.openrouter_model.strip() or config.DEFAULT_OPENROUTER_MODEL,
            "OPENROUTER_API_KEY": s.openrouter_api_key.strip(),
            "SLEEPER_TOKEN": s.sleeper_token.strip(),
        }
        if values["SLEEPER_USERNAME"] != config.SLEEPER_USERNAME:
            # Another user's leagues: pick a league again.
            storage.set_state(_conn(), "active_league_id", "")
        config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_PATH.touch()
        for key, value in values.items():
            if value or key not in ("OPENROUTER_API_KEY", "SLEEPER_TOKEN"):
                set_key(config.SETTINGS_PATH, key, value)
        config.reload()
        _forget_agent()
    return status()


@app.get("/api/leagues")
def leagues() -> list[dict]:
    if not config.SLEEPER_USERNAME:
        raise SleeperError("Enter your Sleeper username first.")
    with SleeperClient() as client:
        season = config.SLEEPER_SEASON or client.get_nfl_state()["season"]
        user = client.get_user(config.SLEEPER_USERNAME)
        found = client.get_user_leagues(user["user_id"], season)
    return [{"league_id": lg["league_id"], "name": lg.get("name") or lg["league_id"]} for lg in found]


class SyncRequest(BaseModel):
    league_id: str = ""


@app.post("/api/sync")
def sync(req: SyncRequest) -> dict:
    with _lock:
        conn = _conn()
        league_id = req.league_id or storage.get_state(conn, "active_league_id")
        if not league_id:
            raise SleeperError("Pick a league first.")
        run_sync(conn, league_query=league_id, log=log_to_stderr)
        _forget_agent()
    return status()


def _visible(messages: list) -> list[dict]:
    """The user's questions and the agent's answers, without tool traffic."""
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage) and m.content and not m.tool_calls:
            out.append({"role": "agent", "content": m.content})
    return out


@app.get("/api/chat")
def history() -> list[dict]:
    with _lock:
        agent, _ctx = _agent()
        state = agent.get_state({"configurable": {"thread_id": CHAT_THREAD}})
        return _visible(state.values.get("messages", []))


class Question(BaseModel):
    question: str


def _set_step(step: str) -> None:
    _progress["step"] = step


@app.post("/api/chat")
def ask(q: Question) -> dict:
    with _lock:
        # Cleared only once the previous question has finished, so a stop
        # always reaches the question that was running.
        _stop.clear()
        _set_step("starting")
        try:
            agent, ctx = _agent()
            return {"answer": chat_turn(
                _conn(), agent, ctx, q.question.strip(), show_progress=False,
                on_step=_set_step, should_stop=_stop.is_set,
            )}
        finally:
            _set_step("")


@app.post("/api/chat/stop")
def stop() -> dict:
    _stop.set()
    return {"ok": True}


@app.get("/api/chat/progress")
def progress() -> dict:
    """The running question's last operation, in plain words ("" when idle)."""
    return {"step": _progress["step"]}


@app.post("/api/chat/new")
def new_chat() -> dict:
    with _lock:
        _agent(new=True)
    return {"ok": True}


# Last, so /api routes win. check_dir=False lets the API run before `npm run build`.
app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True, check_dir=False), name="web")
