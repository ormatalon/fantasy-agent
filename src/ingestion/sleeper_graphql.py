"""Sleeper's GraphQL API: the one the Sleeper web app itself uses.

UNDOCUMENTED: Sleeper publishes no docs for it and can change it without
notice, so every GraphQL call in the project goes through `gql` here - a
break is one file to fix. Three facts it relies on, all checked live:

- Sleeper answers 403 when no User-Agent is sent.
- Some reads are public (`league_rosters`, `get_player_news`); others and
  every write need the account token (`config.SLEEPER_TOKEN`).
- Unlike the REST API, it is not Cloudflare-cached, so it sees a move made
  in the app a second ago.

Only operations written out in this codebase are ever sent. The same
endpoint also serves Sleeper's betting and payment operations; nothing here
may call them.
"""

import httpx

import config

GRAPHQL_URL = "https://sleeper.com/graphql"
USER_AGENT = "fantasy-agent/0.1"


class SleeperError(RuntimeError):
    pass


def gql(operation: str, query: str, variables: dict | None = None, auth: bool = False, timeout: float = 20.0) -> dict:
    """Run one named operation and return its `data`. Raises SleeperError on
    any GraphQL error, and before sending anything when `auth` is asked for
    but no token is set."""
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if auth:
        if not config.SLEEPER_TOKEN:
            raise SleeperError(
                "Set SLEEPER_TOKEN in .env first (see .env.example) - this needs your Sleeper login."
            )
        headers["Authorization"] = f"Bearer {config.SLEEPER_TOKEN}"
    resp = httpx.post(
        GRAPHQL_URL,
        json={"operationName": operation, "variables": variables or {}, "query": query},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        messages = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise SleeperError(f"Sleeper {operation}: {messages}")
    return payload.get("data") or {}
