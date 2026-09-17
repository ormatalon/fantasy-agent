"""Player news from Sleeper's GraphQL endpoint.

UNDOCUMENTED: this is the endpoint the Sleeper app itself uses, not part of
the public REST API the rest of `ingestion/` targets. It can change without
notice, so everything that touches it lives in this one module — a break is
one file to fix, not a hunt. Sleeper's robots.txt is fully permissive
(`Allow: /`, nothing disallowed), unlike the FantasyPros path we declined.

Each item carries a factual `description` (what happened) and an `analysis`
(what it means for fantasy), which is the genuinely useful part: it's
already written for fantasy managers, so the agent can reason over it
directly rather than re-deriving it.
"""

import re
from dataclasses import dataclass

import httpx

GRAPHQL_URL = "https://sleeper.app/graphql"
# Sleeper player ids are numeric strings for players and team abbreviations
# for defenses. Validated rather than escaped because the id is interpolated
# into the query string below (the endpoint is undocumented, so the variable
# types its schema expects aren't published).
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass
class NewsItem:
    news_id: str
    player_id: str
    published: int | None
    source: str | None
    title: str
    description: str
    analysis: str
    url: str


class NewsError(RuntimeError):
    pass


def fetch_player_news(player_id: str, limit: int = 3, timeout: float = 20.0) -> list[NewsItem]:
    if not _SAFE_ID.match(player_id or ""):
        raise NewsError(f"Refusing to query news for suspicious player id: {player_id!r}")
    limit = max(1, min(int(limit), 20))

    query = (
        "query get_player_news {get_player_news"
        f'(sport:"nfl", player_id:"{player_id}", limit: {limit})'
        "{ metadata published source source_key }}"
    )
    resp = httpx.post(
        GRAPHQL_URL,
        json={"operationName": "get_player_news", "variables": {}, "query": query},
        timeout=timeout,
        headers={"Content-Type": "application/json", "User-Agent": "fantasy-agent/0.1"},
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise NewsError(str(payload["errors"])[:200])

    items = []
    for entry in (payload.get("data") or {}).get("get_player_news") or []:
        meta = entry.get("metadata") or {}
        news_id = meta.get("topic_id") or f"{player_id}:{entry.get('source_key')}"
        items.append(
            NewsItem(
                news_id=str(news_id),
                player_id=player_id,
                published=entry.get("published"),
                source=entry.get("source"),
                title=(meta.get("title") or "").strip(),
                description=(meta.get("description") or "").strip(),
                analysis=(meta.get("analysis") or "").strip(),
                url=(meta.get("url") or "").strip(),
            )
        )
    return items
