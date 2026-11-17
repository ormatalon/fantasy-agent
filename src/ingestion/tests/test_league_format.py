import copy

from ingestion.league_format import league_format, league_info
from ingestion.tests.support import TEST_LEAGUE, fixture


def leagues():
    return {l["name"]: l for l in fixture("user_leagues_2026.json")}


def test_idp_superflex_league():
    fmt = league_format(leagues()["Or Test League"])
    assert fmt["defense"] == "idp"
    assert fmt["superflex"] and fmt["kicker"]
    assert fmt["reception_points"] == 1.0
    assert fmt["te_premium"] == 0.0
    assert fmt["slots"][:6] == ["QB", "RB", "RB", "WR", "WR", "TE"]
    assert fmt["bench_size"] == 5
    assert fmt["num_fantasy_teams"] == 4
    assert fmt["waiver_type"] == "rolling" and fmt["faab_budget"] is None
    assert fmt["trade_deadline_week"] == 11
    assert fmt["playoff_start_week"] == 15
    assert fmt["roster_size"] == 17
    assert fmt["supported"] and fmt["unsupported_reason"] is None


def test_team_def_and_ir_slots():
    fmt = league_format(leagues()["Or 8-Team League"])
    assert fmt["defense"] == "team_def"
    assert not fmt["superflex"]
    assert league_format(leagues()["FantaSteelers"])["ir_slots"] == 2


def test_faab_remaining():
    league = copy.deepcopy(leagues()["Or Test League"])
    league["settings"]["waiver_type"] = 2
    fmt = league_format(league, [{"roster_id": 1, "waiver_budget_used": 16}])
    assert fmt["waiver_type"] == "faab"
    assert fmt["faab_remaining"] == {1: 84}


def test_unsupported_types_are_named():
    for change, reason in [({"type": 1}, "keeper"), ({"type": 2}, "dynasty"), ({"best_ball": 1}, "best_ball")]:
        league = copy.deepcopy(leagues()["Or Test League"])
        league["settings"].update(change)
        info = league_info(league)
        assert (info["supported"], info["unsupported_reason"]) == (False, reason)
        assert (info["selectable"], info["not_selectable_reason"]) == (False, reason)


def test_only_in_season_is_selectable():
    info = {name: league_info(l) for name, l in leagues().items()}
    assert info["Or Test League"]["selectable"]
    pre = info["Or 8-Team League"]
    assert pre["supported"] and not pre["selectable"] and pre["not_selectable_reason"] == "pre_draft"
    assert info["Or Test League"]["league_id"] == TEST_LEAGUE


def test_ir_allowed_designations():
    ls = leagues()
    assert league_format(ls["Or Test League"])["ir_allowed_designations"] == []  # no IR slots
    assert league_format(ls["FantaSteelers"])["ir_allowed_designations"] == ["IR", "NA", "Doubtful", "DNR", "Out", "Sus", "COV"]
    steel = copy.deepcopy(ls["FantaSteelers"])
    steel["settings"].update(reserve_allow_out=0, reserve_allow_doubtful=0, reserve_allow_cov=0)
    assert league_format(steel)["ir_allowed_designations"] == ["IR", "NA", "DNR", "Sus"]
