"""My fantasy team panel (docs/api-contract.md). Reads stored data; refreshes it when old."""

import json
import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Depends

from ingestion import sync
from ingestion.sleeper import SleeperError
from storage.db import get_json, get_setting
from .errors import ApiError, sleeper_unreachable
from .setup import db

router = APIRouter(prefix="/api")

PANEL_MAX_AGE = timedelta(seconds=10)  # polling the panel must not hammer Sleeper
FANTASY_STATUS = {"starter": "mine_starter", "bench": "mine_bench", "taxi": "mine_bench", "reserve": "mine_ir"}


def current_league_id(conn: sqlite3.Connection) -> str:
    league_id = get_setting(conn, "current_league_id")
    if not league_id:
        raise ApiError("no_league", 409, "Choose a league first.")
    return league_id


def player_ref(r: sqlite3.Row, slot: str) -> dict:
    """PlayerRef from a roster_player row joined with the catalog (catalog columns are null if unknown)."""
    return {
        "player_id": r["player_id"],
        "name": r["full_name"] or r["player_id"],
        "position": r["position"],
        "nfl_team": r["nfl_team"],
        "injury_status": r["injury_status"],
        "fantasy_status": FANTASY_STATUS[r["role"]],
        "lineup_slot": slot if r["role"] == "starter" else None,
        "game": None,  # filled by the NFL games source (stage 2.2)
    }


def build_panel(conn: sqlite3.Connection, league_id: str) -> dict:
    league = conn.execute("select * from league where league_id = ?", (league_id,)).fetchone()
    week = get_json(conn, "nfl_state", {}).get("week")
    user_id = get_setting(conn, "sleeper_user_id")
    teams = conn.execute(
        "select * from fantasy_team where league_id = ? order by wins desc, points_for desc", (league_id,)
    ).fetchall()
    me = next((t for t in teams if t["owner_id"] == user_id), None)
    if me is None:
        raise ApiError("no_league", 409, "Your fantasy team was not found in this league.")

    starting = [p for p in json.loads(league["roster_positions"]) if p != "BN"]
    rows = conn.execute(
        "select rp.player_id, rp.role, rp.slot_index, p.* from roster_player rp "
        "left join player p on p.player_id = rp.player_id "
        "where rp.league_id = ? and rp.roster_id = ? order by rp.rowid",
        (league_id, me["roster_id"]),
    ).fetchall()
    points = {
        r["player_id"]: r["points"]
        for r in conn.execute(
            "select player_id, points from matchup_player where league_id = ? and week = ? and roster_id = ?",
            (league_id, week, me["roster_id"]),
        )
    }

    def slot(label: str, role: str, r: sqlite3.Row | None) -> dict:
        if r is None:
            return {"slot": label, "role": role, "player": None, "points": None}
        return {
            "slot": label,
            "role": role,
            "player": player_ref(r, label),
            "points": None if r["role"] == "reserve" else points.get(r["player_id"]),
        }

    by_index = {r["slot_index"]: r for r in rows if r["role"] == "starter"}
    slots = [slot(label, "starter", by_index.get(i)) for i, label in enumerate(starting)]
    labels = {"bench": "BN", "reserve": "IR", "taxi": "TAXI"}
    for role in ("bench", "reserve", "taxi"):
        slots += [slot(labels[role], role if role != "taxi" else "bench", r) for r in rows if r["role"] == role]

    record = f"{me['wins']}-{me['losses']}" + (f"-{me['ties']}" if me["ties"] else "")
    mine = conn.execute(
        "select * from matchup where league_id = ? and week = ? and roster_id = ?", (league_id, week, me["roster_id"])
    ).fetchone()
    matchup = None
    if mine and mine["matchup_id"] is not None:
        opp = conn.execute(
            "select m.points, t.name from matchup m join fantasy_team t "
            "on t.league_id = m.league_id and t.roster_id = m.roster_id "
            "where m.league_id = ? and m.week = ? and m.matchup_id = ? and m.roster_id != ?",
            (league_id, week, mine["matchup_id"], me["roster_id"]),
        ).fetchone()
        if opp:
            # state (not_started, live, final) needs game states from stage 2.2
            matchup = {
                "opponent_fantasy_team": opp["name"],
                "my_score": mine["points"],
                "opponent_score": opp["points"],
                "state": None,
            }

    catalog = conn.execute("select max(fetched_at) from player").fetchone()[0]
    stored = [league["fetched_at"]] + ([mine["fetched_at"]] if mine else [])
    return {
        "league_id": league_id,
        "season": league["season"],
        "week": week,
        "fantasy_team": {"name": me["name"], "record": record, "rank": teams.index(me) + 1},
        "slots": slots,
        "matchup": matchup,
        "as_of": {
            "rosters": league["fetched_at"],
            "matchup": mine["fetched_at"] if mine else None,
            "games": None,
            "injuries": catalog,
        },
        "refresh": {"stored_at": min(stored), "failed": sync.failures(conn)},
    }


def panel_after(conn: sqlite3.Connection, max_age: timedelta) -> dict:
    league_id = current_league_id(conn)
    try:
        sync.ensure_fresh(conn, league_id, max_age)
    except SleeperError:
        raise sleeper_unreachable() from None
    return build_panel(conn, league_id)


@router.get("/panel")
def get_panel(conn=Depends(db)):
    return panel_after(conn, PANEL_MAX_AGE)


@router.post("/refresh")
def post_refresh(conn=Depends(db)):
    return panel_after(conn, timedelta(0))
