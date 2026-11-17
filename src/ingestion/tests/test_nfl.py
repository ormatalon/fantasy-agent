"""NFL facts on real captures (2026-11-17): SEA and ARI nflverse files, ESPN injuries and scoreboards."""

from datetime import UTC, datetime, timedelta

import pytest

from ingestion.tests.support import csv_rows, espn_json, load_catalog, load_games, load_nfl_facts
from ingestion.nfl import crosswalk, espn as espn_mod, service, teams
from ingestion.nfl.base import SourceError
from ingestion.nfl.crosswalk import Crosswalk

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


@pytest.fixture
def nfl(conn, monkeypatch):
    monkeypatch.setattr(service, "team_roster_refresh", lambda *a, **k: None)  # no ESPN call
    results = load_nfl_facts(conn, monkeypatch)
    return conn, results


def player_id(conn, name):
    return conn.execute("select player_id from player where full_name = ?", (name,)).fetchone()[0]


def test_teams_resolve_every_spelling():
    assert teams.resolve("Seattle") == teams.resolve("Seahawks") == teams.resolve("SEA") == "SEA"
    assert teams.resolve("LA") == "LAR" and teams.resolve("WSH") == "WAS" and teams.resolve("New York") is None


def test_seattle_roster_has_no_metcalf(nfl):
    conn, _ = nfl
    roster = service.team_roster(conn, "SEA", now=NOW)
    names = [p["name"] for p in roster["players"]]
    assert "DK Metcalf" not in names and "Jaxon Smith-Njigba" in names
    metcalf = service.roster_fact(conn, player_id(conn, "DK Metcalf"), NOW)
    assert metcalf["nfl_team"] == "PIT" and metcalf["source"] == "nflverse"
    assert metcalf["source_label"] and metcalf["as_of"] and metcalf["age_text"]


def test_depth_labels_and_filter_by_position(nfl):
    conn, _ = nfl
    wr = service.team_roster(conn, "SEA", "WR", NOW)["players"]
    labels = {p["name"]: p["depth"]["value"]["slot_label"] for p in wr if p["depth"]}
    assert labels["Jaxon Smith-Njigba"] == "WR1" and labels["Rashid Shaheed"] == "WR2"
    assert all(p["position"] == "WR" for p in wr)
    assert wr[0]["depth"]["source_label"] == "nflverse depth chart"


def test_designation_matches_official_report(nfl):
    """Same fixtures as a Wednesday of week 10: week 10 not final, so the official week 10 report applies."""
    conn, _ = nfl
    conn.execute("update nfl_game set status = 'scheduled' where week = 10")
    assert service.this_week(conn) == 10
    xwalk = Crosswalk(conn)
    checked = 0
    for r in csv_rows("injuries_2026_sea_ari.csv"):
        if r["week"] != "10" or r["position"] not in crosswalk.FANTASY_POSITIONS:
            continue
        pid = xwalk.resolve("nfl", r["gsis_id"])
        if not pid:
            continue
        fact = service.injury_fact(conn, pid, NOW)
        assert fact["source"] == "nflverse" and fact["week"] == 10
        assert fact["value"]["designation"] == (r["report_status"] or None)
        assert fact["value"]["practice_status"] == (r["practice_status"] or None)
        checked += 1
    assert checked > 20
    assert any(f["value"]["designation"] == "Questionable" for p in service.team_roster(conn, "ARI", now=NOW)["players"] if (f := p["injury"]))


def test_before_the_weekly_report_exists_espn_is_used_and_the_gap_is_stated(nfl):
    conn, _ = nfl
    assert service.this_week(conn) == 11
    espn_rows = conn.execute("select * from nfl_injury where source = 'espn'").fetchall()
    out = next(r for r in espn_rows if r["designation"] == "Out")
    fact = service.injury_fact(conn, out["player_id"], NOW)
    assert fact["source"] == "espn" and fact["source_label"] == "ESPN injury list"
    assert fact["value"]["designation"] == "Out"
    assert any("no official injury report for week 11" in g for g in service.team_roster(conn, "ARI", now=NOW)["set"]["gaps"])


def test_return_date_comes_from_espn_with_its_own_source_and_date(nfl):
    conn, _ = nfl
    dated = [r for r in conn.execute("select * from nfl_injury where source = 'espn'") if r["return_date"]]
    assert dated, "the capture has an ESPN injury with a return date"
    fact = service.injury_fact(conn, dated[0]["player_id"], NOW)
    v = fact["value"]
    assert v["return_date"] == dated[0]["return_date"] and v["return_date_source"] == "espn"
    assert v["return_date_as_of"] == dated[0]["as_of"]


