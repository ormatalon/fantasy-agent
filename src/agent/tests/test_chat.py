"""The question loop against a fake OpenRouter replaying recorded responses (no live calls)."""

import json

import httpx
import pytest
from langsmith.utils import tracing_is_enabled

from agent import chat, llm
from storage.db import get_setting, set_settings
from web.errors import ApiError
from .conftest import KEY, TEST_LEAGUE, recorded

STARTERS = ("live_starters_1_tool_call.json", "live_starters_2_answer.json")
# Recorded 2026-11-17 (live acceptance run, OpenRouter user id removed): the provider overloaded, three times in a
# row. The HTTP status was not captured; the client retries it on 200 and on 5xx alike.
UPSTREAM_ERROR_200 = recorded("live_upstream_overloaded.json")
QUOTA_429 = {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}}


def ask(conn, text, retry_of=None):
    events = []
    qid = chat.start_question(conn, text, retry_of)
    chat.run_question(conn, qid, lambda event, payload: events.append((event, payload)))
    return qid, events


def test_tool_then_answer_events_rows_and_sources(league, openrouter):
    openrouter.reply(*STARTERS)
    qid, events = ask(league, "Who are my starters?")
    names = [(e, p.get("kind"), p.get("status")) for e, p in events]
    assert names == [
        ("start", None, None),
        ("step", "refresh", "running"), ("step", "refresh", "done"),
        ("step", "model", "running"), ("step", "model", "done"),
        ("step", "tool", "running"), ("step", "tool", "done"),
        ("step", "model", "running"), ("step", "model", "done"),
        ("answer", None, None),
    ]
    tool_step = events[5][1]
    assert tool_step["label"] == "Reading your roster" and tool_step["tool"] == "get_my_roster"
    assert [p["id"] for e, p in events if e == "step"] == [1, 1, 2, 2, 3, 3, 4, 4]
    elapsed = [p["elapsed_ms"] for e, p in events if e == "step"]
    assert elapsed == sorted(elapsed)
    answer = events[-1][1]
    assert answer["text"] == recorded(STARTERS[1])["choices"][0]["message"]["content"]
    assert answer["model_turns"] == 2 and answer["refresh"]["failed"] == []
    assert {s["label"] for s in answer["sources"]} == {"Sleeper rosters", "Sleeper players catalog"}

    rows = chat.trace(league, TEST_LEAGUE, qid)["rows"]
    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"]
    first, tool, final = rows[1]["meta"], rows[2], rows[3]["meta"]
    assert first["tool_calls"] == [{"id": first["tool_calls"][0]["id"], "name": "get_my_roster", "arguments": {},
                                   "result_id": tool["id"]}]
    assert first["context"].startswith("TODAY: ") and first["turn"] == 1
    assert tool["content"].startswith("SUMMARY: My lineup as set in Sleeper")
    assert tool["meta"] | {"tool_call_id": None} == {"question_id": qid, "tool_call_id": None, "name": "get_my_roster",
                                                     "arguments": {}, "ok": True}
    assert final["model_turns"] == 2 and final["sources"] == answer["sources"] and final["usage"]["total_tokens"] > 0


def test_model_sees_prompt_context_and_its_tool_result(league, openrouter):
    openrouter.reply(*STARTERS)
    ask(league, "Who are my starters?")
    first, second = openrouter.bodies()
    system = first["messages"][0]["content"]
    assert system.startswith(chat.system_prompt()) and "\nCONTEXT\nTODAY: " in system and "GAPS: " in system
    assert len(first["tools"]) == 17 and first["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool"]
    assert second["messages"][3]["content"].startswith("SUMMARY: My lineup as set in Sleeper")


def test_the_key_appears_in_no_row_event_or_model_message(league, openrouter):
    openrouter.reply(*STARTERS)
    _, events = ask(league, "Who are my starters?")
    stored = json.dumps(chat.trace(league, TEST_LEAGUE)) + json.dumps(chat.history(league, TEST_LEAGUE))
    sent = json.dumps(openrouter.bodies())
    for text in (stored, json.dumps(events), sent):
        assert KEY not in text and "sk-or" not in text
    assert "reasoning" not in stored  # the model's reasoning text is never stored or sent back


