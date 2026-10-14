"""Read-only Sleeper API client.

Sleeper's documented REST API is public and keyless: https://docs.sleeper.com/
It is Cloudflare-cached (rosters up to ~10 minutes), which is fine for
everything except rosters, so those come from the live GraphQL store
instead. Writes go through execution/sleeper_writes.py.
"""

import httpx

from config import SLEEPER_API_BASE
from src.ingestion.sleeper_graphql import SleeperError, gql

LIVE_ROSTERS_QUERY = (
    "query league_rosters($league_id: Snowflake!) {"
    " league_rosters(league_id: $league_id) { roster_id owner_id players starters reserve settings } }"
)


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
        """Every roster as Sleeper has it right now. A move made in the app
        a minute ago can still be missing from the cached REST copy, which
        is only the fallback for when GraphQL is down."""
        try:
            return gql("league_rosters", LIVE_ROSTERS_QUERY, {"league_id": league_id}).get("league_rosters") or []
        except (SleeperError, httpx.HTTPError):
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

    def get_trending(self, kind: str = "add", lookback_hours: int = 24, limit: int = 25) -> list[dict]:
        """Most-added or most-dropped players across all of Sleeper.

        `kind` is "add" or "drop". Returns [{player_id, count}], where count is
        the number of leagues site-wide - a market-sentiment signal our own
        projections can't produce.
        """
        return self._get(
            f"/players/nfl/trending/{kind}?lookback_hours={lookback_hours}&limit={limit}"
        ) or []

    def get_league_drafts(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/drafts") or []

    def get_draft(self, draft_id: str) -> dict:
        return self._get(f"/draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks") or []


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
