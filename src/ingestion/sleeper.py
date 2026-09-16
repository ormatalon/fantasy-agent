"""Read-only Sleeper API client.

Sleeper's read API is public and keyless: https://docs.sleeper.com/
No writes are possible through it (see PLAN.md Stage 5 for execution).
"""

from typing import Callable

import httpx

from config import SLEEPER_API_BASE


class SleeperError(RuntimeError):
    pass


class SleeperClient:
    def __init__(self, base_url: str = SLEEPER_API_BASE, timeout: float = 15.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SleeperClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, path: str):
        resp = self._client.get(path)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # --- lookups ---

    def get_user(self, username: str) -> dict:
        user = self._get(f"/user/{username}")
        if user is None:
            raise SleeperError(f"No Sleeper user found for username '{username}'")
        return user

    def get_user_leagues(self, user_id: str, season: str) -> list[dict]:
        return self._get(f"/user/{user_id}/leagues/nfl/{season}") or []

    def get_nfl_state(self) -> dict:
        return self._get("/state/nfl")

    # --- league data ---

    def get_league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def get_rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters") or []

    def get_league_users(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/users") or []

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/matchups/{week}") or []

    def get_transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/transactions/{week}") or []

    def get_players(self) -> dict:
        """Full NFL players catalog. Large payload — cache aggressively."""
        return self._get("/players/nfl") or {}

    def get_league_drafts(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/drafts") or []

    def get_draft(self, draft_id: str) -> dict:
        return self._get(f"/draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks") or []


def resolve_league(
    client: SleeperClient,
    username: str,
    season: str,
    choose: Callable[[list[dict]], dict] | None = None,
) -> tuple[dict, dict]:
    """Resolve username -> the league to operate on.

    Returns (user, league). If the user is in more than one league for the
    season, `choose` is called with the candidate list and must return the
    selected one — never guess which league to use.
    """
    user = client.get_user(username)
    leagues = client.get_user_leagues(user["user_id"], season)

    if not leagues:
        raise SleeperError(
            f"No leagues found for '{username}' in season {season}. "
            "Check SLEEPER_USERNAME/SLEEPER_SEASON in .env."
        )

    if len(leagues) == 1:
        return user, leagues[0]

    if choose is None:
        raise SleeperError(
            f"'{username}' is in {len(leagues)} leagues for {season}; "
            "a `choose` callback is required to disambiguate."
        )
    return user, choose(leagues)


def prompt_choose_league(leagues: list[dict]) -> dict:
    """Default interactive chooser for the CLI."""
    print(f"You're in {len(leagues)} leagues this season:")
    for i, league in enumerate(leagues, start=1):
        print(f"  {i}. {league['name']} (league_id={league['league_id']})")
    while True:
        raw = input(f"Which league? [1-{len(leagues)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(leagues):
            return leagues[int(raw) - 1]
        print("Invalid choice, try again.")
