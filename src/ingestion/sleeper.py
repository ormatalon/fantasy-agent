"""Every call to Sleeper (REST v1 and the undocumented GraphQL) lives here and nowhere else.

Read-only. GraphQL needs a User-Agent (403 without) and always gets variables.
"""

import httpx

REST = "https://api.sleeper.app/v1"
GRAPHQL = "https://sleeper.com/graphql"

client = httpx.Client(headers={"User-Agent": "fantasy-agent/0.1"}, timeout=30, follow_redirects=True)


class SleeperError(Exception):
    pass


def _get(path: str):
    try:
        r = client.get(REST + path)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise SleeperError(f"Sleeper {path}: {e}") from e


def get_state() -> dict:
    return _get("/state/nfl")


def get_user(username: str) -> dict | None:
    """None when no such user (Sleeper answers 200 with null)."""
    return _get(f"/user/{username}")


def get_user_leagues(user_id: str, season: str) -> list[dict]:
    return _get(f"/user/{user_id}/leagues/nfl/{season}")


def get_league(league_id: str) -> dict:
    return _get(f"/league/{league_id}")


def get_league_users(league_id: str) -> list[dict]:
    return _get(f"/league/{league_id}/users")


def get_matchups(league_id: str, week: int) -> list[dict]:
    return _get(f"/league/{league_id}/matchups/{week}")


def get_transactions(league_id: str, week: int) -> list[dict]:
    return _get(f"/league/{league_id}/transactions/{week}")


def get_players_catalog() -> dict[str, dict]:
    return _get("/players/nfl")


ROSTERS_QUERY = """
query($league_id: Snowflake!) {
  league_rosters(league_id: $league_id) {
    roster_id owner_id starters players reserve taxi settings metadata
  }
}
"""


def get_league_rosters(league_id: str) -> list[dict]:
    """Live rosters. The REST endpoint is cached for minutes, GraphQL is not."""
    try:
        r = client.post(GRAPHQL, json={"query": ROSTERS_QUERY, "variables": {"league_id": league_id}})
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPError as e:
        raise SleeperError(f"Sleeper GraphQL league_rosters: {e}") from e
    if body.get("errors") or not body.get("data"):
        raise SleeperError(f"Sleeper GraphQL league_rosters: {body.get('errors')}")
    return body["data"]["league_rosters"]
