"""POST /api/chat as server-sent events, with recorded OpenRouter responses behind the base-URL setting."""

import json
import time

import httpx

from agent import chat, llm
from agent.tests.conftest import KEY, recorded
from ingestion.tests.support import TEST_LEAGUE
from web import setup

STUB = "http://openrouter.stub/api/v1"
STARTERS = ("live_starters_1_tool_call.json", "live_starters_2_answer.json")


class Stub:
    """OpenRouter stand-in: the /key counter and queued completions, all recorded from the real service."""

    def __init__(self):
        self.completions, self.hosts = [], set()
        self.key_info = recorded("key_info_free_tier.json")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hosts.add(request.url.host)
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json=self.key_info)
        status, body = self.completions.pop(0)
        return httpx.Response(status, json=body)

    def queue(self, *names):
        self.completions += [(200, recorded(n)) for n in names]


def start(client, fake, monkeypatch):
    """Or Test League chosen, a key saved and the base URL pointed at the stub."""
    stub = Stub()
    monkeypatch.setattr(llm, "http", httpx.Client(transport=httpx.MockTransport(stub.handler)))
    monkeypatch.setattr(chat, "_running", None)
    monkeypatch.setattr(setup, "verify", lambda key, base: None)
    fake.leagues = [l for l in fake.leagues if l["league_id"] == TEST_LEAGUE]
    client.put("/api/settings", json={"sleeper_username": "OrMatalon", "openrouter_api_key": KEY,
                                      "openrouter_base_url": STUB})
    return stub


def stream(client, text="Who are my starters?", **extra):
    """(status, events) of one POST /api/chat; events are (name, payload). On an HTTP error: (status, body)."""
    events = []
    with client.stream("POST", "/api/chat", json={"text": text, **extra}) as r:
        if r.status_code != 200:
            r.read()
            return r.status_code, r.json()
        name = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    return 200, events


def test_streams_steps_then_the_answer_and_stores_it(client, fake, monkeypatch):
    stub = start(client, fake, monkeypatch)
    stub.queue(*STARTERS)
    status, events = stream(client)
    assert status == 200 and stub.hosts == {"openrouter.stub"}  # the base-URL setting is used
    names = [e for e, _ in events]
    assert names[0] == "start" and names[-1] == "answer" and names.count("answer") == 1
    assert {p["kind"] for e, p in events if e == "step"} == {"refresh", "model", "tool"}
    answer = events[-1][1]
    assert answer["text"] == recorded(STARTERS[1])["choices"][0]["message"]["content"]
    assert answer["sources"] and answer["model_turns"] == 2

    history = client.get("/api/chat/history")
    body = history.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["running"] is None and body["messages"][1]["text"] == answer["text"]
    assert KEY not in history.text


def test_trace_has_tool_calls_context_and_no_key(client, fake, monkeypatch):
    stub = start(client, fake, monkeypatch)
    stub.queue(*STARTERS)
    stream(client)
    trace = client.get("/api/chat/history", params={"detail": "trace"})
    rows = trace.json()["rows"]
    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1]["meta"]["tool_calls"][0]["name"] == "get_my_roster" and rows[1]["meta"]["context"]
    assert rows[2]["meta"]["question_id"] == rows[0]["id"]
    assert KEY not in trace.text
    only = client.get("/api/chat/history", params={"detail": "trace", "question_id": rows[0]["id"]})
    assert len(only.json()["rows"]) == 4


def test_start_fresh_deletes_this_leagues_history(client, fake, monkeypatch):
    stub = start(client, fake, monkeypatch)
    stub.queue(*STARTERS)
    stream(client)
    assert client.delete("/api/chat/history").status_code == 204
    assert client.get("/api/chat/history").json()["messages"] == []


def test_checks_before_the_stream_are_http_errors_and_the_question_is_kept(client, fake, monkeypatch):
    start(client, fake, monkeypatch)
    client.delete("/api/settings/key")
    status, body = stream(client, "Hello?")
    assert status == 409 and body["error"]["code"] == "key_missing" and body["error"]["question_kept"] is True
    first = client.get("/api/chat/history").json()["messages"][0]
    assert first["text"] == "Hello?" and first["status"] == "failed" and first["error"]["code"] == "key_missing"


def test_daily_quota_stops_before_any_model_request(client, fake, monkeypatch):
    stub = start(client, fake, monkeypatch)
    stub.key_info["data"]["free_model_daily_requests"].update(used=50, remaining=0)
    status, body = stream(client)
    assert status == 429 and body["error"]["code"] == "quota_exceeded"
    assert body["error"]["remaining"] == 0 and body["error"]["question_kept"] is True and stub.completions == []


def test_a_failure_during_the_question_is_an_error_event_and_retry_replaces_it(client, fake, monkeypatch):
    stub = start(client, fake, monkeypatch)
    stub.completions.append((401, recorded("error_401_invalid_key.json")))
    status, events = stream(client)
    assert status == 200 and events[-1][0] == "error" and events[-1][1]["code"] == "key_invalid"
    assert events[-1][1]["question_kept"] is True
    failed = client.get("/api/chat/history").json()["messages"][0]
    assert failed["status"] == "failed"
    stub.queue(*STARTERS)
    status, events = stream(client, retry_of=failed["id"])
    assert events[-1][0] == "answer"
    assert [m["role"] for m in client.get("/api/chat/history").json()["messages"]] == ["user", "assistant"]


def test_dropped_connection_still_finishes_the_question(client, fake, monkeypatch):
    stub = start(client, fake, monkeypatch)
    stub.queue(*STARTERS)
    with client.stream("POST", "/api/chat", json={"text": "Who are my starters?"}) as r:
        next(r.iter_lines())  # read one line, then drop the connection
    for _ in range(200):
        if chat.running() is None:
            break
        time.sleep(0.05)
    messages = client.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"] and messages[1]["text"]


def test_one_question_at_a_time(client, fake, monkeypatch):
    start(client, fake, monkeypatch)
    monkeypatch.setattr(chat, "_running", {"question_id": 1, "started_at": "t", "t0": 0})
    status, body = stream(client)
    assert status == 409 and body["error"]["code"] == "question_in_progress"
