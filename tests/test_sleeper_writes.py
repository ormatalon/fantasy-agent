import pytest

from src.execution import approval as approval_mod
from src.execution import sleeper_writes
from src.execution.approval import ApprovalInvalid, ProposedWrite, require_approval
from src.ingestion.sleeper_graphql import SleeperError


@pytest.fixture
def sent(monkeypatch):
    """Stub gql: records every operation and answers read-backs."""
    calls = []
    answers = {
        "matchup_legs": {"matchup_legs": [{"roster_id": 3, "starters": ["a", "b"]}]},
        "league_rosters": {"league_rosters": [{"roster_id": 3, "players": ["a", "b", "new"], "starters": ["a", "b"],
                                               "reserve": ["x"]}]},
        "submit_waiver_claim": {"submit_waiver_claim": {"transaction_id": "t1", "status": "pending"}},
    }

    def gql(operation, query, variables=None, auth=False):
        calls.append({"op": operation, "vars": variables, "auth": auth})
        return answers.get(operation, {})

    monkeypatch.setattr(sleeper_writes, "gql", gql)
    return calls


def approved(write: ProposedWrite):
    return require_approval(write, prompt=lambda _p: approval_mod.CONFIRM_PHRASE, out=lambda _m: None)


def lineup_write(starters):
    return ProposedWrite(action="set_lineup", summary="s", payload={"starters": starters})


def test_nothing_is_written_without_an_approval_for_this_exact_payload(sent):
    approval = approved(lineup_write(["a", "b"]))
    with pytest.raises(ApprovalInvalid):
        sleeper_writes.set_lineup("L", 3, 5, ["b", "a"], lineup_write(["b", "a"]), approval)
    assert sent == []


def test_lineup_sets_the_matchup_leg_and_the_roster_and_reads_both_back(sent):
    write = lineup_write(["a", "b"])
    result = sleeper_writes.set_lineup("L", 3, 5, ["a", "b"], write, approved(write))

    writes = [c for c in sent if c["op"] in ("update_matchup_leg", "roster_update_starters")]
    assert [c["vars"]["starters"] for c in writes] == [["a", "b"], ["a", "b"]]
    assert writes[0]["vars"]["round"] == writes[0]["vars"]["leg"] == 5
    assert all(c["auth"] for c in writes)
    assert result == {"matchup": ["a", "b"], "roster": ["a", "b"]}


def test_a_failed_read_back_is_unverified_not_a_failed_write(sent, monkeypatch):
    real = sleeper_writes.gql

    def flaky(operation, query, variables=None, auth=False):
        if operation in ("matchup_legs", "league_rosters"):
            raise SleeperError("Unauthorized")
        return real(operation, query, variables, auth)

    monkeypatch.setattr(sleeper_writes, "gql", flaky)
    write = lineup_write(["a", "b"])
    assert sleeper_writes.set_lineup("L", 3, 5, ["a", "b"], write, approved(write)) == {"matchup": None, "roster": None}


def test_waiver_claim_sends_paired_arrays_and_the_bid(sent):
    write = ProposedWrite(action="waiver_claim", summary="s", payload={"add": "new", "drop": "b", "bid": 7})
    txn = sleeper_writes.submit_claim("L", 3, "new", "b", 7, write, approved(write))

    v = sent[0]["vars"]
    assert (v["k_adds"], v["v_adds"], v["k_drops"], v["v_drops"]) == (["new"], [3], ["b"], [3])
    assert (v["k_settings"], v["v_settings"]) == (["waiver_bid"], [7])
    assert txn["transaction_id"] == "t1"


def test_waiver_claim_without_a_bid_sends_no_bid(sent):
    write = ProposedWrite(action="waiver_claim", summary="s", payload={"add": "new"})
    sleeper_writes.submit_claim("L", 3, "new", None, None, write, approved(write))
    assert "k_settings" not in sent[0]["vars"]
    assert sent[0]["vars"]["k_drops"] == []


def test_add_drop_is_a_free_agent_transaction_read_back_live(sent):
    write = ProposedWrite(action="add_drop", summary="s", payload={"add": "new"})
    players = sleeper_writes.add_drop("L", 3, "new", None, write, approved(write))
    assert sent[0]["op"] == "league_create_transaction" and sent[0]["vars"]["type"] == "free_agent"
    assert "new" in players


def test_ir_is_set_as_a_complete_list(sent):
    write = ProposedWrite(action="set_ir", summary="s", payload={"reserve": ["x"]})
    assert sleeper_writes.set_reserve("L", 3, ["x"], write, approved(write)) == ["x"]
    assert sent[0]["vars"]["reserve"] == ["x"]


def test_one_refused_lineup_write_does_not_hide_the_other(sent, monkeypatch):
    real = sleeper_writes.gql

    def refuse_matchup(operation, query, variables=None, auth=False):
        if operation == "update_matchup_leg":
            raise SleeperError("no matchup this week")
        return real(operation, query, variables, auth)

    monkeypatch.setattr(sleeper_writes, "gql", refuse_matchup)
    write = lineup_write(["a", "b"])
    result = sleeper_writes.set_lineup("L", 3, 5, ["a", "b"], write, approved(write))
    assert isinstance(result["matchup"], SleeperError)
    assert result["roster"] == ["a", "b"]
