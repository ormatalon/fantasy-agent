"""The projection artifact: one row per player per week and rest of season (docs/data-contract.md section 7).

Pure functions over source rows, the schedule and injury facts; `build` reads them from SQLite and `save`
writes the `projection` and `projection_meta` tables. Every decision reads the artifact.
"""

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime

from ingestion.nfl import service
from projections.blend import DEFAULT_WEIGHTS, Blended, blend
from projections.scoring import unprojected_items
from projections.sources import ProjectionSource

LAST_WEEK = 18
RULED_OUT = {"Out": "out", "PUP": "pup", "Sus": "suspended"}  # zero for that one game
COLUMNS = (
    "league_id season player_id horizon week mean variance n_games zero_reason mean_if_healthy return_week "
    "return_date return_date_source return_date_as_of sources computed_at"
).split()


def return_week(return_date: str, games: dict[int, str], from_week: int) -> int | None:
    """First week from `from_week` through 18 whose game kicks off on or after the return date (ISO)."""
    return next((w for w in sorted(games) if from_week <= w <= LAST_WEEK and games[w][:10] >= return_date), None)


def _zero(week: int, reason: str, healthy: Blended | None) -> dict:
    return {"week": week, "mean": 0.0, "variance": 0.0, "games": reason != "bye", "zero_reason": reason,
            "healthy": healthy.mean if healthy else None, "sources": []}  # fmt: skip


def player_weeks(blended: dict[int, Blended], games: dict[int, str], injury: dict | None, from_week: int) -> list[dict]:
    """One dict per week from `from_week` through 18, with the injury overlay applied.

    blended: week -> Blended for this player; games: week -> kickoff of his NFL team's games (no entry = bye);
    injury: the injury fact value (designation, return_date, return_date_source, return_date_as_of) or None.
    Out, PUP and Sus zero the next game only. IR zeroes the weeks before the expected return week; with no
    return date the rows are left as they are (build gives such players Sleeper's own rows). Nothing else
    changes a projection.
    """
    designation = (injury or {}).get("designation")
    next_game = min((w for w in games if w >= from_week), default=None)
    ret = None
    if designation == "IR" and injury.get("return_date"):
        ret = return_week(injury["return_date"], games, from_week) or LAST_WEEK + 1  # after week 18: never
    out = []
    for w in range(from_week, LAST_WEEK + 1):
        b = blended.get(w)
        if w not in games:
            out.append(_zero(w, "bye", None))
        elif b is None:
            continue
        elif ret is not None and w < ret:
            out.append(_zero(w, "ir", b))
        elif designation in RULED_OUT and w == next_game:
            out.append(_zero(w, RULED_OUT[designation], b))
        else:
            out.append({"week": w, "mean": b.mean, "variance": b.variance, "games": True, "zero_reason": None,
                        "healthy": b.mean if designation in RULED_OUT or designation == "IR" else None,
                        "sources": b.sources})  # fmt: skip
    return out


def _sources_total(weeks: list[dict]) -> list[dict]:
    """Per source: the sum of its weekly means and variances, weight averaged over the weeks it appears in."""
    acc = defaultdict(lambda: {"weight": [], "mean": 0.0, "variance": 0.0})
    for wk in weeks:
        for s in wk["sources"]:
            a = acc[s["source"]]
            a["weight"].append(s["weight"])
            a["mean"] += s["mean"]
            a["variance"] += s["variance"]
    return [{"source": k, "weight": sum(a["weight"]) / len(a["weight"]), "mean": a["mean"], "variance": a["variance"]} for k, a in acc.items()]


def player_rows(league_id: str, season: int, player_id: str, weeks: list[dict], injury: dict | None, games: dict[int, str], now: str) -> list[dict]:
    """The week rows plus the one rest-of-season row (the sum of the weeks, which follows the same overlay)."""
    if not weeks:
        return []
    ir = injury if (injury or {}).get("designation") == "IR" and injury.get("return_date") else {}
    common = {
        "league_id": league_id, "season": season, "player_id": player_id, "computed_at": now,
        "return_week": return_week(ir["return_date"], games, weeks[0]["week"]) if ir else None,
        "return_date": ir.get("return_date"), "return_date_source": ir.get("return_date_source"),
        "return_date_as_of": ir.get("return_date_as_of"),
    }  # fmt: skip
    rows = [
        {**common, "horizon": "week", "week": wk["week"], "mean": wk["mean"], "variance": wk["variance"],
         "n_games": int(wk["games"]), "zero_reason": wk["zero_reason"],
         "mean_if_healthy": wk["healthy"], "sources": json.dumps(wk["sources"])}
        for wk in weeks
    ]  # fmt: skip
    zeroed = next((wk["zero_reason"] for wk in weeks if wk["zero_reason"] not in (None, "bye")), None)
    healthy = [wk["healthy"] if wk["healthy"] is not None else wk["mean"] for wk in weeks]
    rows.append(
        {**common, "horizon": "ros", "week": weeks[0]["week"], "mean": sum(wk["mean"] for wk in weeks),
         "variance": sum(wk["variance"] for wk in weeks), "n_games": sum(wk["games"] for wk in weeks),
         "zero_reason": zeroed, "mean_if_healthy": sum(healthy) if zeroed else None,
         "sources": json.dumps(_sources_total(weeks))}
    )  # fmt: skip
    return rows