def test_history_endpoint_shape_and_start_fresh(league, openrouter):
    openrouter.reply(*STARTERS)
    qid, events = ask(league, "Who are my starters?")
    h = chat.history(league, TEST_LEAGUE)
    assert h["running"] is None
    assert [(m["role"], m["status"]) for m in h["messages"]] == [("user", "ok"), ("assistant", "ok")]
    assert h["messages"][1]["id"] == events[-1][1]["message_id"] and h["messages"][1]["sources"]
    chat.start_fresh(league, "another-league")
    assert len(chat.history(league, TEST_LEAGUE)["messages"]) == 2
    chat.start_fresh(league, TEST_LEAGUE)
    assert chat.history(league, TEST_LEAGUE)["messages"] == []


def test_sixth_turn_has_no_tools_and_answers(league, openrouter):
    openrouter.reply(*[STARTERS[0]] * 5, STARTERS[1])
    _, events = ask(league, "Who are my starters?")
    bodies = openrouter.bodies()
    assert len(bodies) == 6 and all("tools" in b for b in bodies[:5]) and "tools" not in bodies[5]
    assert events[-1][0] == "answer" and events[-1][1]["model_turns"] == 6


def test_too_many_turns_when_the_last_turn_writes_a_tool_call_as_text(league, openrouter):
    openrouter.reply(*[STARTERS[0]] * 5, "live_tool_call_as_text_no_tools_turn.json")
    qid, events = ask(league, "Show me the league standings.")
    assert events[-1] == ("error", {"code": "too_many_turns", "message": events[-1][1]["message"],
                                    "max_model_turns": 6, "question_kept": True})
    user = chat.history(league, TEST_LEAGUE)["messages"][0]
    assert user["status"] == "failed" and user["error"]["code"] == "too_many_turns"


def test_history_keeps_the_latest_result_of_each_tool_in_full(league, openrouter):
    openrouter.reply(*STARTERS)
    ask(league, "Who are my starters?")
    openrouter.reply(*STARTERS)
    ask(league, "And now?")
    msgs = openrouter.bodies()[-1]["messages"]
    tool_msgs = [m["content"] for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0].startswith("SUMMARY: ") and "\n" not in tool_msgs[0]  # older result: SUMMARY line only
    assert "QB: Josh Allen" in tool_msgs[1]  # latest result in full
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "assistant", "user", "assistant", "tool"]


