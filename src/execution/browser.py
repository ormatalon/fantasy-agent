"""Sleeper web UI automation.

Sleeper has no write API (PLAN.md §2), so enacting a move means driving the
real web app. Two deliberate choices:

- **Your installed Chrome via `channel="chrome"`**, not a downloaded
  Chromium. Avoids a 150MB fetch that was timing out here anyway, and a
  real browser build behaves more like one.
- **A persistent profile directory**, so you log in by hand once and the
  session survives. We never see, store, or transmit your Sleeper password.
  The profile lives under data/ which is gitignored.

Nothing in this module writes anything without an `Approval` from
execution/approval.py, verified against the exact payload the human saw.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

import config
from src.execution.approval import Approval, ProposedWrite, verify

PROFILE_DIR = config.DATA_DIR / "browser_profile"
SLEEPER_URL = "https://sleeper.com"
LOGIN_TIMEOUT_MS = 5 * 60 * 1000


class BrowserError(RuntimeError):
    pass


@contextmanager
def browser_context(headless: bool = True):
    """A persistent Chrome context. Reuses the saved login if there is one."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            viewport={"width": 1440, "height": 900},
        )
        try:
            yield context
        finally:
            context.close()


def is_logged_in(page) -> bool:
    """Logged-out Sleeper bounces you to a login/landing view; logged-in lands
    on the app shell with the league nav present."""
    page.goto(f"{SLEEPER_URL}/leagues", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    url = page.url.lower()
    if "login" in url or url.rstrip("/").endswith("sleeper.com"):
        return False
    return "/leagues" in url


def login() -> bool:
    """Open a real browser window and wait for the human to log in.

    Intentionally manual: Sleeper accounts commonly have email-code or 2FA
    steps, and scripting a credential entry would mean handling the password
    ourselves. Logging in by hand once is both simpler and safer.
    """
    with browser_context(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SLEEPER_URL, wait_until="domcontentloaded", timeout=60000)

        print("A browser window is open. Log in to Sleeper there.", file=sys.stderr)
        print("Waiting for you to reach your leagues page...", file=sys.stderr)

        try:
            page.wait_for_url("**/leagues**", timeout=LOGIN_TIMEOUT_MS)
        except Exception:
            print("Did not reach the leagues page - login not saved.", file=sys.stderr)
            return False

        page.wait_for_timeout(3000)
        print(f"Logged in. Session saved to {PROFILE_DIR}.", file=sys.stderr)
        return True


def check_session(headless: bool = True) -> bool:
    with browser_context(headless=headless) as context:
        page = context.pages[0] if context.pages else context.new_page()
        return is_logged_in(page)


def _require_valid_approval(approval: Approval, write: ProposedWrite) -> None:
    """Every write path calls this first. Kept as its own function so the
    check is impossible to omit accidentally when adding a new action."""
    verify(approval, write)
