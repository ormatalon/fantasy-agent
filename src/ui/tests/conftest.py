"""UI tests: NiceGUI's user simulation on the page, the Or Test League from captured Sleeper responses and
recorded OpenRouter replies (agent/tests/fixtures/openrouter). No live calls.

NiceGUI's own `user` fixture needs pytest-asyncio and a main file; this runs the page function directly
and lets anyio's pytest plugin (already installed) run the async fixture and tests.
"""

import pytest
from nicegui.testing.user_simulation import user_simulation

from agent.tests.conftest import KEY, isolated, league, openrouter, recorded  # noqa: F401  (fixtures)
from ui.app import index


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def user(anyio_backend, monkeypatch):
    monkeypatch.setenv("FANTASY_POLL", "0")
    async with user_simulation(root=index) as u:
        yield u
