"""Everything a decision reads about one league: format, rosters, players, projections, games.

Read-only. Every number a decision returns comes from the stored artifact (docs/data-contract.md section 7)
and the stored league state; nothing here talks to Sleeper.
"""

import json
import math
import sqlite3
from collections import defaultdict

from ingestion.nfl import service
from storage.db import get_setting

POSITION_GROUP = {
    "DE": "DL", "DT": "DL", "NT": "DL", "OLB": "LB", "ILB": "LB", "MLB": "LB", "CB": "DB", "S": "DB", "FS": "DB",
    "SS": "DB", "FB": "RB", "PK": "K",
}  # fmt: skip
# Sleeper's roster slot names and the positions each accepts
ACCEPTS = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"}, "DL": {"DL"}, "LB": {"LB"},
    "DB": {"DB"}, "FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}, "REC_FLEX": {"WR", "TE"},
    "WRRB_FLEX": {"WR", "RB"}, "IDP_FLEX": {"DL", "LB", "DB"},
}  # fmt: skip
FANTASY_STATUS = {"starter": "mine_starter", "bench": "mine_bench", "taxi": "mine_bench", "reserve": "mine_ir"}
LAST_WEEK = 18


def r1(x: float | None) -> float | None:
    return None if x is None else round(x + 0.0, 1)


def group(position: str | None) -> str | None:
    return POSITION_GROUP.get(position, position)


class NoData(Exception):
    """A question the stored data cannot answer; the message says why."""