def test_official_designation_keeps_an_espn_return_date_and_lists_disagreement(nfl):
    conn, _ = nfl
    pid = conn.execute("select player_id from nfl_injury where source = 'espn' and return_date is not null").fetchone()[0]
    conn.execute("insert or replace into nfl_injury(player_id, season, week, source, designation, practice_status, as_of, fetched_at)"
                 " values (?, 2026, 11, 'nflverse', 'Questionable', 'Limited Participation in Practice', '2026-11-19T20:00:00Z', '2026-11-19T20:00:00Z')", (pid,))
    fact = service.injury_fact(conn, pid, NOW)
    assert fact["source"] == "nflverse" and fact["value"]["designation"] == "Questionable"
    assert fact["value"]["return_date_source"] == "espn"
    assert fact["also"][0]["source"] == "espn" and fact["also"][0]["value"] != "Questionable"


def test_snap_usage_has_source_and_week(nfl):
    conn, _ = nfl
    pid = player_id(conn, "Jaxon Smith-Njigba")
    facts = service.usage_facts(conn, pid, 3, NOW)
    assert facts and {f["source"] for f in facts} == {"nflverse"}
    assert all(0 <= f["value"]["offense_pct"] <= 1 for f in facts) and facts[0]["week"] == 10


def test_crosswalk_coverage_is_reported_per_source(nfl):
    conn, results = nfl
    cov = Crosswalk(conn).coverage(conn)
    assert set(cov) == {"nfl", "espn", "pfr"}
    for source, c in cov.items():
        assert c["relevant_mapped"] / c["relevant_total"] >= 0.95, source
    # per fetch: rows the source listed at fantasy positions that resolved
    for key in (("nflverse", "injury"), ("nflverse", "depth"), ("espn", "injury")):
        r = results[key]
        assert (r.total - r.unmapped) / r.total >= 0.95, key


def test_crosswalk_rules_in_order(conn, monkeypatch):
    load_catalog(conn, monkeypatch)
    xw = Crosswalk(conn)
    xw.learn_rosters([r for r in csv_rows("roster_weekly_2026_sea_ari_metcalf.csv") if r["full_name"] == "DK Metcalf"][-1:])
    metcalf = player_id(conn, "DK Metcalf")
    assert xw.resolve("nfl", "00-0000000") is None  # an unknown id alone resolves nothing
    row = [r for r in csv_rows("roster_weekly_2026_sea_ari_metcalf.csv") if r["full_name"] == "DK Metcalf"][-1]
    assert xw.resolve("nfl", row["gsis_id"]) == metcalf
    assert xw.resolve("espn", None, "D.K. Metcalf Jr.", "PIT", "WR") == metcalf  # punctuation and suffix normalised
    assert crosswalk.norm_name("Marvin Harrison Jr.") == "marvin harrison"


def test_unknown_name_does_not_guess(conn, monkeypatch):
    load_catalog(conn, monkeypatch)
    assert Crosswalk(conn).resolve("espn", "1", "Nobody Atall", "SEA", "WR") is None


def test_games_kickoff_and_this_week(conn):
    load_games(conn)
    assert service.this_week(conn) == 11
    ref = service.game_ref(conn, "KC", 11)
    assert ref["status"] == "not_started" and ref["opponent_nfl_team"] and ref["kickoff"].endswith("Z")
    thu = conn.execute("select * from nfl_game where week = 11 order by kickoff_utc").fetchone()
    assert thu["kickoff_utc"] == "2026-11-20T01:15:00Z"  # Thursday 20:15 US Eastern


def test_espn_state_overrides_and_brings_byes(conn):
    load_games(conn, espn=True)
    assert service.game_ref(conn, "SEA", 11)["status"] == "bye"
    assert service.game_ref(conn, "SEA", 10)["status"] == "complete"
    # a game in progress on ESPN beats nflverse's "scheduled"
    event = espn_json("scoreboard_week11.json")
    event["events"][0]["status"]["type"]["state"] = "in"
    result = espn_mod.EspnSource(Crosswalk(conn)).schedule_from([event], 2026)
    service._store(conn, "espn", "schedule", result, 2026, [])
    first = conn.execute("select * from nfl_game where week = 11 order by kickoff_utc").fetchone()
    assert first["status"] == "in_progress" and first["source"] == "espn"


def test_this_week_stays_until_the_last_game_is_final(conn):
    load_games(conn)
    conn.execute("update nfl_game set status = 'scheduled' where game_id = (select game_id from nfl_game where week = 10 limit 1)")
    assert service.this_week(conn) == 10


def test_game_window_is_three_hours_before_kickoff_until_final(conn):
    load_games(conn)
    kickoff = datetime(2026, 11, 20, 1, 15, tzinfo=UTC)
    assert not service.in_game_window(conn, kickoff - timedelta(hours=4))
    assert service.in_game_window(conn, kickoff - timedelta(hours=2, minutes=59))
    assert service.in_game_window(conn, kickoff + timedelta(hours=2))
    assert not service.in_game_window(conn, NOW)


