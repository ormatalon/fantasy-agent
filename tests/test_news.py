import sqlite3

import pytest

from src.ingestion import storage
from src.ingestion.news import NewsError, NewsItem, fetch_player_news


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    return conn


def item(news_id: str, player_id: str, published: int, title: str = "T") -> NewsItem:
    return NewsItem(
        news_id=news_id, player_id=player_id, published=published, source="fantasy_pros",
        title=title, description="D", analysis="A", url="http://example.test",
    )


def test_rejects_a_suspicious_player_id_rather_than_interpolating_it():
    # The id is interpolated into the GraphQL query string, so it is validated
    # rather than trusted.
    with pytest.raises(NewsError):
        fetch_player_news('4984" ) { evil } #')


def test_news_is_returned_newest_first():
    conn = make_conn()
    storage.save_player_news(conn, [
        item("n1", "p1", 100, "older"),
        item("n2", "p1", 300, "newest"),
        item("n3", "p1", 200, "middle"),
    ])

    rows = storage.get_player_news(conn, "p1", limit=3)

    assert [r["title"] for r in rows] == ["newest", "middle", "older"]


def test_news_is_limited_per_player():
    conn = make_conn()
    storage.save_player_news(conn, [item(f"n{i}", "p1", i) for i in range(10)])

    assert len(storage.get_player_news(conn, "p1", limit=2)) == 2


def test_resaving_the_same_item_updates_rather_than_duplicates():
    conn = make_conn()
    storage.save_player_news(conn, [item("n1", "p1", 100, "first title")])
    storage.save_player_news(conn, [item("n1", "p1", 100, "corrected title")])

    rows = storage.get_player_news(conn, "p1", limit=5)

    assert len(rows) == 1
    assert rows[0]["title"] == "corrected title"


def test_news_is_scoped_to_the_requested_player():
    conn = make_conn()
    storage.save_player_news(conn, [item("n1", "p1", 100), item("n2", "p2", 100)])

    assert len(storage.get_player_news(conn, "p1", limit=5)) == 1
