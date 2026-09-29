"""Cross-source player ID resolution, built from Sleeper's own players catalog.

Sleeper is the canonical ID space (see ProjectionSource.fetch) since it's
also what rosters/matchups/transactions are keyed by. Other sources
crosswalk into it — by gsis_id where available (exact), falling back to
name matching where it isn't.

The fallback is not optional: Sleeper's catalog carries a gsis_id for only
a minority of current players (26 of 187 active RBs in 2026), so gsis-only
matching silently dropped most young players from anything nflverse-based.
"""

import re
import sqlite3
from dataclasses import dataclass, field

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# nflverse team codes that differ from Sleeper's.
_TEAM_TO_SLEEPER = {"LA": "LAR"}


def _normalize(name: str) -> str:
    """'Kenneth Walker III' -> 'kennethwalker', "D'Andre Swift" -> 'dandreswift'."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower().replace("'", "").replace(".", "")) if t]
    return "".join(t for t in tokens if t not in _SUFFIXES)


@dataclass
class Crosswalk:
    by_gsis_id: dict[str, str] = field(default_factory=dict)
    by_name_team_pos: dict[tuple[str, str, str], str] = field(default_factory=dict)
    by_name_pos: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    by_name: dict[str, list[str]] = field(default_factory=dict)

    def from_gsis(self, gsis_id: str | None) -> str | None:
        return self.by_gsis_id.get(gsis_id) if gsis_id else None

    def from_name(self, name: str, team: str | None, position: str | None) -> str | None:
        return self.by_name_team_pos.get((_normalize(name), team or "", position or ""))

    def resolve(self, gsis_id: str | None, name: str | None, team: str | None, position: str | None) -> str | None:
        """gsis_id, else name+team+position, else a name+position or bare
        name that belongs to exactly one active player. Never guesses between
        two players sharing a name."""
        if sleeper_id := self.from_gsis(gsis_id):
            return sleeper_id
        if not name:
            return None
        key = _normalize(name)
        team = _TEAM_TO_SLEEPER.get(team, team)
        if sleeper_id := self.by_name_team_pos.get((key, team or "", position or "")):
            return sleeper_id
        for candidates in (self.by_name_pos.get((key, position or ""), []), self.by_name.get(key, [])):
            if len(candidates) == 1:
                return candidates[0]
        return None


def build_crosswalk(conn: sqlite3.Connection) -> Crosswalk:
    rows = conn.execute(
        "SELECT player_id, first_name, last_name, position, team, gsis_id FROM players"
    ).fetchall()

    cw = Crosswalk()
    for r in rows:
        if r["gsis_id"]:
            cw.by_gsis_id[r["gsis_id"]] = r["player_id"]
        full_name = " ".join(p for p in [r["first_name"], r["last_name"]] if p)
        if not full_name:
            continue
        key = _normalize(full_name)
        cw.by_name_team_pos[(key, r["team"] or "", r["position"] or "")] = r["player_id"]
        # Loose indexes only cover players on a team: the catalog also holds
        # retired players, who would otherwise make live names look ambiguous.
        if r["team"]:
            cw.by_name_pos.setdefault((key, r["position"] or ""), []).append(r["player_id"])
            cw.by_name.setdefault(key, []).append(r["player_id"])
    return cw


def map_nflverse_ids(crosswalk: Crosswalk, df) -> dict[str, str]:
    """{nflverse gsis player_id: sleeper_id} for the players in an nflverse
    weekly-stats frame, using each player's latest row for name/team/position."""
    name_col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    cols = [c for c in ("player_id", name_col, "recent_team", "position", "week") if c in df.columns]
    latest = df[cols].sort_values("week").groupby("player_id").tail(1)
    out = {}
    for row in latest.to_dict("records"):
        sleeper_id = crosswalk.resolve(row["player_id"], row.get(name_col), row.get("recent_team"), row.get("position"))
        if sleeper_id:
            out[row["player_id"]] = sleeper_id
    return out
