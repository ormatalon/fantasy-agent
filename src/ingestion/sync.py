"""Pulls league state from Sleeper into local storage.

One code path for the CLI `sync` command, the agent's `sync_league` tool and
the agent's auto-sync, so "synced" always means the same thing: league
settings, users, every roster, every week's matchups and transactions, the
players catalog (which is where injury designations live) when it's due, and
news for my roster.

Rosters also have a light refresh of their own (`refresh_rosters`), run
before every agent question, because a move made in Sleeper a minute ago
must not wait for the next full sync.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable

import config
from src.ingestion import storage
from src.ingestion.news import fetch_player_news
from src.ingestion.sleeper import SleeperClient, SleeperError, prompt_choose_league

Log = Callable[[str], None]


def log_to_stderr(message: str) -> None:
    """For agent paths, where stdout must carry only the answer."""
    print(message, file=sys.stderr, flush=True)


def _age_hours(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)) / timedelta(hours=1)


def _age_seconds(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)).total_seconds()


def _roster_snapshot(conn, league_id: str, user_id: str | None) -> dict | None:
    roster_id = storage.get_roster_id_for_user(conn, league_id, user_id) if user_id else None
    row = storage.get_roster(conn, league_id, roster_id) if roster_id is not None else None
    if not row:
        return None
    return {
        "players": set(json.loads(row["players"])),
        "starters": json.loads(row["starters"]),
        # NULL in rows saved before IR slots were stored: unknown, not empty.
        "reserve": None if row["reserve"] is None else set(json.loads(row["reserve"])),
    }


def _describe_roster_change(conn, before: dict, after: dict) -> str | None:
    added = sorted(storage.player_name(conn, pid) for pid in after["players"] - before["players"])
    dropped = sorted(storage.player_name(conn, pid) for pid in before["players"] - after["players"])
    parts = []
    if added:
        parts.append(f"added {', '.join(added)}")
    if dropped:
        parts.append(f"dropped {', '.join(dropped)}")
    if parts:
        return "; ".join(parts)
    reserve_moved = before["reserve"] is not None and before["reserve"] != after["reserve"]
    if before["starters"] != after["starters"] or reserve_moved:
        return "lineup changed"
    return None


def save_rosters_tracking_changes(conn, league_id: str, rosters: list[dict], user_id: str | None) -> None:
    """Save every roster, and when MY roster differs from the stored one,
    record when and how (`roster_changed_at:<league>`, `roster_change:<league>`).
    The agent uses these to tell the model what changed and to hide roster
    results it fetched before the change. A first save is not a change."""
    before = _roster_snapshot(conn, league_id, user_id)
    storage.save_rosters(conn, league_id, rosters)
    after = _roster_snapshot(conn, league_id, user_id)
    if before is None or after is None:
        return
    change = _describe_roster_change(conn, before, after)
    if change:
        storage.set_state(conn, f"roster_changed_at:{league_id}", storage.now_iso())
        storage.set_state(conn, f"roster_change:{league_id}", change)


def _select_leagues(
    leagues: list[dict], league_query: str | None, all_leagues: bool, choose: Callable[[list[dict]], dict]
) -> list[dict]:
    if all_leagues:
        return leagues
    if league_query:
        # Checked even with a single league: asking for a league that doesn't
        # exist must fail, not silently land on the one that does.
        by_id = [lg for lg in leagues if lg["league_id"] == league_query]
        matches = by_id or [lg for lg in leagues if league_query.lower() in (lg.get("name") or "").lower()]
        if len(matches) != 1:
            names = ", ".join(lg.get("name") or lg["league_id"] for lg in leagues)
            raise SleeperError(f"'{league_query}' doesn't identify exactly one of your leagues: {names}")
        return matches
    return leagues if len(leagues) == 1 else [choose(leagues)]


def _sync_one_league(conn, client: SleeperClient, league_id: str, current_week: int, user_id: str) -> None:
    storage.save_league(conn, client.get_league(league_id))
    storage.save_league_users(conn, league_id, client.get_league_users(league_id))
    save_rosters_tracking_changes(conn, league_id, client.get_rosters(league_id), user_id)
    # Every week, not just the current one: past weeks pick up stat
    # corrections, and a recap needs last week's final scores.
    for week in range(1, current_week + 1):
        matchups = client.get_matchups(league_id, week)
        if matchups:
            storage.save_matchups(conn, league_id, week, matchups)
        txns = client.get_transactions(league_id, week)
        if txns:
            storage.save_transactions(conn, league_id, week, txns)


def _sync_roster_news(conn, league_ids: list[str], user_id: str, log: Log) -> None:
    """News for my own roster(s) only - one request per player, so the whole
    league's ~200 rostered players would make sync crawl."""
    player_ids: list[str] = []
    for league_id in league_ids:
        roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
        row = storage.get_roster(conn, league_id, roster_id) if roster_id is not None else None
        if row:
            player_ids.extend(pid for pid in json.loads(row["players"]) if pid not in player_ids)

    log(f"Fetching news for {len(player_ids)} rostered players...")
    fetched = 0
    for pid in player_ids:
        try:
            items = fetch_player_news(pid, limit=3)
        except Exception as e:
            # Undocumented endpoint - a failure here must never fail the sync.
            log(f"  news unavailable for {storage.player_name(conn, pid)}: {e}")
            continue
        if items:
            storage.save_player_news(conn, items)
            fetched += len(items)
    log(f"Cached {fetched} news items.")


