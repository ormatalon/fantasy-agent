"""Player news from Sleeper's GraphQL API (`get_player_news`, public).

UNDOCUMENTED: see `sleeper_graphql.py`, which every GraphQL call goes
through. Sleeper's robots.txt is fully permissive (`Allow: /`, nothing
disallowed), unlike the FantasyPros path we declined.

Each item carries a factual `description` (what happened) and an `analysis`
(what it means for fantasy), which is the genuinely useful part: it's
already written for fantasy managers, so the agent can reason over it
directly rather than re-deriving it.
"""

from dataclasses import dataclass

from src.ingestion.sleeper_graphql import gql

QUERY = (
    "query get_player_news($player_id: String!, $limit: Int) {"
    ' get_player_news(sport: "nfl", player_id: $player_id, limit: $limit)'
    " { metadata published source source_key } }"
)


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


def fetch_player_news(player_id: str, limit: int = 3) -> list[NewsItem]:
    limit = max(1, min(int(limit), 20))
    data = gql("get_player_news", QUERY, {"player_id": player_id, "limit": limit})

    items = []
    for entry in data.get("get_player_news") or []:
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
