"""Setup, settings and league selection (docs/api-contract.md)."""

import json
import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ingestion import sync
from ingestion.sleeper import SleeperError
from storage.db import connect, get_json, get_setting, set_settings, tx
from .errors import ApiError, sleeper_unreachable
from .key_check import base_url, verify
from .models import DEFAULT_MODEL, MODELS

router = APIRouter(prefix="/api")


def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def build_state(conn: sqlite3.Connection) -> dict:
    username = get_setting(conn, "sleeper_username")
    key_set = bool(get_setting(conn, "openrouter_api_key"))
    leagues = get_json(conn, "leagues", [])
    league_id = get_setting(conn, "current_league_id")
    row = conn.execute("select * from league where league_id = ?", (league_id,)).fetchone() if league_id else None
    current = None
    if row:
        week = get_json(conn, "nfl_state", {}).get("week")
        current = {
            "league_id": row["league_id"],
            "name": row["name"],
            "season": row["season"],
            "this_week": week,
            "last_completed_week": week - 1 if week else None,
            "format": json.loads(row["format"]),
        }
    return {
        "username": username,
        "key_set": key_set,
        "model": get_setting(conn, "model", DEFAULT_MODEL),
        "setup_complete": bool(username and current and key_set),
        "needs_league_choice": current is None and sum(l["selectable"] for l in leagues) > 1,
        "leagues": leagues,
        "current_league": current,
        "refresh": {"stored_at": row["fetched_at"] if row else None, "failed": sync.failures(conn)},
    }


def select_league(conn: sqlite3.Connection, league_id: str) -> None:
    try:
        sync.refresh(conn, league_id)
    except SleeperError:
        raise sleeper_unreachable() from None
    set_settings(conn, {"current_league_id": league_id})


@router.get("/state")
def get_state(conn=Depends(db)):
    return build_state(conn)


@router.get("/models")
def get_models():
    return {"models": MODELS}


class SettingsBody(BaseModel):
    sleeper_username: str | None = None
    openrouter_api_key: str | None = None
    model: str | None = None
    openrouter_base_url: str | None = None


@router.put("/settings")
def put_settings(body: SettingsBody, conn=Depends(db)):
    key = (body.openrouter_api_key or "").strip()
    if key:
        verify(key, body.openrouter_base_url or base_url(conn))
    if body.model and body.model not in {m["id"] for m in MODELS}:
        raise ApiError("model_unknown", 422, "That model is not in the list.")

    found = None
    if body.sleeper_username:
        found = _discover(body.sleeper_username.strip())

    values = {}
    if key:
        values["openrouter_api_key"] = key
    if body.model:
        values["model"] = body.model
    if body.openrouter_base_url:
        values["openrouter_base_url"] = body.openrouter_base_url
    if found:
        values.update(
            sleeper_username=found["username"], sleeper_user_id=found["user_id"], leagues=found["leagues"]
        )
    if values:
        set_settings(conn, values)

    if found:
        selectable = [l["league_id"] for l in found["leagues"] if l["selectable"]]
        current = get_setting(conn, "current_league_id")
        if current not in selectable:
            set_settings(conn, {"current_league_id": ""})
            if len(selectable) == 1:
                select_league(conn, selectable[0])
    return {**build_state(conn), "key": "saved" if key else "unchanged"}


@router.delete("/settings/key")
def delete_key(conn=Depends(db)):
    with tx(conn):
        conn.execute("delete from settings where key = 'openrouter_api_key'")
    return build_state(conn)


def _discover(username: str) -> dict:
    try:
        return sync.discover(username)
    except sync.UsernameNotFound:
        raise ApiError("username_not_found", 404, "No Sleeper user named that. Check the spelling.") from None
    except SleeperError:
        raise sleeper_unreachable() from None


@router.get("/leagues")
def get_leagues(username: str | None = None, conn=Depends(db)):
    saved = get_setting(conn, "sleeper_username")
    username = username or saved
    if not username:
        raise ApiError("username_not_found", 404, "No Sleeper user named that. Check the spelling.")
    found = _discover(username)
    if saved and found["username"].lower() == saved.lower():
        set_settings(conn, {"leagues": found["leagues"]})
    return {"username": found["username"], "season": found["season"], "leagues": found["leagues"]}


class LeagueBody(BaseModel):
    league_id: str | None = None
    league_name: str | None = None


def resolve_league(leagues: list[dict], body: LeagueBody) -> dict:
    """By id, or by name: exact (any case), then a unique prefix."""
    if body.league_id:
        match = [l for l in leagues if l["league_id"] == body.league_id]
    else:
        name = (body.league_name or "").strip().lower()
        match = [l for l in leagues if l["name"].lower() == name]
        if not match and name:
            match = [l for l in leagues if l["name"].lower().startswith(name)]
    if len(match) != 1:
        names = [l["name"] for l in leagues]
        raise ApiError(
            "league_not_found",
            404,
            "No league with that name. Your leagues: " + (", ".join(names) or "none") + ".",
            available=names,
        )
    return match[0]


@router.put("/league")
def put_league(body: LeagueBody, conn=Depends(db)):
    league = resolve_league(get_json(conn, "leagues", []), body)
    if not league["selectable"]:
        reason = league["not_selectable_reason"]
        if league["unsupported_reason"]:
            raise ApiError(
                "league_unsupported",
                409,
                f"{league['name']} is a {reason.replace('_', ' ')} league. Only season-long redraft is supported.",
                unsupported_reason=reason,
            )
        raise ApiError("league_not_playable", 409, f"{league['name']} is not in season ({reason}).", reason=reason)
    select_league(conn, league["league_id"])
    return build_state(conn)