def run_sync(
    conn,
    league_query: str | None = None,
    all_leagues: bool = False,
    include_news: bool = True,
    choose: Callable[[list[dict]], dict] = prompt_choose_league,
    log: Log = print,
) -> list[dict]:
    """Sync the selected league(s) and return them. The active league becomes
    the one selected; with `all_leagues`, the previously active league stays
    active if it's among them."""
    if not config.SLEEPER_USERNAME:
        raise SleeperError("Set SLEEPER_USERNAME in .env first (see .env.example).")

    with SleeperClient() as client:
        nfl_state = client.get_nfl_state()
        season = config.SLEEPER_SEASON or nfl_state["season"]
        current_week = int(nfl_state["week"])

        user = client.get_user(config.SLEEPER_USERNAME)
        leagues = client.get_user_leagues(user["user_id"], season)
        if not leagues:
            raise SleeperError(
                f"No leagues found for '{config.SLEEPER_USERNAME}' in season {season}. "
                "Check SLEEPER_USERNAME/SLEEPER_SEASON in .env."
            )
        # Every league the user is in, synced or not, so the agent can tell
        # them apart and offer to switch.
        storage.set_state(conn, "user_leagues", json.dumps(
            [{"league_id": lg["league_id"], "name": lg.get("name")} for lg in leagues]
        ))
        targets = _select_leagues(leagues, league_query, all_leagues, choose)

        players_age = _age_hours(storage.players_last_synced(conn))
        if players_age is None or players_age > config.PLAYERS_CACHE_TTL_HOURS:
            log("Fetching players catalog (injury designations live here)...")
            storage.save_players(conn, client.get_players())

        for league in targets:
            log(f"Syncing league: {league['name']} ({league['league_id']})")
            _sync_one_league(conn, client, league["league_id"], current_week, user["user_id"])

    target_ids = [lg["league_id"] for lg in targets]
    previous = storage.get_state(conn, "active_league_id")
    storage.set_state(conn, "active_league_id", previous if all_leagues and previous in target_ids else target_ids[0])
    storage.set_state(conn, "active_user_id", user["user_id"])
    storage.set_state(conn, "active_season", season)
    storage.set_state(conn, "current_week", str(current_week))

    if include_news:
        _sync_roster_news(conn, target_ids, user["user_id"], log)

    storage.set_state(conn, "last_synced_at", storage.now_iso())
    # Every roster was just pulled, so the next question needn't pull them again.
    storage.set_state(conn, "rosters_synced_at", storage.now_iso())
    return targets


def hours_since_sync(conn) -> float | None:
    return _age_hours(storage.get_state(conn, "last_synced_at"))


def sync_if_stale(conn, max_age_hours: float | None = None, log: Log = log_to_stderr) -> bool:
    """Re-sync the active league when the last sync is older than
    `max_age_hours` (default config.AUTO_SYNC_HOURS). Returns True if a sync
    ran. Never syncs a DB that was never synced (picking a league may need
    the user), and never raises: stale data beats no answer, so a failure is
    reported and the caller carries on with what's stored."""
    max_age = config.AUTO_SYNC_HOURS if max_age_hours is None else max_age_hours
    league_id = storage.get_state(conn, "active_league_id")
    if not league_id or not config.SLEEPER_USERNAME:
        return False
    age = hours_since_sync(conn)
    if age is not None and age <= max_age:
        return False

    log(f"Local data is {'of unknown age' if age is None else f'{age:.1f}h old'} - syncing from Sleeper...")
    try:
        run_sync(conn, league_query=league_id, log=log)
    except Exception as e:
        log(f"Auto-sync failed ({e}); answering from the last synced data.")
        return False
    return True


def refresh_rosters(conn, max_age_seconds: float | None = None, log: Log = log_to_stderr) -> bool:
    """Re-pull the active league's rosters plus this week's matchups and
    transactions (3 requests) when the stored rosters are older than
    `max_age_seconds` (default config.ROSTER_REFRESH_SECONDS). Returns True if
    it ran. Never touches `last_synced_at`, which marks a FULL sync only, and
    never raises: on failure the stored rosters are kept."""
    max_age = config.ROSTER_REFRESH_SECONDS if max_age_seconds is None else max_age_seconds
    # From state, not the agent context, so a mid-chat switch_league is followed.
    league_id = storage.get_state(conn, "active_league_id")
    week = storage.get_state(conn, "current_week")
    if not league_id or not week or not config.SLEEPER_USERNAME:
        return False
    age = _age_seconds(storage.get_state(conn, "rosters_synced_at"))
    if age is not None and age < max_age:
        return False

    try:
        with SleeperClient() as client:
            rosters = client.get_rosters(league_id)
            matchups = client.get_matchups(league_id, int(week))
            txns = client.get_transactions(league_id, int(week))
    except Exception as e:
        log(f"Roster refresh failed ({e}); answering from the last synced rosters.")
        return False
    save_rosters_tracking_changes(conn, league_id, rosters, storage.get_state(conn, "active_user_id"))
    if matchups:
        storage.save_matchups(conn, league_id, int(week), matchups)
    if txns:
        storage.save_transactions(conn, league_id, int(week), txns)
    storage.set_state(conn, "rosters_synced_at", storage.now_iso())
    return True


def refresh_before_question(conn, log: Log = log_to_stderr) -> bool:
    """What the agent runs before each question: a full sync if the data is
    stale, otherwise a roster refresh. Returns True if a full sync ran."""
    if sync_if_stale(conn, log=log):
        return True
    age = hours_since_sync(conn)
    if age is None or age > config.AUTO_SYNC_HOURS:
        # The full sync just failed (or the DB was never synced): Sleeper is
        # likely unreachable, so don't fail a second time.
        return False
    refresh_rosters(conn, log=log)
    return False
