"""CLI entry point.

Usage (from repo root):
    uv run python -m src.interface.cli sync
    uv run python -m src.interface.cli roster [--team "name"]
    uv run python -m src.interface.cli transactions [--limit N]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import config
from src.ingestion import storage
from src.ingestion.sleeper import SleeperClient, SleeperError, prompt_choose_league, resolve_league


def cmd_sync(_args: argparse.Namespace) -> None:
    if not config.SLEEPER_USERNAME:
        print("Set SLEEPER_USERNAME in .env first (see .env.example).", file=sys.stderr)
        sys.exit(1)

    conn = storage.get_connection(config.DB_PATH)

    with SleeperClient() as client:
        nfl_state = client.get_nfl_state()
        season = config.SLEEPER_SEASON or nfl_state["season"]
        current_week = int(nfl_state["week"])

        user, league = resolve_league(client, config.SLEEPER_USERNAME, season, choose=prompt_choose_league)
        league_id = league["league_id"]
        print(f"Using league: {league['name']} ({league_id})")

        league_detail = client.get_league(league_id)
        storage.save_league(conn, league_detail)

        league_users = client.get_league_users(league_id)
        storage.save_league_users(conn, league_id, league_users)

        rosters = client.get_rosters(league_id)
        storage.save_rosters(conn, league_id, rosters)

        last_synced = storage.players_last_synced(conn)
        stale = True
        if last_synced:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last_synced)
            stale = age > timedelta(hours=config.PLAYERS_CACHE_TTL_HOURS)
        if stale:
            print("Fetching players catalog (may take a few seconds)...")
            players = client.get_players()
            storage.save_players(conn, players)
        else:
            print("Players catalog is fresh, skipping re-fetch.")

        for week in range(1, current_week + 1):
            matchups = client.get_matchups(league_id, week)
            if matchups:
                storage.save_matchups(conn, league_id, week, matchups)
            txns = client.get_transactions(league_id, week)
            if txns:
                storage.save_transactions(conn, league_id, week, txns)

        storage.set_state(conn, "active_league_id", league_id)
        storage.set_state(conn, "active_user_id", user["user_id"])
        storage.set_state(conn, "active_season", season)
        storage.set_state(conn, "current_week", str(current_week))

    print(f"Synced. Season {season}, week {current_week}.")


def _require_synced_state(conn) -> tuple[str, str, int]:
    league_id = storage.get_state(conn, "active_league_id")
    user_id = storage.get_state(conn, "active_user_id")
    week = storage.get_state(conn, "current_week")
    if not league_id or not user_id or not week:
        print("No local data yet - run `sync` first.", file=sys.stderr)
        sys.exit(1)
    return league_id, user_id, int(week)


def _print_roster(conn, league_id: str, roster_row, week: int) -> None:
    owner_label = storage.team_label(conn, league_id, roster_row["owner_id"])
    print(f"\n{owner_label}")
    print("-" * len(owner_label))

    starters = json.loads(roster_row["starters"])
    all_players = json.loads(roster_row["players"])
    bench = [p for p in all_players if p not in starters]

    print("Starters:")
    for pid in starters:
        print(f"  - {storage.player_name(conn, pid)}")
    if bench:
        print("Bench:")
        for pid in bench:
            print(f"  - {storage.player_name(conn, pid)}")

    opponent = storage.get_opponent_roster(conn, league_id, week, roster_row["roster_id"])
    if opponent:
        opp_roster = storage.get_roster(conn, league_id, opponent["roster_id"])
        opp_label = storage.team_label(conn, league_id, opp_roster["owner_id"]) if opp_roster else f"roster {opponent['roster_id']}"
        print(f"\nWeek {week} opponent: {opp_label}")
    else:
        print(f"\nWeek {week} opponent: none found (bye or not yet scheduled)")


def cmd_roster(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, week = _require_synced_state(conn)

    if args.team:
        roster_row = storage.find_roster_by_team_query(conn, league_id, args.team)
        if not roster_row:
            print(f"No team matching '{args.team}' found.", file=sys.stderr)
            sys.exit(1)
    else:
        roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
        if roster_id is None:
            print("Could not find your roster in this league.", file=sys.stderr)
            sys.exit(1)
        roster_row = storage.get_roster(conn, league_id, roster_id)

    _print_roster(conn, league_id, roster_row, week)


def cmd_transactions(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, _user_id, _week = _require_synced_state(conn)

    txns = storage.get_recent_transactions(conn, league_id, limit=args.limit)
    if not txns:
        print("No transactions found.")
        return

    for t in txns:
        when = datetime.fromtimestamp(t["created"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if t["created"] else "?"
        adds = json.loads(t["adds"]) or {}
        drops = json.loads(t["drops"]) or {}
        print(f"\n[{when}] {t['type']} ({t['status']})")
        for pid in adds:
            print(f"  + {storage.player_name(conn, pid)}")
        for pid in drops:
            print(f"  - {storage.player_name(conn, pid)}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="fantasy-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="Fetch latest league state from Sleeper into local storage")

    roster_p = sub.add_parser("roster", help="Print a roster from local storage")
    roster_p.add_argument("--team", help="Team/owner name to look up (defaults to your own roster)")

    txn_p = sub.add_parser("transactions", help="List recent league transactions from local storage")
    txn_p.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    try:
        if args.command == "sync":
            cmd_sync(args)
        elif args.command == "roster":
            cmd_roster(args)
        elif args.command == "transactions":
            cmd_transactions(args)
    except SleeperError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