def test_two_calls_to_one_tool_in_one_question_both_stay_in_full(league, openrouter):
    """"Start A or B?" calls one tool twice; the answer turn needs both results whole."""
    openrouter.reply(STARTERS[0], STARTERS[0], STARTERS[1])
    ask(league, "Who are my starters?")
    tool_msgs = [m["content"] for m in openrouter.bodies()[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and all("QB: Josh Allen" in t for t in tool_msgs)


def test_roster_dependent_result_is_outdated_after_my_roster_changes(league, openrouter, fake):
    openrouter.reply(*STARTERS)
    ask(league, "Who are my starters?")
    fake.rosters[0]["players"] = [p for p in fake.rosters[0]["players"] if p != "4866"] + ["12526"]
    openrouter.reply("live_standings_1_tool_call.json", "live_standings_2_answer.json")
    ask(league, "Show me the league standings.")
    last = openrouter.bodies()[-1]["messages"]
    tool_msgs = [m["content"] for m in last if m["role"] == "tool"]
    assert tool_msgs[0].startswith("[outdated: my roster changed at ") and tool_msgs[0].endswith("call the tool again]")
    assert "RECENT CHANGES TO MY ROSTER: " in last[0]["content"] and "Tetairoa McMillan" in last[0]["content"]


def test_upstream_error_on_http_200_is_retried(league, openrouter):
    openrouter.reply(UPSTREAM_ERROR_200, *STARTERS)
    _, events = ask(league, "Who are my starters?")
    assert events[-1][0] == "answer" and len(openrouter.bodies()) == 3


def test_provider_error_after_two_retries_keeps_the_question(league, openrouter):
    openrouter.reply(UPSTREAM_ERROR_200, UPSTREAM_ERROR_200, UPSTREAM_ERROR_200)
    qid, events = ask(league, "Who are my starters?")
    assert len(openrouter.bodies()) == 3
    assert events[-1][0] == "error" and events[-1][1]["code"] == "provider_error"
    assert events[-1][1]["question_kept"] is True
    assert [e for e, p in events if e == "step" and p["status"] == "error"]  # the model step shows the failure
    assert chat.history(league, TEST_LEAGUE)["messages"][0]["status"] == "failed"


def test_overloaded_provider_says_busy_on_200_and_on_503(league, openrouter):
    """Same code, provider_error; the message tells the manager to try again in a minute."""
    for status in (200, 503):
        openrouter.reply(UPSTREAM_ERROR_200, UPSTREAM_ERROR_200, UPSTREAM_ERROR_200, status=status)
        error = ask(league, "Who are my starters?")[1][-1][1]
        assert (error["code"], error["message"]) == (
            "provider_error", "The model provider is busy right now. Try again in a minute."), status


def test_other_provider_errors_keep_the_general_message(league, openrouter):
    other = {"error": {"code": 502, "message": "Provider returned error"}}
    openrouter.reply(UPSTREAM_ERROR_200, UPSTREAM_ERROR_200, other)  # the last failure decides
    error = ask(league, "Who are my starters?")[1][-1][1]
    assert error["message"] == "Something went wrong, so there is no answer. Nothing was changed in Sleeper."


def test_5xx_is_retried_401_is_not(league, openrouter):
    openrouter.reply({"error": {"message": "bad gateway"}}, status=502)
    openrouter.reply(*STARTERS)
    assert ask(league, "Who are my starters?")[1][-1][0] == "answer"
    openrouter.requests.clear()
    openrouter.reply("error_401_invalid_key.json", status=401)
    _, events = ask(league, "Who are my starters?")
    assert len(openrouter.bodies()) == 1 and events[-1][1]["code"] == "key_invalid"


def test_mid_question_429_with_the_quota_used_up_fails_fast(league, openrouter):
    openrouter.reply(QUOTA_429, status=429)
    qid = chat.start_question(league, "Who are my starters?")
    openrouter.key_info["data"]["free_model_daily_requests"] |= {"used": 50, "remaining": 0}
    events = []
    chat.run_question(league, qid, lambda e, p: events.append((e, p)))
    assert len(openrouter.bodies()) == 1
    assert events[-1][1] | {"message": ""} == {"code": "quota_exceeded", "message": "", "remaining": 0, "limit": 50,
                                               "model": "nvidia/nemotron-3-ultra-550b-a55b:free", "resets_at": None,
                                               "question_kept": True}


def test_429_with_quota_left_is_a_rate_limit_and_retried(league, openrouter):
    openrouter.reply(QUOTA_429, status=429)
    openrouter.reply(*STARTERS)
    assert ask(league, "Who are my starters?")[1][-1][0] == "answer"


def test_quota_pre_check_spends_no_model_request(league, openrouter):
    openrouter.key_info["data"]["free_model_daily_requests"] |= {"used": 50, "remaining": 0}
    with pytest.raises(ApiError) as e:
        chat.start_question(league, "Who are my starters?")
    assert e.value.code == "quota_exceeded" and e.value.extra["question_kept"] is True
    assert openrouter.bodies() == [] and chat.running() is None
    failed = chat.history(league, TEST_LEAGUE)["messages"][0]
    assert failed["status"] == "failed" and failed["error"]["resets_at"] is None


def test_paid_model_skips_the_quota_check(league, openrouter):
    set_settings(league, {"model": "some/paid-model"})
    openrouter.key_info["data"]["free_model_daily_requests"] |= {"remaining": 0}
    openrouter.reply(*STARTERS)
    assert ask(league, "Who are my starters?")[1][-1][0] == "answer"
    assert not [r for r in openrouter.requests if r.url.path.endswith("/key")]


def test_retry_of_removes_the_failed_question_once_accepted(league, openrouter):
    openrouter.key_info["data"]["free_model_daily_requests"] |= {"remaining": 0}
    with pytest.raises(ApiError):
        chat.start_question(league, "Who are my starters?")
    failed_id = chat.history(league, TEST_LEAGUE)["messages"][0]["id"]
    openrouter.key_info["data"]["free_model_daily_requests"] |= {"remaining": 10}
    openrouter.reply(*STARTERS)
    ask(league, "Who are my starters?", retry_of=failed_id)
    assert [m["status"] for m in chat.history(league, TEST_LEAGUE)["messages"]] == ["ok", "ok"]


def test_failed_question_is_not_sent_to_the_model_later(league, openrouter):
    openrouter.reply(UPSTREAM_ERROR_200, UPSTREAM_ERROR_200, UPSTREAM_ERROR_200)
    ask(league, "First question")
    openrouter.reply(*STARTERS)
    ask(league, "Who are my starters?")
    users = [m["content"] for m in openrouter.bodies()[-1]["messages"] if m["role"] == "user"]
    assert users == ["Who are my starters?"]


def test_checks_before_the_stream(league, openrouter):
    set_settings(league, {"openrouter_api_key": ""})
    with pytest.raises(ApiError) as e:
        chat.start_question(league, "hi")
    assert (e.value.code, e.value.status, e.value.extra) == ("key_missing", 409, {"question_kept": True})
    set_settings(league, {"openrouter_api_key": KEY, "current_league_id": ""})
    with pytest.raises(ApiError) as e:
        chat.start_question(league, "hi")
    assert e.value.code == "no_league"


def test_html_page_instead_of_the_key_counter_fails_the_question_and_frees_the_slot(league, openrouter, monkeypatch):
    """A captive portal answers 200 with HTML: a provider error, and the next question is not blocked."""
    portal = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>Sign in to the Wi-Fi</html>", headers={"content-type": "text/html"})))
    monkeypatch.setattr(llm, "http", portal)
    with pytest.raises(ApiError) as e:
        chat.start_question(league, "Who are my starters?")
    assert e.value.code == "provider_error" and chat.running() is None
    assert chat.history(league, TEST_LEAGUE)["messages"][0]["error"]["code"] == "provider_error"


def test_any_failure_in_the_checks_frees_the_slot_and_fails_the_question(league, openrouter, monkeypatch):
    def broken(*args):
        raise KeyError("data")

    check_quota = llm.check_quota
    monkeypatch.setattr(llm, "check_quota", broken)
    with pytest.raises(ApiError) as e:
        chat.start_question(league, "Who are my starters?")
    assert (e.value.code, e.value.status) == ("internal_error", 500) and chat.running() is None
    failed = chat.history(league, TEST_LEAGUE)["messages"][0]
    assert failed["status"] == "failed" and failed["error"]["code"] == "internal_error"
    monkeypatch.setattr(llm, "check_quota", check_quota)
    openrouter.reply(*STARTERS)
    assert ask(league, "Who are my starters?")[1][-1][0] == "answer"


def test_one_question_at_a_time(league, openrouter):
    qid = chat.start_question(league, "first")
    assert chat.running()["question_id"] == qid
    with pytest.raises(ApiError) as e:
        chat.start_question(league, "second")
    assert e.value.code == "question_in_progress"
    openrouter.reply(*STARTERS)
    chat.run_question(league, qid, lambda e, p: None)
    assert chat.running() is None


def test_switch_league_mid_question_changes_the_next_context(league, openrouter):
    call = recorded("live_starters_1_tool_call.json")
    tc = call["choices"][0]["message"]["tool_calls"][0]
    tc["function"] = {"name": "switch_league", "arguments": json.dumps({"league_name": "FantaSteelers"})}
    openrouter.reply(call, "live_starters_2_answer.json")
    ask(league, "Switch to FantaSteelers")
    first, second = openrouter.bodies()
    assert "LEAGUE: Or Test League" in first["messages"][0]["content"]
    assert "LEAGUE: FantaSteelers" in second["messages"][0]["content"]
    assert get_setting(league, "current_league_id") != TEST_LEAGUE


def test_tracing_is_off_by_default():
    assert not tracing_is_enabled()
