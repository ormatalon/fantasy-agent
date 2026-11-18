"""Injured stash: unrostered injured players by healthy rest-of-season value, with the latest news.

Achane (IR) and Egbuka (Out) are real injured players on Or Test League rosters; they are dropped to free agency.
Will Anderson Jr. (DL) gets the real RotoWire and FantasyPros headlines captured from Sleeper.
"""

import json

from decisions import stash
from decisions.tests.support import LEAGUE, drop, seed_projections
from ingestion.tests.support import fixture

ACHANE, EGBUKA, ANDERSON = "9226", "12514", "10892"


def put_news(conn):
    for n in fixture("graphql_get_player_news.json")["data"]["get_player_news"]:
        m = n["metadata"]
        conn.execute("insert or replace into news values (?,?,?,?,?,?,?,?,?)",
                     (n["source"], n["source_key"], n["player_id"], "2026-11-16T12:00:00Z", m["title"], m.get("description"),
                      m.get("analysis"), m.get("url"), "2026-11-17T09:00:00Z"))  # fmt: skip


def test_injured_free_agents_rank_by_healthy_value_over_replacement(league):
    for pid in (ACHANE, EGBUKA):
        drop(league, pid)
    # both are projected 0 now (injured); their healthy rest-of-season totals are what the stash ranks
    seed_projections(league, means={ACHANE: 0.0, EGBUKA: 0.0}, healthy={ACHANE: 120.5, EGBUKA: 70.0})
    r = stash.get_injured_stash(league, LEAGUE)
    assert [c["player"]["name"] for c in r["candidates"]] == ["De'Von Achane", "Emeka Egbuka"]
    first = r["candidates"][0]
    assert first["healthy_ros_mean"] == 120.5 and first["healthy_ros_vorp"] > first["healthy_ros_mean"] - 60  # 8 weeks at 5 = 40 replacement
    assert first["injury"]["status"] == "IR" and first["player"]["fantasy_status"] == "free_agent"
    assert r["summary"].startswith("Best injured stash: De'Von Achane (IR)")
    assert [x["code"] for x in first["why"]["reasons"]] == ["replacement_level", "injury"]


def test_news_prose_comes_with_each_candidate_newest_first(league):
    drop(league, ANDERSON)
    league.execute("update player set injury_status = 'Out', injury_body_part = 'Ankle' where player_id = ?", (ANDERSON,))
    seed_projections(league, means={ANDERSON: 0.0}, healthy={ANDERSON: 90.0})
    put_news(league)
    (c,) = stash.get_injured_stash(league, LEAGUE)["candidates"]
    assert c["player"]["name"] == "Will Anderson Jr." and c["injury"]["body_part"] == "Ankle"
    assert len(c["news"]) == 3 and c["news"][0]["title"].startswith("Will Anderson")
    assert c["news"][0]["text"] and c["news"][0]["source"] == "Sleeper news (RotoWire)"


def test_a_player_with_no_news_has_an_empty_list_not_a_guess(league):
    drop(league, ACHANE)
    seed_projections(league, means={ACHANE: 0.0}, healthy={ACHANE: 100.0})
    assert stash.get_injured_stash(league, LEAGUE)["candidates"][0]["news"] == []


def test_healthy_players_and_rostered_players_are_not_stash_candidates(league):
    seed_projections(league, healthy={ACHANE: 100.0})  # Achane is on a roster: not available
    assert stash.get_injured_stash(league, LEAGUE)["candidates"] == []


def test_position_filter_and_the_envelope(league):
    for pid in (ACHANE, EGBUKA):
        drop(league, pid)
    seed_projections(league, means={ACHANE: 0.0, EGBUKA: 0.0}, healthy={ACHANE: 120.5, EGBUKA: 70.0}, unprojected=[("def_st_td", 6.0)])
    r = stash.get_injured_stash(league, LEAGUE, position="WR")
    assert [c["player"]["name"] for c in r["candidates"]] == ["Emeka Egbuka"]
    assert r["kind"] == "stash" and r["unprojected_scoring_items"] == [{"key": "def_st_td", "value": 6.0}]
    assert json.dumps(r)  # plain data
