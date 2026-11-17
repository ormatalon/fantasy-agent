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


def get_trending(kind: str = "add", hours: int = 24, limit: int = 25) -> list[dict]:
    """Most added or dropped players over the last hours, as Sleeper gives them: [{player_id, count}].

    kind is "add" or "drop". Public REST; a defense shows up with its abbreviation as player_id.
    """
    try:
        r = client.get(f"{REST}/players/nfl/trending/{kind}", params={"lookback_hours": hours, "limit": limit})
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise SleeperError(f"Sleeper trending {kind}: {e}") from e


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


def get_stats(season: int, week: int) -> list[dict]:
    """Actual stats of one week (undocumented REST, same keys as the projections)."""
    try:
        r = client.get(f"https://api.sleeper.app/stats/nfl/{season}/{week}", params={"season_type": "regular"})
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise SleeperError(f"Sleeper stats {season}/{week}: {e}") from e


NEWS_QUERY = """
query($sport: String, $player_id: String, $limit: Int) {
  get_player_news(sport: $sport, player_id: $player_id, limit: $limit) {
    player_id source source_key published metadata
  }
}
"""


def get_player_news(player_id: str, limit: int = 5) -> list[dict]:
    """Latest news items for one player (RotoWire prose), newest first."""
    try:
        r = client.post(
            GRAPHQL,
            json={"query": NEWS_QUERY, "variables": {"sport": "nfl", "player_id": player_id, "limit": limit}},
        )
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPError as e:
        raise SleeperError(f"Sleeper GraphQL get_player_news: {e}") from e
    if body.get("errors") or not body.get("data"):
        raise SleeperError(f"Sleeper GraphQL get_player_news: {body.get('errors')}")
    return body["data"]["get_player_news"]


def get_projections(season: int, week: int | None = None) -> list[dict]:
    """Projections of one week, or the whole-season ones when week is None (undocumented REST).

    Rows: player_id, week, opponent, last_modified (ms) and stats (the projected stat lines).
    """
    path = f"{season}/{week}" if week else str(season)
    try:
        r = client.get(f"https://api.sleeper.app/projections/nfl/{path}", params={"season_type": "regular"})
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise SleeperError(f"Sleeper projections {path}: {e}") from e
