"""The artifact is rebuilt when missing or stale, and a failed rebuild falls back to what is stored."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from decisions import artifact_view
from decisions.tests.support import LEAGUE, REAL_ENSURE, seed_projections
from projections import artifact


def fail(*a, **k):
    raise ConnectionError("Sleeper unreachable")


def test_a_fresh_artifact_is_not_rebuilt(league, monkeypatch):
    monkeypatch.setattr(artifact, "build", fail)
    assert REAL_ENSURE(league, LEAGUE) == []


def test_a_stale_artifact_is_rebuilt_and_saved(league, monkeypatch):
    calls = []
    meta = {"league_id": LEAGUE, "scoring_hash": "x", "unprojected_scoring_items": "[]", "weights": "{}", "computed_at": "2026-11-17T00:00:00Z"}
    monkeypatch.setattr(artifact, "build", lambda conn, league_id, season, week, sources, weights: calls.append((season, week, weights)) or ([], meta))
    monkeypatch.setattr(artifact, "save", lambda conn, rows, m: calls.append("saved"))
    monkeypatch.setattr(artifact_view, "default_sources", lambda conn, season: [])
    later = datetime.now(UTC) + timedelta(hours=3)
    assert REAL_ENSURE(league, LEAGUE, now=later) == []
    assert calls == [(2026, 11, {"sleeper": 0.9, "nflverse": 0.1}), "saved"]


def test_a_changed_league_scoring_makes_the_artifact_stale(league, monkeypatch):
    league.execute("update league set scoring_settings = replace(scoring_settings, '\"rec\": 1.0', '\"rec\": 0.5')")
    called = []
    monkeypatch.setattr(artifact_view, "default_sources", lambda conn, season: called.append(1) or [])
    monkeypatch.setattr(artifact, "build", lambda *a, **k: called.append(2) or ([], {"league_id": LEAGUE}))
    monkeypatch.setattr(artifact, "save", lambda *a: None)
    REAL_ENSURE(league, LEAGUE)
    assert called == [1, 2]


def test_a_failed_rebuild_uses_the_stored_artifact_and_says_how_old_it_is(league, monkeypatch):
    monkeypatch.setattr(artifact, "build", fail)
    monkeypatch.setattr(artifact_view, "default_sources", lambda conn, season: [])
    warnings = REAL_ENSURE(league, LEAGUE, now=datetime.now(UTC) + timedelta(hours=3))
    assert len(warnings) == 1 and "could not be refreshed (Sleeper unreachable)" in warnings[0] and "using the ones stored at" in warnings[0]


def test_a_failed_rebuild_with_nothing_stored_is_an_error(league, monkeypatch):
    league.execute("delete from projection_meta")
    monkeypatch.setattr(artifact, "build", fail)
    monkeypatch.setattr(artifact_view, "default_sources", lambda conn, season: [])
    with pytest.raises(ConnectionError):
        REAL_ENSURE(league, LEAGUE)


def test_seeding_helper_matches_what_ensure_considers_fresh(league):
    seed_projections(league, default=7.0)
    assert not artifact_view.is_stale(league, LEAGUE, json.loads(league.execute("select scoring_settings from league").fetchone()[0]), 11, datetime.now(UTC))


def test_a_changed_injury_designation_makes_the_artifact_stale(league):
    scoring = json.loads(league.execute("select scoring_settings from league").fetchone()[0])
    stale = lambda: artifact_view.is_stale(league, LEAGUE, scoring, 11, datetime.now(UTC))  # noqa: E731
    assert not stale()
    league.execute("insert into nfl_injury(player_id, season, week, source, designation, as_of, fetched_at)"
                   " values ('4984', 2026, 11, 'nflverse', 'Doubtful', '2026-11-19T20:00:00Z', '2026-11-19T20:01:00Z')")
    assert stale()  # an official report arrived
    artifact_view.set_settings(league, {f"projection_injury_hash:{LEAGUE}": artifact_view.injury_hash(league, 2026)})
    assert not stale()
    league.execute("update player set injury_status = 'Out' where player_id = '8228'")
    assert stale()  # the catalog changed
