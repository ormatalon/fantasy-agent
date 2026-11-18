"""Seeding helpers: projections over the league's real players, small edits to the stored league."""

import hashlib
import json

from decisions import artifact_view
from ingestion.nfl import service
from storage.db import now_iso, set_settings

REAL_ENSURE = artifact_view.ensure  # conftest replaces it with a no-op; test_artifact_view tests the real one

LEAGUE = "1412371724632166400"
ROW = ("insert into projection(league_id, season, player_id, horizon, week, mean, variance, n_games, zero_reason, "
       "mean_if_healthy, sources, computed_at) values (?,?,?,?,?,?,?,?,?,?,?,?)")  # fmt: skip


def seed_projections(conn, league_id=LEAGUE, means=None, variances=None, default=5.0, weeks=range(11, 19),
                     byes=(), unprojected=(), healthy=None):  # fmt: skip
    """Week rows and a rest-of-season row for every active player on an NFL team.

    means: player_id -> weekly mean, or {week: mean}; None = no row at all. variances: player_id -> weekly variance
    (default (0.6 * mean) ** 2). byes: (player_id, week) pairs that get a zero bye row. healthy: player_id ->
    mean_if_healthy of the rest-of-season row.
    """
    means, variances, healthy = means or {}, variances or {}, healthy or {}
    ts = now_iso()
    scoring = json.loads(conn.execute("select scoring_settings from league where league_id = ?", (league_id,)).fetchone()[0])
    conn.execute("delete from projection where league_id = ?", (league_id,))
    for p in conn.execute("select player_id from player where active = 1 and nfl_team is not null").fetchall():
        pid = p["player_id"]
        if pid in means and means[pid] is None:
            continue
        total = total_var = games = 0
        for w in weeks:
            m = means.get(pid, default)
            m = m.get(w, default) if isinstance(m, dict) else m
            bye = (pid, w) in byes
            var = 0.0 if bye else variances.get(pid, (0.6 * m) ** 2)
            m = 0.0 if bye else m
            src = [] if bye else [{"source": "sleeper", "weight": 1.0, "mean": m, "variance": var}]
            conn.execute(ROW, (league_id, 2026, pid, "week", w, m, var, 0 if bye else 1, "bye" if bye else None, None,
                               json.dumps(src), ts))  # fmt: skip
            total, total_var, games = total + m, total_var + var, games + (not bye)
        conn.execute(ROW, (league_id, 2026, pid, "ros", min(weeks), total, total_var, games, None, healthy.get(pid), "[]", ts))
    conn.execute(
        "insert or replace into projection_meta values (?,?,?,?,?)",
        (league_id, hashlib.sha1(json.dumps(scoring, sort_keys=True).encode()).hexdigest(),
         json.dumps([{"key": k, "value": v} for k, v in unprojected]), json.dumps({"sleeper": 0.9, "nflverse": 0.1}), ts),
    )  # fmt: skip
    set_settings(conn, {f"projection_injury_hash:{league_id}": artifact_view.injury_hash(conn, service.season(conn))})


def set_game_status(conn, nfl_team, week, status):
    conn.execute("update nfl_game set status = ? where season = 2026 and week = ? and (home = ? or away = ?)",
                 (status, week, nfl_team, nfl_team))  # fmt: skip


def put_on_bye(conn, nfl_team, week):
    conn.execute("delete from nfl_game where season = 2026 and week = ? and (home = ? or away = ?)", (week, nfl_team, nfl_team))
    conn.execute("update nfl_team set bye_week = ? where abbr = ?", (week, nfl_team))


def move(conn, player_id, role, roster_id=1, league_id=LEAGUE):
    """Change a player's role on a roster (a starter keeps no slot when moved out)."""
    conn.execute("update roster_player set role = ?, slot_index = NULL where league_id = ? and roster_id = ? and player_id = ?",
                 (role, league_id, roster_id, player_id))  # fmt: skip


def drop(conn, player_id, league_id=LEAGUE):
    conn.execute("delete from roster_player where league_id = ? and player_id = ?", (league_id, player_id))
