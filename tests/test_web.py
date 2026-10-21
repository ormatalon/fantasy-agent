import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

import config
from src.agent.graph import build_graph
from src.ingestion import storage
from src.web import app as web
from tests.test_agent import make_ctx, seeded_conn

SETTING_KEYS = ("SLEEPER_USERNAME", "SLEEPER_TOKEN", "OPENROUTER_API_KEY", "OPENROUTER_MODEL")


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Everything the app writes goes to tmp_path, and every setting it
    # changes (module globals and env vars) is restored afterwards.
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fantasy.db")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.env")
    for key in SETTING_KEYS:
        monkeypatch.setenv(key, "")
        monkeypatch.setattr(config, key, "")
    monkeypatch.setattr(web, "_session", {})
    return TestClient(web.app)


def test_status_before_setup(client):
    body = client.get("/api/status").json()
    assert body["configured"] is False
    assert body["league"] is None


def test_settings_are_saved_applied_and_the_key_never_returned(client):
    body = client.post("/api/settings", json={"sleeper_username": "me", "openrouter_api_key": "sk-secret"}).json()

    assert body["configured"] is True
    assert body["sleeper_username"] == "me"
    assert "sk-secret" not in str(body)
    assert config.OPENROUTER_API_KEY == "sk-secret"
    assert "sk-secret" in config.SETTINGS_PATH.read_text()


def test_blank_secret_keeps_the_saved_one(client):
    client.post("/api/settings", json={"sleeper_username": "me", "openrouter_api_key": "sk-secret"})
    client.post("/api/settings", json={"sleeper_username": "me", "openrouter_model": "some/model"})

    assert config.OPENROUTER_API_KEY == "sk-secret"
    assert config.OPENROUTER_MODEL == "some/model"


def test_changing_username_asks_for_a_league_again(client):
    client.post("/api/settings", json={"sleeper_username": "me", "openrouter_api_key": "k"})
    storage.set_state(web._conn(), "active_league_id", "L1")

    client.post("/api/settings", json={"sleeper_username": "someone_else"})

    assert not storage.get_state(web._conn(), "active_league_id")


def test_sync_without_a_league_is_a_readable_400(client):
    resp = client.post("/api/sync", json={})
    assert resp.status_code == 400
    assert "league" in resp.json()["detail"].lower()


@pytest.fixture
def chat_client(client, monkeypatch):
    conn = seeded_conn()
    web._session["conn"] = conn

    def fake_open(_conn, new=False, in_app=False):
        assert in_app, "the web app must ask for the app prompt (no CLI commands)"
        llm = lambda messages: AIMessage(content=f"answer to: {messages[-1].content}")
        return build_graph(llm, [], lambda: ""), make_ctx(conn)

    monkeypatch.setattr(web, "open_chat_agent", fake_open)
    return client


def test_chat_answers_and_keeps_history(chat_client):
    resp = chat_client.post("/api/chat", json={"question": "who starts?"})

    assert resp.json() == {"answer": "answer to: who starts?"}
    assert chat_client.get("/api/chat").json() == [
        {"role": "user", "content": "who starts?"},
        {"role": "agent", "content": "answer to: who starts?"},
    ]


def test_new_chat_clears_history(chat_client):
    chat_client.post("/api/chat", json={"question": "hi"})
    chat_client.post("/api/chat/new")
    assert chat_client.get("/api/chat").json() == []


def test_an_unexpected_error_reaches_the_page_with_its_reason(client, monkeypatch):
    def boom(_conn, new=False, in_app=False):
        raise RuntimeError("disk full")

    monkeypatch.setattr(web, "open_chat_agent", boom)
    resp = TestClient(web.app, raise_server_exceptions=False).post("/api/chat", json={"question": "hi"})

    assert resp.status_code == 500
    assert "disk full" in resp.json()["detail"]


@pytest.fixture
def looping_client(client, monkeypatch):
    """A model that calls a tool forever, and a tool that presses Stop and
    records what the progress endpoint showed while the question ran."""
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool

    conn = seeded_conn()
    web._session["conn"] = conn
    seen_steps = []

    @tool
    def get_projections(position: str) -> str:
        """Fake projections."""
        seen_steps.append(client.get("/api/chat/progress").json()["step"])
        client.post("/api/chat/stop")
        return "Puka Nacua 23.6"

    def llm(messages):
        last_question = [m for m in messages if isinstance(m, HumanMessage)][-1].content
        if last_question == "after stop":
            return AIMessage(content="fresh answer")
        return AIMessage(content="", tool_calls=[{"name": "get_projections", "args": {"position": "RB"}, "id": f"c{len(messages)}"}])

    graph = build_graph(llm, [get_projections], lambda: "")
    monkeypatch.setattr(web, "open_chat_agent", lambda _c, new=False, in_app=False: (graph, make_ctx(conn)))
    return client, seen_steps


def test_stop_ends_a_looping_question_and_the_chat_still_works(looping_client):
    client, seen_steps = looping_client

    assert client.post("/api/chat", json={"question": "loop"}).json() == {"answer": "Stopped."}
    assert seen_steps == ["checking projections (RB)"]
    assert client.get("/api/chat/progress").json() == {"step": ""}

    # The stopped turn left a valid conversation: the next question runs.
    assert client.post("/api/chat", json={"question": "after stop"}).json() == {"answer": "fresh answer"}
    assert [m["content"] for m in client.get("/api/chat").json()] == ["loop", "Stopped.", "after stop", "fresh answer"]
