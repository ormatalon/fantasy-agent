"""Read-only queries over the stored league state, shaped as the contract's PlayerRef."""

import json
import sqlite3

from ingestion.nfl import service
from storage.db import get_setting

FANTASY_STATUS = {"starter": "mine_starter", "bench": "mine_bench", "taxi": "mine_bench", "reserve": "mine_ir"}


def league_row(conn: sqlite3.Connection, league_id: str) -> sqlite3.Row:
    return conn.execute("select * from league where league_id = ?", (league_id,)).fetchone()


def league_format(conn: sqlite3.Connection, league_id: str) -> dict:
    return json.loads(league_row(conn, league_id)["format"])


def weeks(conn: sqlite3.Connection) -> tuple[int | None, int | None]:
    """(this week, last completed week): this week is the next week to be played (the NFL facts rule)."""
    week = service.this_week(conn)
    return week, (week - 1 if week else None)


def fantasy_teams(conn: sqlite3.Connection, league_id: str) -> list[sqlite3.Row]:
    """Standings order, as the panel ranks them."""
    return conn.execute(
        "select * from fantasy_team where league_id = ? order by wins desc, points_for desc", (league_id,)
    ).fetchall()


def my_roster_id(conn: sqlite3.Connection, league_id: str) -> int | None:
    row = conn.execute(
        "select roster_id from fantasy_team where league_id = ? and owner_id = ?",
        (league_id, get_setting(conn, "sleeper_user_id")),
    ).fetchone()
    return row["roster_id"] if row else None


def record(team: sqlite3.Row) -> str:
    return f"{team['wins']}-{team['losses']}" + (f"-{team['ties']}" if team["ties"] else "")


def opponent(conn: sqlite3.Connection, league_id: str, week: int, roster_id: int) -> sqlite3.Row | None:
    """The other fantasy team in my matchup that week (None on a bye or before matchups are stored)."""
    return conn.execute(
        "select t.*, o.points as week_points from matchup m "
        "join matchup o on o.league_id = m.league_id and o.week = m.week and o.matchup_id = m.matchup_id "
        "and o.roster_id != m.roster_id "
        "join fantasy_team t on t.league_id = o.league_id and t.roster_id = o.roster_id "
        "where m.league_id = ? and m.week = ? and m.roster_id = ? and m.matchup_id is not null",
        (league_id, week, roster_id),
    ).fetchone()


class League:
    """Who holds which player in one league, for fantasy status labels, and each player's injury designation."""

    def __init__(self, conn: sqlite3.Connection, league_id: str):
        self.conn, self.league_id = conn, league_id
        self.week = weeks(conn)[0]
        self.me = my_roster_id(conn, league_id)
        self.names = {t["roster_id"]: t["name"] for t in fantasy_teams(conn, league_id)}
        self.slots = [p for p in json.loads(league_row(conn, league_id)["roster_positions"]) if p != "BN"]
        self.held = {
            r["player_id"]: r
            for r in conn.execute(
                "select player_id, roster_id, role, slot_index from roster_player where league_id = ?", (league_id,)
            )
        }
        self.injury_facts: dict[str, dict | None] = {}  # player_id -> injury fact, as read by injury_status

    def injury_status(self, player_id: str) -> str | None:
        """The designation by the NFL facts' precedence (official report, ESPN, Sleeper catalog), as decisions and
        the panel read it; the catalog alone can be most of a day old."""
        if player_id not in self.injury_facts:
            self.injury_facts[player_id] = service.injury_fact(self.conn, player_id)
        fact = self.injury_facts[player_id]
        status = fact["value"]["designation"] if fact else None
        return None if status in (None, "", "None") else status

    def ref(self, p: sqlite3.Row) -> dict:
        """PlayerRef (docs/data-contract.md section 9) from a player catalog row."""
        held = self.held.get(p["player_id"])
        slot = None
        if held is None:
            status = "free_agent"
        elif held["roster_id"] == self.me:
            status = FANTASY_STATUS[held["role"]]
            if held["role"] == "starter":
                slot = self.slots[held["slot_index"]]
        else:
            status = f"fantasy_team:{self.names[held['roster_id']]}"
        return {
            "player_id": p["player_id"],
            "name": p["full_name"] or p["player_id"],
            "position": p["position"],
            "nfl_team": p["nfl_team"],
            "injury_status": self.injury_status(p["player_id"]),
            "fantasy_status": status,
            "lineup_slot": slot,
            "game": service.game_ref(self.conn, p["nfl_team"], self.week) if self.week else None,
        }

    def roster(self, roster_id: int) -> list[dict]:
        """Slots as set in Sleeper: starters in slot order (None = empty), then bench, IR, taxi.

        Each item: {"slot": "QB" | "BN" | "IR" | "TAXI", "player": catalog row or None}.
        """
        rows = self.conn.execute(
            "select rp.role, rp.slot_index, p.* from roster_player rp "
            "join player p on p.player_id = rp.player_id "
            "where rp.league_id = ? and rp.roster_id = ? order by rp.rowid",
            (self.league_id, roster_id),
        ).fetchall()
        by_index = {r["slot_index"]: r for r in rows if r["role"] == "starter"}
        out = [{"slot": label, "player": by_index.get(i)} for i, label in enumerate(self.slots)]
        for role, label in (("bench", "BN"), ("reserve", "IR"), ("taxi", "TAXI")):
            out += [{"slot": label, "player": r} for r in rows if r["role"] == role]
        return out


def player(conn: sqlite3.Connection, player_id: str) -> sqlite3.Row | None:
    return conn.execute("select * from player where player_id = ?", (player_id,)).fetchone()


def catalog_as_of(conn: sqlite3.Connection) -> str | None:
    return conn.execute("select max(fetched_at) from player").fetchone()[0]


def refresh_ok_at(conn: sqlite3.Connection, source: str, scope: str) -> str | None:
    row = conn.execute(
        "select last_ok_at from refresh_log where source = ? and scope = ?", (source, scope)
    ).fetchone()
    return row["last_ok_at"] if row else None
