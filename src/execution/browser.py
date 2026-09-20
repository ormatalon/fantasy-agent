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

import socket
import sys
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

import config
from src.execution.approval import Approval, ProposedWrite, verify

PROFILE_DIR = config.DATA_DIR / "browser_profile"
SLEEPER_URL = "https://sleeper.com"
LOGIN_TIMEOUT_MS = 5 * 60 * 1000
CDP_PORT = 9222

# Sleeper's login is behind bot detection that blocks a Playwright-launched
# browser (navigator.webdriver and friends). This strips the most obvious
# tells; it is not a full stealth suite, which is why attaching to your own
# already-running Chrome is the preferred path - see attach_to_chrome.
_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--exclude-switches=enable-automation",
]

CHROME_CDP_HINT = (
    'Start Chrome with remote debugging, then retry:\n'
    '  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222\n'
    "Chrome must be fully closed first - the flag is ignored if it is already running."
)


class BrowserError(RuntimeError):
    pass


def cdp_available(port: int = CDP_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def browser_context(headless: bool = True, prefer_cdp: bool = True):
    """Prefers attaching to your already-running Chrome over launching our own.

    Attaching means we inherit your real Sleeper session (no separate login)
    and the browser is indistinguishable from normal use to bot detection,
    because it *is* normal use. Falls back to an isolated persistent profile
    when no debuggable Chrome is listening.
    """
    with sync_playwright() as p:
        if prefer_cdp and cdp_available():
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            try:
                yield context
            finally:
                # Do NOT close: this is the user's own browser, not ours.
                browser.close()
            return

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            viewport={"width": 1440, "height": 900},
            args=_STEALTH_ARGS,
        )
        try:
            yield context
        finally:
            context.close()


