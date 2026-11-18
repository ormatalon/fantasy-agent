"""The backend functions the UI calls, in process. Each opens its own SQLite connection, so each can run in a thread."""

from contextlib import contextmanager
from datetime import timedelta

from agent import chat
from storage import db
from web import panel, setup
from web.models import MODELS


@contextmanager
def conn():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


def state() -> dict:
    with conn() as c:
        return setup.build_state(c)


def models() -> list[dict]:
    return MODELS


def find_leagues(username: str) -> dict:
    with conn() as c:
        return setup.get_leagues(username, c)


def save(username: str, key: str | None, model: str, league_id: str) -> None:
    """Settings first (the key is checked before anything is stored), then the league."""
    with conn() as c:
        setup.put_settings(setup.SettingsBody(sleeper_username=username, openrouter_api_key=key, model=model), c)
        setup.put_league(setup.LeagueBody(league_id=league_id), c)
        if key:
            db.set_settings(c, {"key_saved_at": db.now_iso()})  # a refused key stays locked until a newer one is saved


def key_saved_at() -> str:
    with conn() as c:
        return db.get_setting(c, "key_saved_at", "")


def remove_key() -> None:
    with conn() as c:
        setup.delete_key(c)


def switch_league(league_id: str) -> None:
    with conn() as c:
        setup.put_league(setup.LeagueBody(league_id=league_id), c)


def panel_data(force: bool = False) -> dict:
    """Stored panel, re-pulled when old. force: the Refresh button (Sleeper and every NFL source now)."""
    with conn() as c:
        return panel.panel_after(c, timedelta(0) if force else panel.PANEL_MAX_AGE, force_nfl=force)


def history() -> dict:
    with conn() as c:
        return chat.history(c, panel.current_league_id(c))


def start_fresh() -> None:
    with conn() as c:
        chat.start_fresh(c, panel.current_league_id(c))


def start_question(text: str, retry_of: int | None) -> int:
    with conn() as c:
        return chat.start_question(c, text, retry_of)


def run_question(question_id: int, emit) -> None:
    """Blocking. Ends with an `end` event so the page's loop always stops."""
    try:
        with conn() as c:
            chat.run_question(c, question_id, emit)
    finally:
        emit("end", {})


def running() -> dict | None:
    return chat.running()
