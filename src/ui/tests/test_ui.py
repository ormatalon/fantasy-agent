import asyncio

import pytest
from nicegui import ui

from agent.tests.conftest import KEY, recorded
from ingestion import sync
from storage.db import set_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent import chat
from ui import backend
from ui.widgets import CSS
from ui.app import allow_local_hosts_only

pytestmark = pytest.mark.anyio

STARTERS = ("live_starters_1_tool_call.json", "live_starters_2_answer.json")


async def see(user, text):
    """should_see, waiting up to 10 s: the agent runs in a thread."""
    await user.should_see(text, retries=100)


async def ask(user, text):
    user.find(ui.textarea).type(text)
    user.find("Send").click()


async def test_setup_shows_until_username_league_and_key_are_set(conn, user):
    await user.open("/")
    await user.should_see("Set up Fantasy Agent")
    await user.should_not_see("Ask about your league")


async def test_setup_lists_leagues_and_names_why_one_cannot_be_chosen(conn, fake, user):
    found = sync.discover("OrMatalon")
    set_settings(conn, {"sleeper_username": found["username"], "leagues": found["leagues"]})
    await user.open("/")
    await user.should_see("Or Test League")
    await user.should_see("Or 8-Team League")
    await user.should_see("Not playable yet. The draft has not happened.")
    await user.should_not_see("Ask about your league")


async def test_a_saved_key_is_never_shown_back(league, user):
    await user.open("/")
    await user.should_see("Ask about your league")
    user.find("Settings").click()
    await user.should_see("Key saved on this computer")
    await user.should_not_see(KEY)


async def test_question_streams_to_an_answer_with_sources_and_history_persists(league, openrouter, user):
    openrouter.reply(*STARTERS)
    await user.open("/")
    await user.should_see("Ask about your league")
    await ask(user, "Who are my starters?")
    answer = recorded(STARTERS[1])["choices"][0]["message"]["content"]
    await see(user, answer.split("\n")[0][:30])
    await see(user, "Sources:")
    await user.should_not_see(KEY)
    assert [m["role"] for m in backend.history()["messages"]] == ["user", "assistant"]
    await user.open("/")  # history persists across a page load
    await user.should_see("Who are my starters?")
    await user.should_see("Sources:")


async def test_start_fresh_clears_history(league, openrouter, user):
    openrouter.reply(*STARTERS)
    await user.open("/")
    await ask(user, "Who are my starters?")
    await see(user, "Sources:")
    user.find(kind=ui.button, content="Start fresh").click()
    await user.should_see("Start fresh in Or Test League?")
    user.find(marker="confirm-fresh").click()
    await see(user, "Lineups, waiver pickups")
    assert backend.history()["messages"] == []


async def test_rejected_key_keeps_the_question_and_offers_ask_again(league, openrouter, user):
    openrouter.reply("error_401_invalid_key.json", status=401)
    await user.open("/")
    await ask(user, "Who are my starters?")
    await see(user, "OpenRouter refused the saved key.")
    await user.should_see("Ask again")
    box = user.find(ui.textarea).elements.pop()
    assert box.value == "Who are my starters?" and not box.enabled  # locked until the key is fixed in Settings
    await user.should_see("Open settings")


async def test_a_newer_key_unlocks_the_input_after_a_refused_one(league, openrouter, user):
    openrouter.reply("error_401_invalid_key.json", status=401)
    await user.open("/")
    await ask(user, "Who are my starters?")
    await see(user, "OpenRouter refused the saved key.")
    set_settings(league, {"key_saved_at": "2999-01-01T00:00:00Z"})  # what Settings records when a key is saved
    await user.open("/")
    await see(user, "OpenRouter refused the saved key.")
    assert user.find(ui.textarea).elements.pop().enabled


async def test_missing_key_is_an_error_and_the_question_is_kept(league, user):
    await user.open("/")
    backend.remove_key()  # removed in another window
    await ask(user, "Who are my starters?")
    await see(user, "Add your OpenRouter key to ask questions.")
    await user.should_see("Open settings")
    assert user.find(ui.textarea).elements.pop().value == "Who are my starters?"


async def test_panel_shows_roster_injury_chips_and_no_matchup(league, monkeypatch, user):
    panel = backend.panel_data()
    starters = [s for s in panel["slots"] if s["role"] == "starter" and s["player"]]
    starters[0]["player"]["injury_status"] = "IR"
    starters[1]["player"]["injury_status"] = "Questionable"
    panel["matchup"] = None
    monkeypatch.setattr(backend, "panel_data", lambda force=False: panel)
    await user.open("/")
    await user.should_see(panel["fantasy_team"]["name"])
    await user.should_see(starters[0]["player"]["name"])
    await user.should_see("IR")
    await user.should_see("Q")
    await user.should_see("No matchup this week")
    await user.should_see("Starters who may not play")