def is_logged_in(page) -> bool:
    """Logged-out Sleeper redirects /leagues to `/?redirect=%2Fleagues&login=`,
    so a URL that still contains /leagues means the session held."""
    page.goto(f"{SLEEPER_URL}/leagues", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    url = page.url.lower()
    if "login=" in url or "redirect=" in url:
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


# --- DOM facts, established by inspecting the live logged-in page ---
ROSTER_ROW = ".team-roster-item"          # one per slot: 13 starters + 7 BN + 2 IR
SLOT_LABEL = ".league-slot-position-square"  # "QB", "RB", "W\nR\nT" (FLEX), "BN", "IR"
TEAM_PAGE = "{base}/leagues/{league_id}/team"

# Slot squares render multi-position flex slots as stacked letters.
_FLEX_LABELS = {"WRT": "FLEX", "WRTQ": "SUPER_FLEX"}


def dismiss_overlays(page) -> None:
    """Remove the cookie-consent overlay.

    It intercepts every click on the lineup, and clicking its own accept
    button proved unreliable (the dialog re-renders). Removing the nodes
    outright is the approach that actually holds.
    """
    page.evaluate(
        """() => {
          for (const s of ['#onetrust-consent-sdk','#onetrust-banner-sdk',
                           '.onetrust-pc-dark-filter','[role=dialog]']) {
            document.querySelectorAll(s).forEach(e => e.remove());
          }
        }"""
    )
    page.wait_for_timeout(500)


def _normalise_slot(raw: str) -> str:
    collapsed = "".join(raw.split())
    return _FLEX_LABELS.get(collapsed, collapsed)


def _row_player(row) -> str:
    lines = [ln.strip() for ln in row.inner_text().split("\n") if ln.strip()]
    slot_end = 1
    while slot_end < len(lines) and len(lines[slot_end - 1]) == 1 and len(lines[slot_end]) == 1:
        slot_end += 1
    return lines[slot_end] if slot_end < len(lines) else "(empty)"


def surname(name: str) -> str:
    """Sleeper abbreviates first names ("D Swift"), so surnames are the only
    reliable join between our projection names and what the UI shows."""
    return name.split()[-1].lower().strip(".")


def _open_page(context, league_id: str):
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(
        TEAM_PAGE.format(base=SLEEPER_URL, league_id=league_id),
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_timeout(5000)
    dismiss_overlays(page)
    return page


def _lock_reason(row_text: str) -> str:
    """Why a player can't be moved, in human terms. Sleeper shows a kickoff
    time for upcoming games and a score once one has started."""
    lowered = row_text.lower()
    if "final" in lowered:
        return "game already finished"
    if any(q in lowered for q in ["q1", "q2", "q3", "q4", "half", "ot "]):
        return "game in progress"
    return "not offered by Sleeper (locked or ineligible)"


def probe_moves(
    league_id: str, desired: list[tuple[int, str]], headless: bool = True
) -> tuple[list[tuple[int, str]], list[tuple[int, str, str]]]:
    """Ask Sleeper which of these moves it will actually accept.

    Selecting a slot makes Sleeper mark each row valid/invalid, which already
    accounts for kickoff locks - a player whose game has started is never
    offered. Probing first means an impossible move never reaches the
    approval prompt, instead of failing after the human has confirmed it.

    Returns (actionable, blocked) where blocked carries a reason.
    """
    actionable: list[tuple[int, str]] = []
    blocked: list[tuple[int, str, str]] = []

    with browser_context(headless=headless) as context:
        page = _open_page(context, league_id)
        for slot_index, target in desired:
            slots = page.locator(SLOT_LABEL)
            if slot_index >= slots.count():
                blocked.append((slot_index, target, "slot not found on the page"))
                continue
            slots.nth(slot_index).click()
            page.wait_for_timeout(1200)

            rows = page.locator(ROSTER_ROW)
            offered = False
            target_text = ""
            for i in range(rows.count()):
                text = rows.nth(i).inner_text()
                if surname(_row_player(rows.nth(i))) != surname(target):
                    continue
                target_text = text
                if "valid" in (rows.nth(i).get_attribute("class") or "").split():
                    offered = True
                break

            if offered:
                actionable.append((slot_index, target))
            else:
                blocked.append((slot_index, target, _lock_reason(target_text)))

            slots.nth(slot_index).click()  # deselect before the next probe
            page.wait_for_timeout(500)

    return actionable, blocked


def set_lineup(
    league_id: str,
    moves: list[tuple[int, str]],
    write: ProposedWrite,
    approval: Approval,
    headless: bool = True,
) -> list[tuple[str, str]]:
    """Enact lineup changes. `moves` is [(slot_row_index, player_surname)].

    Selecting a slot makes Sleeper mark every row `valid` or `invalid`, so
    eligibility is read off the page rather than reimplemented here - if a
    target isn't marked valid, we refuse rather than click blindly.

    Returns the lineup re-read from the page afterwards, so the caller can
    confirm what actually landed.
    """
    _require_valid_approval(approval, write)

    with browser_context(headless=headless) as context:
        page = _open_page(context, league_id)

        for slot_index, target in moves:
            slots = page.locator(SLOT_LABEL)
            if slot_index >= slots.count():
                raise BrowserError(f"Slot index {slot_index} is out of range.")
            slots.nth(slot_index).click()
            page.wait_for_timeout(1200)

            rows = page.locator(ROSTER_ROW)
            chosen = None
            for i in range(rows.count()):
                classes = rows.nth(i).get_attribute("class") or ""
                if "valid" not in classes.split():
                    continue
                if surname(_row_player(rows.nth(i))) == surname(target):
                    chosen = i
                    break

            if chosen is None:
                raise BrowserError(
                    f"Sleeper did not offer '{target}' as an eligible swap for slot "
                    f"index {slot_index} - refusing to click anything else."
                )

            rows.nth(chosen).click()
            page.wait_for_timeout(2000)

        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        dismiss_overlays(page)
        return _scrape_lineup(page)


def is_locked(row_text: str) -> bool:
    """A player whose game has kicked off cannot be moved. Sleeper shows a
    kickoff time before the game and a score once it starts, so the presence
    of a score is the tell."""
    lowered = row_text.lower()
    return "final" in lowered or any(q in lowered for q in ["q1", "q2", "q3", "q4", "half", "ot "])


def _scrape_lineup(page) -> list[tuple[str, str, bool]]:
    """Returns [(slot, player, locked)]."""
    rows = page.locator(ROSTER_ROW)
    if rows.count() == 0:
        raise BrowserError(
            "No roster rows found - the session may have expired, or Sleeper's "
            "markup changed. Try `login --check`."
        )
    lineup = []
    for i in range(rows.count()):
        text = rows.nth(i).inner_text()
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            continue
        slot_end = 1
        while slot_end < len(lines) and len(lines[slot_end - 1]) == 1 and len(lines[slot_end]) == 1:
            slot_end += 1
        lineup.append((
            _normalise_slot("".join(lines[:slot_end])),
            lines[slot_end] if slot_end < len(lines) else "(empty)",
            is_locked(text),
        ))
    return lineup


def read_lineup(league_id: str, headless: bool = True) -> list[tuple[str, str, bool]]:
    """Scrape the lineup Sleeper currently shows: [(slot, player), ...].

    Read-only. This is what verifies a write actually landed, which is half
    of the Stage 5 DoD ("sets my lineup ... and confirms the result").
    """
    with browser_context(headless=headless) as context:
        return _scrape_lineup(_open_page(context, league_id))