def test_inactives_from_espn_roster(conn, monkeypatch):
    """JAX at BAL, final: ESPN marks 8 players of BAL's roster didNotPlay."""
    load_catalog(conn, monkeypatch)
    xw = Crosswalk(conn)
    entries = espn_json("core_event_roster_jax_at_bal_final.json")["entries"]
    out = [e for e in entries if e["didNotPlay"]]
    two = [p[0] for p in conn.execute("select player_id from player limit 2")]
    for e, pid in zip(out, two):
        xw.map[("espn", str(e["playerId"]))] = (pid, "test")
    game = {"game_id": "2026_09_JAX_BAL"}
    result = espn_mod.EspnSource(xw).inactive_from([(game, {"33": entries})])
    assert len(out) == 8 and result.total == 8 and result.unmapped == 6
    assert [r["player_id"] for r in result.rows] == two and result.rows[0]["game_id"] == "2026_09_JAX_BAL"
    # nothing marked yet (before inactives are announced) is a gap, not "nobody is out"
    for e in entries:
        e["didNotPlay"] = False
    result = espn_mod.EspnSource(xw).inactive_from([(game, {"33": entries})])
    assert result.rows == [] and "no inactives listed yet" in result.gaps[0]


def test_inactive_gap_is_stated_when_none_stored(nfl):
    conn, _ = nfl
    arizona = conn.execute("select player_id from player where nfl_team = 'ARI' and position = 'WR' and active = 1").fetchone()[0]
    fact, gaps = service.inactive_fact(conn, arizona, 11, NOW)
    assert fact is None and gaps and "not available" in gaps[0]


def test_espn_injuries_need_no_athlete_id_field(conn, monkeypatch):
    load_catalog(conn, monkeypatch)
    xw = Crosswalk(conn)
    xw.learn_rosters(csv_rows("roster_weekly_2026_sea_ari_metcalf.csv"))
    result = espn_mod.EspnSource(xw).injury_from(espn_json("injuries_sea_ari.json"), 2026, 11)
    assert result.rows and result.unmapped == 0
    assert {r["designation"] for r in result.rows} <= {"Out", "Doubtful", "Questionable", "IR", "Sus"}


def make_refresh(conn, monkeypatch, espn_error=None):
    """service.refresh with every network source replaced; returns the list of calls made."""
    calls = []

    def espn_fetch(self, kind, season, week, **kw):
        calls.append(("espn", kind))
        raise espn_error

    monkeypatch.setattr(service.EspnSource, "fetch", espn_fetch)
    monkeypatch.setattr(service.NflverseSource, "fetch", lambda self, kind, season, week=None: calls.append(("nflverse", kind)))
    monkeypatch.setattr(service.sleeper_facts, "refresh_stats", lambda *a: calls.append(("sleeper", "stats")))
    monkeypatch.setattr(service.projection_pulls, "pull", lambda *a, **k: calls.append(("sleeper", "projections")))
    return calls


def test_espn_throttling_backs_off_ten_minutes(conn, monkeypatch):
    load_catalog(conn, monkeypatch)
    load_games(conn)
    calls = make_refresh(conn, monkeypatch, SourceError("429", status=429))
    t0 = datetime.now(UTC)
    failed = service.refresh(conn, t0, force=True)
    assert [f["source"] for f in failed] == ["espn"] and failed[0]["code"] == "refresh_failed"
    espn_calls = sum(c[0] == "espn" for c in calls)
    assert espn_calls == 1  # the first throttled call stopped every other ESPN step
    service.refresh(conn, t0 + timedelta(minutes=5), force=True)
    assert sum(c[0] == "espn" for c in calls) == espn_calls
    service.refresh(conn, t0 + timedelta(minutes=11), force=True)
    assert sum(c[0] == "espn" for c in calls) > espn_calls


def test_sources_are_pulled_only_when_due(conn, monkeypatch):
    load_catalog(conn, monkeypatch)
    load_games(conn)
    calls = make_refresh(conn, monkeypatch, SourceError("down"))
    t0 = datetime.now(UTC)
    service.refresh(conn, t0)
    first = len(calls)
    assert first > 0
    service.refresh(conn, t0 + timedelta(seconds=30))
    assert len(calls) == first  # nothing is due after 30 seconds on a normal day
    service.refresh(conn, t0 + timedelta(hours=1))
    assert len(calls) > first


def test_news_label_follows_the_rows_own_source(nfl, monkeypatch):
    conn, _ = nfl
    monkeypatch.setattr(service.sleeper_facts, "refresh_news", lambda *a: True)
    pid = player_id(conn, "Jaxon Smith-Njigba")
    for source, key in (("rotowire", "1"), ("test", "2")):
        conn.execute("insert into news values (?,?,?,?,?,?,?,?,?)",
                     (source, key, pid, f"2026-11-17T0{key}:00:00Z", "t", "d", None, None, "2026-11-17T09:00:00Z"))
    facts, _ = service.news_facts(conn, pid, now=NOW)
    labels = {f["source"]: f["source_label"] for f in facts}
    assert labels == {"sleeper": "Sleeper news (RotoWire)", "test": "test news"}
