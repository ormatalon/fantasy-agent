import pytest

import config
from src.ingestion import sleeper_graphql
from src.ingestion.sleeper_graphql import SleeperError


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def sent(monkeypatch):
    requests = []

    def post(url, json, headers, timeout):
        requests.append({"url": url, "json": json, "headers": headers})
        return FakeResponse({"data": {"ok": True}})

    monkeypatch.setattr(sleeper_graphql.httpx, "post", post)
    return requests


def test_always_sends_a_user_agent_and_no_token_for_public_reads(sent, monkeypatch):
    monkeypatch.setattr(config, "SLEEPER_TOKEN", "secret")
    assert sleeper_graphql.gql("op", "query op { ok }") == {"ok": True}
    assert sent[0]["headers"]["User-Agent"]
    assert "Authorization" not in sent[0]["headers"]


def test_sends_the_token_only_when_asked(sent, monkeypatch):
    monkeypatch.setattr(config, "SLEEPER_TOKEN", "secret")
    sleeper_graphql.gql("op", "mutation op { ok }", auth=True)
    assert sent[0]["headers"]["Authorization"] == "Bearer secret"


def test_variables_are_sent_as_variables_not_in_the_query(sent):
    sleeper_graphql.gql("op", "query op($id: String!) { ok }", {"id": '4984" ) { evil } #'})
    assert sent[0]["json"]["variables"] == {"id": '4984" ) { evil } #'}
    assert "evil" not in sent[0]["json"]["query"]


def test_a_missing_token_fails_before_any_request(sent, monkeypatch):
    monkeypatch.setattr(config, "SLEEPER_TOKEN", "")
    with pytest.raises(SleeperError, match="SLEEPER_TOKEN"):
        sleeper_graphql.gql("op", "mutation op { ok }", auth=True)
    assert sent == []


def test_graphql_errors_raise(monkeypatch):
    monkeypatch.setattr(
        sleeper_graphql.httpx, "post",
        lambda *a, **kw: FakeResponse({"data": None, "errors": [{"message": "Unauthorized"}]}),
    )
    with pytest.raises(SleeperError, match="Unauthorized"):
        sleeper_graphql.gql("op", "query op { ok }")


def test_rosters_come_from_the_live_store(monkeypatch):
    from src.ingestion import sleeper

    monkeypatch.setattr(sleeper, "gql", lambda op, q, v: {"league_rosters": [{"roster_id": 1, "players": ["live"]}]})
    client = sleeper.SleeperClient()
    monkeypatch.setattr(client, "_get", lambda path: pytest.fail("REST is cached; must not be read"))
    assert client.get_rosters("L1") == [{"roster_id": 1, "players": ["live"]}]


def test_rosters_fall_back_to_rest_when_graphql_fails(monkeypatch):
    from src.ingestion import sleeper

    def down(*a):
        raise SleeperError("down")

    monkeypatch.setattr(sleeper, "gql", down)
    client = sleeper.SleeperClient()
    monkeypatch.setattr(client, "_get", lambda path: [{"roster_id": 1, "players": ["cached"]}])
    assert client.get_rosters("L1") == [{"roster_id": 1, "players": ["cached"]}]