def test_a_request_with_another_host_is_refused():
    api = FastAPI()
    allow_local_hosts_only(api)
    api.get("/api/health")(lambda: {"ok": True})
    assert TestClient(api, base_url="http://evil.example").get("/api/health").status_code == 400
    assert TestClient(api, base_url="http://localhost").get("/api/health").status_code == 200


def failure(source):
    return {"code": "refresh_failed", "source": source, "message": "m", "since": "2026-11-17T15:42:00Z"}


async def test_a_sleeper_failure_gets_the_sleeper_banner(league, monkeypatch, user):
    panel = backend.panel_data()
    panel["refresh"]["failed"] = [failure("sleeper")]
    monkeypatch.setattr(backend, "panel_data", lambda force=False: panel)
    await user.open("/")
    await see(user, "Could not refresh from Sleeper.")
    await user.should_see("A move you made after that is not included.")


async def test_an_espn_failure_gets_a_neutral_line_not_the_sleeper_one(league, monkeypatch, user):
    panel = backend.panel_data()
    panel["refresh"]["failed"] = [failure("espn")]
    monkeypatch.setattr(backend, "panel_data", lambda force=False: panel)
    await user.open("/")
    await see(user, "NFL data from ESPN last updated")
    await user.should_not_see("Could not refresh from Sleeper.")
    await user.should_not_see("A move you made after that")


async def test_a_sleeper_stats_failure_gets_a_neutral_line_not_the_moves_banner(league, monkeypatch, user):
    panel = backend.panel_data()
    panel["refresh"]["failed"] = [failure("sleeper_stats")]
    monkeypatch.setattr(backend, "panel_data", lambda force=False: panel)
    await user.open("/")
    await see(user, "Sleeper stats last updated")
    await user.should_not_see("Could not refresh from Sleeper.")
    await user.should_not_see("A move you made after that")


def selected(user, marker):
    return "selected" in user.find(marker=marker).elements.pop().classes


async def test_leagues_are_option_cards_and_a_choice_is_never_preselected(conn, fake, user):
    fake.leagues = fake.leagues[1:]  # the two in-season leagues
    found = sync.discover("OrMatalon")
    set_settings(conn, {"sleeper_username": found["username"], "leagues": found["leagues"]})
    await user.open("/")
    await user.should_see("You are in several leagues.")
    first, second = [l["league_id"] for l in found["leagues"] if l["selectable"]]
    assert not selected(user, f"league-{first}") and not selected(user, f"league-{second}")
    user.find(marker=f"league-{second}").click()
    await asyncio.sleep(0.3)  # the click handler runs as a task
    assert selected(user, f"league-{second}") and not selected(user, f"league-{first}")


async def test_a_league_that_cannot_be_chosen_is_inline_and_disabled(conn, fake, user):
    found = sync.discover("OrMatalon")
    set_settings(conn, {"sleeper_username": found["username"], "leagues": found["leagues"]})
    await user.open("/")
    await user.should_see("Not playable yet. The draft has not happened.")
    pre_draft = next(l["league_id"] for l in found["leagues"] if not l["selectable"])
    user.find(marker=f"league-{pre_draft}").click()
    assert not selected(user, f"league-{pre_draft}")


async def test_settings_model_picker_has_free_and_recommended_chips(league, user):
    await user.open("/")
    user.find("Settings").click()
    await user.should_see("Recommended")
    await user.should_see("Free")
    await user.should_see("nvidia/nemotron-3-ultra-550b-a55b:free")


async def test_settings_says_when_a_question_is_already_running(league, monkeypatch, user):
    monkeypatch.setattr(chat, "_running", {"question_id": 1, "started_at": "2026-11-17T15:00:00Z", "t0": 0})
    await user.open("/")
    user.find("Settings").click()
    await see(user, "A question is already running.")


async def test_chip_meaning_is_a_tooltip(league, monkeypatch, user):
    panel = backend.panel_data()
    next(s for s in panel["slots"] if s["role"] == "starter" and s["player"])["player"]["injury_status"] = "IR"
    monkeypatch.setattr(backend, "panel_data", lambda force=False: panel)
    await user.open("/")
    await see(user, "Injured reserve")
    assert user.find(kind=ui.tooltip).elements


def test_focus_ring_and_reduced_motion_rules_are_in_the_css():
    assert ":focus-visible" in CSS and "prefers-reduced-motion" in CSS and "animation:none" in CSS


async def test_a_provider_error_shows_the_servers_message(league, user):
    from agent import history
    from ingestion.tests.support import TEST_LEAGUE
    busy = "The model provider is busy right now. Try again in a minute."
    history.add(league, TEST_LEAGUE, "user", "Who are my starters?", {
        "status": "failed", "error": {"code": "provider_error", "message": busy, "question_kept": True}})
    await user.open("/")
    await see(user, busy)
    await user.should_see("Nothing was changed in Sleeper.")
    await user.should_see("Ask again")
    await user.should_not_see("Something went wrong, so there is no answer.")
