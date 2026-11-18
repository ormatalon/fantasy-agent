"""Player id crosswalk: Sleeper id is the key (docs/data-contract.md section 3)."""

import re
import sqlite3
from collections import defaultdict

from storage.db import tx

from . import teams

FANTASY_POSITIONS = {"QB", "RB", "FB", "WR", "TE", "K", "DL", "DE", "DT", "NT", "LB", "OLB", "ILB", "MLB",
                     "DB", "CB", "S", "FS", "SS"}
GROUP = {"FB": "RB", "DE": "DL", "DT": "DL", "NT": "DL", "OLB": "LB", "ILB": "LB", "MLB": "LB",
         "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
ID_SOURCES = ("nfl", "espn", "pfr")  # nfl = gsis id (nflverse rows key on it)


def group(position: str | None) -> str | None:
    return GROUP.get(position, position)


def norm_name(name: str | None) -> str:
    words = re.sub(r"[^a-z ]", "", (name or "").lower().replace("-", " ")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


class Crosswalk:
    def __init__(self, conn: sqlite3.Connection):
        self.players = {
            r["player_id"]: r
            for r in conn.execute(
                "select player_id, full_name, position, nfl_team, active, search_rank from player where position != 'DEF'"
            )
        }
        active = [p for p in self.players.values() if p["active"]]
        self.by_name_team = defaultdict(list)
        self.by_name = defaultdict(list)
        for p in active:
            self.by_name[norm_name(p["full_name"])].append(p)
            self.by_name_team[(norm_name(p["full_name"]), p["nfl_team"])].append(p)
        self.map: dict[tuple[str, str], tuple[str, str]] = {
            (r["source"], r["source_id"]): (r["player_id"], r["method"])
            for r in conn.execute("select * from player_id_map")
        }

    def resolve(self, source: str, source_id=None, name=None, nfl_team=None, position=None) -> str | None:
        """Sleeper id, or None. source: nfl (gsis), espn, pfr, or any name for a name-only lookup."""
        if source_id is not None and (hit := self.map.get((source, str(source_id)))):
            return hit[0]
        found = self._by_name(name, nfl_team, position)
        if found and source_id is not None and source in ID_SOURCES:
            self.map[(source, str(source_id))] = (found[0], found[1])
        return found[0] if found else None

    def _by_name(self, name, nfl_team, position) -> tuple[str, str] | None:
        key = norm_name(name)
        if not key:
            return None
        team = teams.resolve(nfl_team) if nfl_team else None
        pos = group(position)
        same = [p for p in self.by_name_team.get((key, team), []) if pos is None or group(p["position"]) == pos]
        if len(same) == 1:
            return same[0]["player_id"], "name_team_pos"
        others = self.by_name.get(key, [])
        if not same and len(others) == 1:
            return others[0]["player_id"], "name_unique"
        return None

    def learn_rosters(self, rows: list[dict]) -> None:
        """Step 1: nflverse weekly roster rows (latest per player) carry sleeper_id, gsis, espn and pfr ids."""
        for r in rows:
            sid = str(int(float(r["sleeper_id"]))) if r.get("sleeper_id") else None
            if sid in self.players:
                found = (sid, "sleeper_id")
            else:
                found = self._by_name(r["full_name"], r["team"], r["position"])
            if not found:
                continue
            for source, col in (("nfl", "gsis_id"), ("espn", "espn_id"), ("pfr", "pfr_id")):
                if r.get(col):
                    self.map[(source, str(int(float(r[col]))) if col == "espn_id" else r[col])] = found

    def save(self, conn: sqlite3.Connection) -> dict:
        with tx(conn):
            conn.execute("delete from player_id_map")
            conn.executemany(
                "insert into player_id_map values (?,?,?,?)",
                [(pid, s, sid, method) for (s, sid), (pid, method) in self.map.items()],
            )
        return self.coverage(conn)

    def coverage(self, conn: sqlite3.Connection) -> dict:
        """Per id source: Sleeper players (active, on an NFL team, fantasy/IDP position) that have an id there."""
        rostered = {r[0] for r in conn.execute("select distinct player_id from roster_player")}
        have = defaultdict(set)
        for (s, _), (pid, _m) in self.map.items():
            have[s].add(pid)
        scope = [p for p in self.players.values() if p["active"] and p["nfl_team"] and p["position"] in FANTASY_POSITIONS]
        relevant = [p for p in scope if p["player_id"] in rostered or (p["search_rank"] or 9999) < 600]
        return {
            s: {
                "mapped": sum(p["player_id"] in have[s] for p in scope),
                "total": len(scope),
                "relevant_mapped": sum(p["player_id"] in have[s] for p in relevant),
                "relevant_total": len(relevant),
            }
            for s in ID_SOURCES
        }