class View:
    def __init__(self, conn: sqlite3.Connection, league_id: str, week: int | None = None):
        self.conn, self.league_id = conn, league_id
        row = conn.execute("select * from league where league_id = ?", (league_id,)).fetchone()
        if row is None:
            raise NoData(f"league {league_id} is not stored")
        self.fmt = json.loads(row["format"])
        self.settings = json.loads(row["settings"])
        self.scoring = self.fmt["scoring"]
        self.slots = self.fmt["slots"]
        self.season = service.season(conn)
        self.this_week = service.this_week(conn)
        self.week = week or self.this_week
        self.me = self._my_roster_id()
        self.names = {r["roster_id"]: r["name"] for r in conn.execute(
            "select roster_id, name from fantasy_team where league_id = ?", (league_id,))}
        self.held: dict[str, tuple[int, str]] = {}  # player_id -> (roster_id, role)
        self.rosters: dict[int, dict] = defaultdict(lambda: {"starter": {}, "bench": [], "reserve": [], "taxi": []})
        for r in conn.execute("select * from roster_player where league_id = ? order by rowid", (league_id,)):
            self.held[r["player_id"]] = (r["roster_id"], r["role"])
            if r["role"] == "starter":
                self.rosters[r["roster_id"]]["starter"][r["slot_index"]] = r["player_id"]
            else:
                self.rosters[r["roster_id"]][r["role"]].append(r["player_id"])
        self._players: dict[str, sqlite3.Row | None] = {}
        self._injury: dict[str, str | None] = {}
        self._week_rows: dict[tuple[str, int], sqlite3.Row] | None = None
        self._ros_rows: dict[str, sqlite3.Row] | None = None

    def _my_roster_id(self) -> int | None:
        row = self.conn.execute(
            "select roster_id from fantasy_team where league_id = ? and owner_id = ?",
            (self.league_id, get_setting(self.conn, "sleeper_user_id")),
        ).fetchone()
        return row["roster_id"] if row else None

    # players

    def player(self, player_id: str) -> sqlite3.Row | None:
        if player_id not in self._players:
            self._players[player_id] = self.conn.execute("select * from player where player_id = ?", (player_id,)).fetchone()
        return self._players[player_id]

    def position(self, player_id: str) -> str | None:
        p = self.player(player_id)
        return group(p["position"]) if p else None

    def ref(self, player_id: str, week: int | None = None) -> dict:
        """PlayerRef (docs/data-contract.md section 9), as the league stands now."""
        p, week = self.player(player_id), week or self.week
        held = self.held.get(player_id)
        slot = None
        if held is None:
            status = "free_agent"
        elif held[0] == self.me:
            status = FANTASY_STATUS[held[1]]
            if held[1] == "starter":
                slot = self.slots[next(i for i, x in self.rosters[self.me]["starter"].items() if x == player_id)]
        else:
            status = f"fantasy_team:{self.names[held[0]]}"
        return {
            "player_id": player_id,
            "name": (p["full_name"] if p else None) or player_id,
            "position": p["position"] if p else None,
            "nfl_team": p["nfl_team"] if p else None,
            "injury_status": self.injury_status(player_id),
            "fantasy_status": status,
            "lineup_slot": slot,
            "game": service.game_ref(self.conn, p["nfl_team"], week) if p and week else None,
        }

    def injury_status(self, player_id: str) -> str | None:
        """The designation by the facts' precedence (official report, ESPN, Sleeper catalog), not the catalog's alone."""
        if player_id not in self._injury:
            fact = service.injury_fact(self.conn, player_id)
            status = fact["value"]["designation"] if fact else None
            self._injury[player_id] = None if status in (None, "", "None") else status
        return self._injury[player_id]

    def game_status(self, player_id: str, week: int | None = None) -> str | None:
        """not_started | in_progress | complete | bye | None (no game known)."""
        g = self.ref(player_id, week or self.week)["game"]
        return g["status"] if g else None

    # projections

    def _load(self) -> None:
        self._week_rows, self._ros_rows = {}, {}
        for r in self.conn.execute("select * from projection where league_id = ?", (self.league_id,)):
            if r["horizon"] == "week":
                self._week_rows[(r["player_id"], r["week"])] = r
            else:
                self._ros_rows[r["player_id"]] = r

    def week_rows(self) -> dict[tuple[str, int], sqlite3.Row]:
        if self._week_rows is None:
            self._load()
        return self._week_rows

    def ros_rows(self) -> dict[str, sqlite3.Row]:
        if self._ros_rows is None:
            self._load()
        return self._ros_rows

    def week_row(self, player_id: str, week: int) -> sqlite3.Row | None:
        if self._week_rows is None:
            self._load()
        return self._week_rows.get((player_id, week))

    def ros_row(self, player_id: str) -> sqlite3.Row | None:
        if self._ros_rows is None:
            self._load()
        return self._ros_rows.get(player_id)

    def mean(self, player_id: str, week: int) -> float:
        r = self.week_row(player_id, week)
        return r["mean"] if r else 0.0

    def variance(self, player_id: str, week: int) -> float:
        r = self.week_row(player_id, week)
        return r["variance"] if r else 0.0

    def weeks(self, horizon: str) -> list[int]:
        return [self.week] if horizon == "week" else list(range(self.this_week, LAST_WEEK + 1))

    def horizon_mean(self, player_id: str, horizon: str) -> float:
        if horizon == "week":
            return self.mean(player_id, self.week)
        r = self.ros_row(player_id)
        return r["mean"] if r else 0.0

    # league

    def opponent_id(self, week: int, roster_id: int | None = None) -> int | None:
        row = self.conn.execute(
            "select o.roster_id from matchup m join matchup o on o.league_id = m.league_id and o.week = m.week "
            "and o.matchup_id = m.matchup_id and o.roster_id != m.roster_id "
            "where m.league_id = ? and m.week = ? and m.roster_id = ? and m.matchup_id is not null",
            (self.league_id, week, roster_id or self.me),
        ).fetchone()
        return row["roster_id"] if row else None

    def active(self, roster_id: int) -> list[str]:
        """Players who can start: starters and bench (not IR, not taxi), in roster order."""
        r = self.rosters[roster_id]
        return [*(p for _, p in sorted(r["starter"].items())), *r["bench"]]

    def free_agents(self) -> list[str]:
        return [
            r["player_id"] for r in self.conn.execute(
                "select player_id from player where active = 1 and nfl_team is not null")
            if r["player_id"] not in self.held
        ]

    def deadline(self) -> dict:
        week = self.fmt["trade_deadline_week"]
        return {"week": week, "passed": bool(week and self.this_week and self.this_week > week)}

    # envelope

    def as_of(self, news_at: str | None = None) -> dict:
        c = self.conn
        meta = c.execute("select * from projection_meta where league_id = ?", (self.league_id,)).fetchone()
        names = " and ".join({"sleeper": "Sleeper"}.get(n, n) for n, w in json.loads(meta["weights"]).items() if w > 0) if meta else ""

        def entry(at, label):
            return {"as_of": at, "source_label": label} if at else None

        ok = lambda source, scope: (c.execute("select last_ok_at from refresh_log where source = ? and scope = ?", (source, scope)).fetchone() or [None])[0]  # noqa: E731
        inj = None
        for source in ("nflverse", "espn"):
            at = c.execute("select max(as_of) from nfl_injury where source = ? and season = ?", (source, self.season)).fetchone()[0]
            if at:
                inj = entry(at, service.LABEL[(source, "injury")])
                break
        inj = inj or entry(c.execute("select max(fetched_at) from player").fetchone()[0], service.LABEL[("sleeper", "injury")])
        game = c.execute("select max(as_of) at, max(source = 'espn') espn from nfl_game where season = ?", (self.season,)).fetchone()
        return {
            "projections": entry(meta["computed_at"], f"projections ({names} blend)") if meta else None,
            "rosters": entry(ok("sleeper", "league"), "Sleeper rosters"),
            "injuries": inj,
            "news": entry(news_at, service.LABEL[("sleeper", "news")]),
            "games": entry(game["at"], "ESPN scoreboard" if game["espn"] else "nflverse schedule"),
        }

    def envelope(self, kind: str, summary: str, warnings: list[str], week: int | None = None, news_at: str | None = None) -> dict:
        meta = self.conn.execute("select unprojected_scoring_items from projection_meta where league_id = ?", (self.league_id,)).fetchone()
        return {
            "kind": kind, "league_id": self.league_id, "season": self.season, "week": week or self.week,
            "summary": summary, "as_of": self.as_of(news_at),
            "unprojected_scoring_items": json.loads(meta["unprojected_scoring_items"]) if meta else [],
            "warnings": warnings,
        }


def why(summary: str, *reasons: dict) -> dict:
    return {"summary": summary, "reasons": list(reasons)}


def reason(code: str, player_ids: list[str], values: dict, text: str) -> dict:
    return {"code": code, "player_ids": player_ids, "values": values, "text": text}


def win_probability(spread: float, variance: float) -> float:
    """Chance the spread comes out positive, the two totals taken as independent normals."""
    if variance <= 0:
        return 1.0 if spread > 0 else 0.5 if spread == 0 else 0.0
    return 0.5 * (1 + math.erf(spread / math.sqrt(2 * variance)))
