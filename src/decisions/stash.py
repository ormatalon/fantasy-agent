"""Injured stash: unrostered injured players by healthy rest-of-season value (docs/data-contract.md section 9.3)."""

import sqlite3

from decisions import artifact_view
from decisions.league import NoData, View, r1, reason, why
from decisions.value import free_agent_pool, replacement
from ingestion.nfl import service

STASH_STATUSES = ("IR", "Out", "PUP")  # players who will miss time; Questionable and Doubtful play


def get_injured_stash(conn: sqlite3.Connection, league_id: str, position: str | None = None, limit: int = 10) -> dict:
    warnings = artifact_view.ensure(conn, league_id)
    v = View(conn, league_id)
    if v.me is None:
        raise NoData("my fantasy team is not stored for this league")
    repl, ranked = {}, []
    for pid in free_agent_pool(v, position):
        if v.injury_status(pid) not in STASH_STATUSES:
            continue
        row, pos = v.ros_row(pid), v.position(pid)
        repl[pos] = repl.get(pos) or replacement(v, pos, "ros")
        healthy = row["mean_if_healthy"] if row["mean_if_healthy"] is not None else row["mean"]
        if healthy - repl[pos]["points"] > 0:
            ranked.append((healthy - repl[pos]["points"], healthy, pid))
    candidates, fetched = [], []
    for rank, (vorp, healthy, pid) in enumerate(sorted(ranked, reverse=True)[:limit], 1):
        ref, p = v.ref(pid), v.player(pid)
        fact = service.injury_fact(conn, pid)
        injury = ({"status": fact["value"]["designation"], "body_part": fact["value"]["body_part"], "source": fact["source_label"], "as_of": fact["as_of"]}
                  if fact else {"status": None, "body_part": p["injury_body_part"], "source": service.LABEL[("sleeper", "injury")], "as_of": p["fetched_at"]})  # fmt: skip
        facts, gaps = service.news_facts(conn, pid, limit=3)
        warnings += [f"{ref['name']}: {g}" for g in gaps]
        news = [{"title": f["value"]["title"], "text": f["value"]["description"], "url": f["value"]["url"],
                 "published_at": f["value"]["published_at"], "source": f["source_label"]} for f in facts]  # fmt: skip
        fetched += [f["fetched_at"] for f in facts]
        summary = (f"{ref['name']} has the highest healthy value among injured free agents." if rank == 1
                   else f"{ref['name']}'s healthy rest-of-season value is {vorp:.1f} over replacement.")
        candidates.append({
            "rank": rank, "player": ref, "healthy_ros_vorp": r1(vorp), "healthy_ros_mean": r1(healthy), "injury": injury, "news": news,
            "why": why(summary, reason("replacement_level", [pid], {"healthy_ros_vorp": r1(vorp)}, f"{vorp:.1f} points over replacement if healthy."),
                       reason("injury", [pid], {"status": injury["status"] or ""}, f"{ref['name']} is listed {injury['status']}.")),
        })  # fmt: skip
    summary = (f"Best injured stash: {candidates[0]['player']['name']} ({candidates[0]['injury']['status']}), healthy rest-of-season "
               f"value {candidates[0]['healthy_ros_vorp']:.1f} over replacement.") if candidates else "No injured free agent has healthy value above replacement."
    return {**v.envelope("stash", summary, warnings, news_at=max(fetched, default=None)), "candidates": candidates}
