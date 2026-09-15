"""Cross-source player ID resolution, built from Sleeper's own players catalog.

Sleeper is the canonical ID space (see ProjectionSource.fetch) since it's
also what rosters/matchups/transactions are keyed by. Other sources
crosswalk into it — by gsis_id where available (exact), falling back to
name+team+position (approximate) where it isn't.
"""

import sqlite3
from dataclasses import dataclass, field


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


@dataclass
class Crosswalk:
    by_gsis_id: dict[str, str] = field(default_factory=dict)
    by_name_team_pos: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def from_gsis(self, gsis_id: str | None) -> str | None:
        return self.by_gsis_id.get(gsis_id) if gsis_id else None

    def from_name(self, name: str, team: str | None, position: str | None) -> str | None:
        return self.by_name_team_pos.get((_normalize(name), team or "", position or ""))


def build_crosswalk(conn: sqlite3.Connection) -> Crosswalk:
    rows = conn.execute(
        "SELECT player_id, first_name, last_name, position, team, gsis_id FROM players"
    ).fetchall()

    by_gsis: dict[str, str] = {}
    by_name: dict[tuple[str, str, str], str] = {}
    for r in rows:
        if r["gsis_id"]:
            by_gsis[r["gsis_id"]] = r["player_id"]
        full_name = " ".join(p for p in [r["first_name"], r["last_name"]] if p)
        if full_name:
            by_name[(_normalize(full_name), r["team"] or "", r["position"] or "")] = r["player_id"]

    return Crosswalk(by_gsis_id=by_gsis, by_name_team_pos=by_name)
