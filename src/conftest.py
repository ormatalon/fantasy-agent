"""Fixtures shared by the web and ingestion tests (helpers live in ingestion/tests/support.py)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from ingestion import sleeper
from ingestion.nfl import service
from ingestion.tests.support import FakeSleeper
from web.main import app


@pytest.fixture
def fake(monkeypatch):
    f = FakeSleeper()
    monkeypatch.setattr(sleeper, "client", httpx.Client(transport=httpx.MockTransport(f.handler)))
    return f


@pytest.fixture
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("FANTASY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FANTASY_POLL", "0")
    monkeypatch.setattr(service, "refresh", lambda conn, **kw: [])  # no NFL source is called from the API tests
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.fixture
def conn(tmp_path, monkeypatch, fake):
    from storage import db

    monkeypatch.setenv("FANTASY_DATA_DIR", str(tmp_path))
    c = db.connect()
    db.migrate(c)
    yield c
    c.close()


