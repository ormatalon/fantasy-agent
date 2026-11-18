"""Week results and season leaders: actual points, not projections (docs/data-contract.md sections 9.6, 9.7)."""

import json
import sqlite3

from decisions.assign import fill_slots
from decisions.league import NoData, View, group, r1
from projections.scoring import score

TOP_SCORERS = 3


def _lineup(v: View, week: int, roster_id: int) -> tuple[list[str | None], dict[str, float]]:
    """The starters of a past matchup by slot, and every player's points that week."""
    rows = v.conn.execute("select player_id, points, is_starter, slot_index from matchup_player where league_id = ? and week = ? and roster_id = ?",
                          (v.league_id, week, roster_id)).fetchall()  # fmt: skip
    starters = [None] * len(v.slots)
    for r in rows:
        if r["is_starter"]:
            starters[r["slot_index"]] = r["player_id"]
    return starters, {r["player_id"]: r["points"] or 0.0 for r in rows}


def get_week_results(conn: sqlite3.Connection, league_id: str, week: int | None = None) -> dict:
    v = View(conn, league_id)
    if v.me is None:
        raise NoData("my fantasy team is not stored for this league")
    week = week or v.this_week - 1
    if week < 1 or week >= v.this_week:
        raise NoData(f"week {week} has not been played; the last completed week is {v.this_week - 1}")
    mine = conn.execute("select points from matchup where league_id = ? and week = ? and roster_id = ?", (league_id, week, v.me)).fetchone()
    opp_id = v.opponent_id(week)
    if mine is None or opp_id is None:
        raise NoData(f"no matchup stored for week {week}")
    opp = conn.execute("select points from matchup where league_id = ? and week = ? and roster_id = ?", (league_id, week, opp_id)).fetchone()
    my_start, my_points = _lineup(v, week, v.me)
    opp_start, opp_points = _lineup(v, week, opp_id)
    scorers = sorted(((p, i) for i, p in enumerate(opp_start) if p), key=lambda x: -opp_points[x[0]])[:TOP_SCORERS]

    # the best lineup from the same players, no locks; a change must gain more than a rounding error
    positions = {p: g for p in my_points if (g := v.position(p))}
    best = fill_slots(v.slots, my_start, positions, {p: my_points[p] for p in positions})
    actual_sum = sum(my_points[p] for p in my_start if p)
    optimal = sum(my_points[p] for p in best if p)
    moves = [{"start": v.ref(new, week), "bench": v.ref(old, week) if old else None, "slot": slot, "gain": r1(my_points[new] - (my_points[old] if old else 0.0))}
             for slot, old, new in zip(v.slots, my_start, best) if new and new != old]  # fmt: skip
    result = "win" if mine["points"] > opp["points"] else "loss" if mine["points"] < opp["points"] else "tie"
    left = optimal - actual_sum
    me, them = v.names[v.me], v.names[opp_id]
    summary = (f"Week {week}: you {'won' if result == 'win' else 'lost' if result == 'loss' else 'tied'} {mine['points']:.1f} to "
               f"{opp['points']:.1f} against {them}; {left:.1f} points left on your bench.")
    env = v.envelope("week_results", summary, [], week)
    env["as_of"] = {"projections": None, "rosters": env["as_of"]["rosters"] and {**env["as_of"]["rosters"], "source_label": "Sleeper matchups"},
                    "injuries": None, "news": None, "games": None}  # fmt: skip
    env["unprojected_scoring_items"] = []  # actual points: Sleeper scored every league scoring item
    return {
        **env,
        "mine": {"fantasy_team": me, "fantasy_team_id": v.me, "score": mine["points"]},
        "opponent": {"fantasy_team": them, "fantasy_team_id": opp_id, "score": opp["points"]},
        "result": result,
        "opponent_top_scorers": [{"player": v.ref(p, week), "slot": v.slots[i], "points": opp_points[p]} for p, i in scorers],
        "my_bench": {"actual_points": mine["points"], "optimal_points": round(mine["points"] + left, 2),
                     "points_left_on_bench": r1(left), "moves": moves},  # fmt: skip
    }


def get_season_leaders(conn: sqlite3.Connection, league_id: str, position: str | None = None, through_week: int | None = None,
                       limit: int = 10) -> dict:  # fmt: skip
    v = View(conn, league_id)
    through = through_week or v.this_week - 1
    if through < 1:
        raise NoData("no week has been played yet")
    pos = group(position.upper()) if position else None
    totals: dict[str, list] = {}
    weeks = set()
    for r in conn.execute("select week, player_id, stats from player_week_stat where season = ? and week <= ?", (v.season, through)):
        weeks.add(r["week"])
        stats = json.loads(r["stats"])
        if pos and v.position(r["player_id"]) != pos:
            continue
        t = totals.setdefault(r["player_id"], [0.0, 0])
        t[0] += score(stats, v.scoring)
        t[1] += int(stats.get("gp", 1) > 0)
    warnings = []
    missing = sorted(set(range(1, through + 1)) - weeks)
    if missing:
        warnings.append(f"stats not stored for week{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))}; those weeks are not counted")
    ranked = sorted(((p, t) for p, t in totals.items() if t[1] and v.player(p)), key=lambda x: -x[1][0])[:limit]
    rows = [{"rank": i, "player": v.ref(p), "points": r1(t[0]), "games": t[1], "points_per_game": r1(t[0] / t[1])} for i, (p, t) in enumerate(ranked, 1)]
    label = f"{pos}s" if pos else "players"
    summary = (f"Top {label} in actual points through week {through}: {rows[0]['player']['name']} leads with {rows[0]['points']:.1f}."
               if rows else f"No stored stats for {label} through week {through}.")
    env = v.envelope("leaders", summary, warnings, through)
    stats_at = conn.execute("select max(fetched_at) from player_week_stat where season = ?", (v.season,)).fetchone()[0]
    env["as_of"] = {"projections": None, "rosters": None, "injuries": None, "news": None,
                    "games": {"as_of": stats_at, "source_label": "Sleeper stats"} if stats_at else None}  # fmt: skip
    env["unprojected_scoring_items"] = []
    return {**env, "through_week": through, "source": "sleeper_stats", "rows": rows}