def sleeper_only(b: Blended) -> Blended | None:
    """The Sleeper part of a blended row, as Sleeper projects it; None when Sleeper has no row."""
    own = next((p for p in b.sources if p["source"] == "sleeper"), None)
    return own and Blended(b.player_id, b.week, own["mean"], own["variance"], [{**own, "weight": 1.0}])


def team_games(conn: sqlite3.Connection, season: int) -> dict[str, dict[int, str]]:
    """NFL team -> week -> kickoff (ISO UTC), from the stored schedule."""
    out = defaultdict(dict)
    for g in conn.execute("select week, home, away, kickoff_utc from nfl_game where season = ?", (season,)):
        out[g["home"]][g["week"]] = out[g["away"]][g["week"]] = g["kickoff_utc"]
    return out


def build(conn: sqlite3.Connection, league_id: str, season: int, from_week: int, sources: list[ProjectionSource],
          weights: dict[str, float] = DEFAULT_WEIGHTS, now: str | None = None) -> tuple[list[dict], dict]:  # fmt: skip
    """Artifact rows and meta for one league. from_week is this week; from_week=1 gives the full season."""
    now = now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    scoring = json.loads(conn.execute("select scoring_settings from league where league_id = ?", (league_id,)).fetchone()[0])
    players = {r["player_id"]: r for r in conn.execute("select player_id, position, nfl_team from player where active = 1")}
    positions = {pid: p["position"] for pid, p in players.items()}
    weeks = list(range(from_week, LAST_WEEK + 1))
    blended = blend({s.name: s.weekly(season, weeks) for s in sources}, weights, scoring, positions)
    by_player = defaultdict(dict)
    for (pid, w), b in blended.items():
        by_player[pid][w] = b
    schedule = team_games(conn, season)
    rows = []
    for pid, bw in by_player.items():
        p = players.get(pid)
        if not p or not p["nfl_team"]:
            continue
        fact = service.injury_fact(conn, pid)
        injury = fact["value"] if fact else None
        if injury and injury["designation"] == "IR" and not injury["return_date"]:  # no date: Sleeper's own rows
            bw = {w: o for w, b in bw.items() if (o := sleeper_only(b))}
        games = schedule.get(p["nfl_team"], {})
        rows += player_rows(league_id, season, pid, player_weeks(bw, games, injury, from_week), injury, games, now)
    active = [s for s in sources if weights.get(s.name, 0) > 0]
    meta = {
        "league_id": league_id,
        "scoring_hash": hashlib.sha1(json.dumps(scoring, sort_keys=True).encode()).hexdigest(),
        "unprojected_scoring_items": json.dumps(unprojected_items(scoring, set().union(*(s.stat_keys for s in active)))),
        "weights": json.dumps(weights), "computed_at": now,
    }  # fmt: skip
    return rows, meta


def save(conn: sqlite3.Connection, rows: list[dict], meta: dict) -> None:
    """Replace the league's artifact (a rebuild is complete or not at all)."""
    league_id = meta["league_id"]
    conn.execute("begin immediate")
    conn.execute("delete from projection where league_id = ?", (league_id,))
    conn.executemany(f"insert into projection({', '.join(COLUMNS)}) values ({', '.join(':' + c for c in COLUMNS)})", rows)
    conn.execute(
        "insert or replace into projection_meta(league_id, scoring_hash, unprojected_scoring_items, weights, computed_at)"
        " values (:league_id, :scoring_hash, :unprojected_scoring_items, :weights, :computed_at)", meta,
    )  # fmt: skip
    conn.execute("commit")


def ir_return_date_coverage(conn: sqlite3.Connection) -> dict:
    """Stage 2.3 check: how many players on IR have a return date, and who does not."""
    ir, missing = 0, []
    for r in conn.execute("select player_id, full_name from player where injury_status = 'IR' and active = 1"):
        ir += 1
        fact = service.injury_fact(conn, r["player_id"])
        if not (fact and fact["value"]["return_date"]):
            missing.append(r["full_name"])
    return {"ir_players": ir, "with_return_date": ir - len(missing), "without_return_date": missing}
