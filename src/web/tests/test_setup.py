from fastapi.testclient import TestClient

from web import setup
from web.errors import ApiError
from web.main import app
from storage import db
from ingestion.tests.support import TEST_LEAGUE


def accept_key(monkeypatch):
    monkeypatch.setattr(setup, "verify", lambda key, base: None)


def test_empty_state(client):
    s = client.get("/api/state").json()
    assert s["setup_complete"] is False and s["key_set"] is False and s["current_league"] is None
    assert s["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_models(client):
    models = client.get("/api/models").json()["models"]
    assert sum(m["recommended"] for m in models) == 1
    assert all({"id", "name", "free", "note"} <= m.keys() for m in models)


def test_one_league_is_chosen_automatically(client, fake, monkeypatch):
    accept_key(monkeypatch)
    fake.leagues = [l for l in fake.leagues if l["league_id"] == TEST_LEAGUE]
    r = client.put("/api/settings", json={"sleeper_username": "OrMatalon", "openrouter_api_key": "sk-or-secret"})
    body = r.json()
    assert r.status_code == 200 and body["key"] == "saved"
    assert body["current_league"]["name"] == "Or Test League"
    assert body["current_league"]["this_week"] == 11 and body["current_league"]["last_completed_week"] == 10
    assert body["setup_complete"] is True and body["needs_league_choice"] is False


def test_several_leagues_ask_then_switch_by_name(client):
    body = client.put("/api/settings", json={"sleeper_username": "OrMatalon"}).json()
    assert body["current_league"] is None and body["needs_league_choice"] is True
    assert [l["selectable"] for l in body["leagues"]] == [False, True, True]
    r = client.put("/api/league", json={"league_name": "or test"})  # unique prefix, any case
    assert r.status_code == 200 and r.json()["current_league"]["name"] == "Or Test League"
    assert client.get("/api/panel").status_code == 200


def test_unknown_league_lists_the_real_ones(client):
    client.put("/api/settings", json={"sleeper_username": "OrMatalon"})
    r = client.put("/api/league", json={"league_name": "Nope"})
    err = r.json()["error"]
    assert r.status_code == 404 and err["code"] == "league_not_found"
    assert err["available"] == ["Or 8-Team League", "Or Test League", "FantaSteelers"]


def test_pre_draft_league_is_not_playable(client):
    client.put("/api/settings", json={"sleeper_username": "OrMatalon"})
    r = client.put("/api/league", json={"league_name": "Or 8-Team League"})
    err = r.json()["error"]
    assert r.status_code == 409 and err["code"] == "league_not_playable" and err["reason"] == "pre_draft"


def test_unsupported_type_is_named(client, fake):
    for league in fake.leagues:
        if league["name"] == "FantaSteelers":
            league["settings"]["type"] = 2
    client.put("/api/settings", json={"sleeper_username": "OrMatalon"})
    r = client.put("/api/league", json={"league_name": "FantaSteelers"})
    err = r.json()["error"]
    assert r.status_code == 409 and err["code"] == "league_unsupported" and err["unsupported_reason"] == "dynasty"
    listed = {l["name"]: l for l in client.get("/api/leagues").json()["leagues"]}
    assert listed["FantaSteelers"]["not_selectable_reason"] == "dynasty"


def test_username_not_found_and_sleeper_down(client, fake):
    r = client.put("/api/settings", json={"sleeper_username": "nobody_here"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "username_not_found"
    assert client.get("/api/state").json()["username"] is None  # nothing was saved
    fake.down = True
    r = client.get("/api/leagues", params={"username": "OrMatalon"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "sleeper_unreachable"


def test_rejected_key_is_not_saved(client, monkeypatch):
    def reject(key, base):
        raise ApiError("key_rejected", 422, "OpenRouter did not accept this key.")

    monkeypatch.setattr(setup, "verify", reject)
    r = client.put("/api/settings", json={"openrouter_api_key": "bad"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "key_rejected"
    assert client.get("/api/state").json()["key_set"] is False


def test_key_is_stored_but_never_returned(client, monkeypatch):
    accept_key(monkeypatch)
    secret = "sk-or-v1-very-secret-value"
    put = client.put("/api/settings", json={"openrouter_api_key": secret, "model": "google/gemma-4-31b-it:free"})
    assert put.json()["key"] == "saved"
    again = client.put("/api/settings", json={"model": "google/gemma-4-31b-it:free"})
    assert again.json()["key"] == "unchanged"
    for r in (put, again, client.get("/api/state"), client.get("/api/models")):
        assert secret not in r.text
    state = client.get("/api/state").json()
    assert state["key_set"] is True and state["model"] == "google/gemma-4-31b-it:free"
    assert db.get_setting(db.connect(), "openrouter_api_key") == secret


def test_unknown_model(client):
    r = client.put("/api/settings", json={"model": "x/y"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "model_unknown"


def test_only_localhost_host_header(client):
    with TestClient(app, base_url="http://evil.example") as other:
        assert other.get("/api/health").status_code == 400
    assert client.get("/api/health").json()["ok"] is True


def test_placeholder_page_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and "Fantasy Agent" in r.text


def test_key_check_uses_the_base_url_setting_and_env(client, monkeypatch):
    import httpx

    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        return httpx.Response(401)

    monkeypatch.setattr("web.key_check.httpx.get", fake_get)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://stub-env/api/v1")
    r = client.put("/api/settings", json={"openrouter_api_key": "k"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "key_rejected"
    r = client.put("/api/settings", json={"openrouter_api_key": "k", "openrouter_base_url": "http://stub-body/api/v1/"})
    assert r.status_code == 422
    assert urls == ["http://stub-env/api/v1/key", "http://stub-body/api/v1/key"]


def test_stored_base_url_wins_over_env(client, monkeypatch):
    import httpx

    urls = []
    monkeypatch.setattr("web.key_check.httpx.get", lambda url, **kw: urls.append(url) or httpx.Response(200))
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://stub-env/api/v1")
    client.put("/api/settings", json={"openrouter_api_key": "k", "openrouter_base_url": "http://stub-stored/api/v1"})
    client.put("/api/settings", json={"openrouter_api_key": "k2"})
    assert urls == ["http://stub-stored/api/v1/key", "http://stub-stored/api/v1/key"]


def test_delete_key(client, monkeypatch):
    accept_key(monkeypatch)
    client.put("/api/settings", json={"openrouter_api_key": "k"})
    assert client.get("/api/state").json()["key_set"] is True
    for _ in range(2):  # idempotent
        r = client.delete("/api/settings/key")
        assert r.status_code == 200 and r.json()["key_set"] is False
    assert db.get_setting(db.connect(), "openrouter_api_key") is None
