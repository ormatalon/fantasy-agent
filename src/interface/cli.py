"""CLI entry point.

Usage (from repo root):
    uv run python -m src.interface.cli sync
    uv run python -m src.interface.cli roster [--team "name"]
    uv run python -m src.interface.cli transactions [--limit N]
    uv run python -m src.interface.cli projections [--week N] [--limit N]
    uv run python -m src.interface.cli backtest [--season YYYY] [--weeks START-END]
    uv run python -m src.interface.cli lineup [--week N]
    uv run python -m src.interface.cli waivers [--limit N]
    uv run python -m src.interface.cli trades --give "name,name" --receive "name,name"
    uv run python -m src.interface.cli draft [--draft-id ID] [--replay]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import config
from src.decisions.draft import find_league_draft_id, run_draft_assistant, run_draft_replay
from src.decisions.lineup import optimize_lineup
from src.decisions.trades import evaluate_trade
from src.decisions.waivers import suggest_waivers_by_position
from src.evaluation.backtest import run_backtest
from src.ingestion import storage
from src.ingestion.sleeper import SleeperClient, SleeperError, prompt_choose_league, resolve_league
from src.projections.engine import build_projection_table


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


def cmd_projections(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    _league_id, _user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    table = build_projection_table(conn, season, week)
    if not table:
        print("No projections available (try a different week, or run `sync` first).")
        return

    header = f"{'Player':<25}{'Pos':<5}{'Team':<6}{'Mean':>8}{'StdDev':>8}{'Matchup':>9}{'Injury':>8}{'Srcs':>6}"
    print(header)
    print("-" * len(header))
    for row in table[: args.limit]:
        std = row.variance**0.5
        print(
            f"{row.name:<25}{row.position or '':<5}{row.team or '':<6}{row.mean:>8.1f}"
            f"{std:>8.1f}{row.matchup_multiplier:>9.2f}{row.injury_multiplier:>8.2f}{row.num_sources:>6}"
        )


def cmd_backtest(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    if not conn.execute("SELECT 1 FROM leagues LIMIT 1").fetchone():
        print("No league synced yet - run `sync` first.", file=sys.stderr)
        sys.exit(1)

    start, end = (int(x) for x in args.weeks.split("-"))
    weeks = list(range(start, end + 1))

    report = run_backtest(conn, args.season, weeks)

    baseline = report["sleeper_only"]
    print(f"Backtest: season {args.season}, weeks {args.weeks}\n")
    print(f"{'Tier':<20}{'N':>6}{'MAE':>8}{'RMSE':>8}{'vs baseline MAE':>18}")
    for tier_name, label in [
        ("sleeper_only", "Sleeper only (baseline)"),
        ("blend", "+ blend"),
        ("blend_matchup", "+ blend + matchup"),
    ]:
        r = report[tier_name]
        delta = f"{(r.mae - baseline.mae) / baseline.mae:+.1%}" if baseline.mae and r.n else "n/a"
        print(f"{label:<20}{r.n:>6}{r.mae:>8.2f}{r.rmse:>8.2f}{delta:>18}")


def cmd_lineup(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
    if roster_id is None:
        print("Could not find your roster in this league.", file=sys.stderr)
        sys.exit(1)
    roster_row = storage.get_roster(conn, league_id, roster_id)
    roster_player_ids = json.loads(roster_row["players"])

    league_row = conn.execute("SELECT roster_positions FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    roster_positions = json.loads(league_row["roster_positions"])

    table = build_projection_table(conn, season, week)
    projections = {row.player_id: row for row in table}

    player_meta = {}
    for pid in roster_player_ids:
        p = conn.execute("SELECT first_name, last_name, position FROM players WHERE player_id = ?", (pid,)).fetchone()
        if p:
            name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) or pid
            player_meta[pid] = {"name": name, "position": p["position"]}

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    print(f"Week {week} lineup recommendation\n")
    total = 0.0
    for slot in lineup:
        print(f"{slot.slot:<12}{slot.name:<25}{slot.mean:>6.1f}  {slot.why}")
        total += slot.mean
    print(f"\nTotal projected: {total:.1f}")

    started = {s.player_id for s in lineup if s.player_id}
    bench = sorted(
        (pid for pid in roster_player_ids if pid not in started and pid in player_meta),
        key=lambda pid: projections[pid].mean if pid in projections else 0.0,
        reverse=True,
    )
    if bench:
        print("\nBench:")
        for pid in bench:
            proj = projections.get(pid)
            mean = proj.mean if proj else 0.0
            print(f"  {player_meta[pid]['name']:<25}{mean:>6.1f}")


def cmd_waivers(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, _user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    league_row = conn.execute("SELECT roster_positions FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    roster_positions = json.loads(league_row["roster_positions"])

    table = build_projection_table(conn, season, week)
    grouped = suggest_waivers_by_position(conn, league_id, roster_positions, table, limit_per_position=args.limit)

    print(f"Week {week} waiver targets by position\n")
    for group, suggestions in grouped.items():
        baseline = suggestions[0].proj.mean - suggestions[0].vor
        print(f"{group} (replacement level: {baseline:.1f} pts)")
        print(f"{'Player':<25}{'Pos':<5}{'Team':<6}{'Mean':>7}{'VOR':>7}{'FAAB %':>8}")
        for s in suggestions:
            p = s.proj
            print(f"{p.name:<25}{(p.position or ''):<5}{(p.team or ''):<6}{p.mean:>7.1f}{s.vor:>+7.1f}{s.bid_pct:>7}%")
        print()


def cmd_trades(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    _league_id, _user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = current_week

    def resolve_ids(names_arg: str) -> list[str]:
        ids = []
        for name in [n.strip() for n in names_arg.split(",") if n.strip()]:
            pid = storage.find_player_id_by_name(conn, name)
            if not pid:
                print(f"No player matching '{name}' found.", file=sys.stderr)
                sys.exit(1)
            ids.append(pid)
        return ids

    give_ids = resolve_ids(args.give)
    receive_ids = resolve_ids(args.receive)

    table = build_projection_table(conn, season, week)
    projections = {row.player_id: row for row in table}

    result = evaluate_trade(give_ids, receive_ids, projections)

    print(f"Week {week} trade evaluation\n")
    print("You give:")
    for p in result.give:
        print(f"  {p.name:<25}{p.mean:>6.1f}")
    print("You receive:")
    for p in result.receive:
        print(f"  {p.name:<25}{p.mean:>6.1f}")
    print(f"\nVerdict: {result.verdict.upper()}")
    print(result.why)


def cmd_draft(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, _current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")

    league_row = conn.execute("SELECT roster_positions FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    roster_positions = json.loads(league_row["roster_positions"])

    with SleeperClient() as client:
        draft_id = args.draft_id
        if not draft_id:
            draft_id = find_league_draft_id(client, league_id)
            if not draft_id:
                print("No draft found for this league. Pass --draft-id to point at a specific "
                      "(e.g. mock) draft instead.", file=sys.stderr)
                sys.exit(1)

        build_table = lambda: build_projection_table(conn, season, 1)  # noqa: E731
        if args.replay:
            run_draft_replay(client, build_table, draft_id, user_id, roster_positions)
        else:
            run_draft_assistant(client, build_table, draft_id, user_id, roster_positions)


def main() -> None:
    parser = argparse.ArgumentParser(prog="fantasy-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="Fetch latest league state from Sleeper into local storage")

    roster_p = sub.add_parser("roster", help="Print a roster from local storage")
    roster_p.add_argument("--team", help="Team/owner name to look up (defaults to your own roster)")

    txn_p = sub.add_parser("transactions", help="List recent league transactions from local storage")
    txn_p.add_argument("--limit", type=int, default=10)

    proj_p = sub.add_parser("projections", help="Print the adjusted projection table")
    proj_p.add_argument("--week", type=int, help="Defaults to the current week")
    proj_p.add_argument("--limit", type=int, default=30)

    backtest_p = sub.add_parser("backtest", help="Accuracy report vs. baseline over past weeks")
    backtest_p.add_argument("--season", default="2024", help="A completed season (default: 2024)")
    backtest_p.add_argument("--weeks", default="3-17", help="Week range, e.g. 3-17")

    lineup_p = sub.add_parser("lineup", help="Optimal start/sit recommendation for your roster")
    lineup_p.add_argument("--week", type=int, help="Defaults to the current week")

    waivers_p = sub.add_parser("waivers", help="Ranked waiver/FAAB targets, grouped by position")
    waivers_p.add_argument("--week", type=int, help="Defaults to the current week")
    waivers_p.add_argument("--limit", type=int, default=5, help="Max targets shown per position (default: 5)")

    trades_p = sub.add_parser("trades", help="Evaluate a proposed trade")
    trades_p.add_argument("--give", required=True, help="Comma-separated player names you'd give")
    trades_p.add_argument("--receive", required=True, help="Comma-separated player names you'd receive")

    draft_p = sub.add_parser("draft", help="Real-time draft assistant (polls until it's your turn)")
    draft_p.add_argument("--draft-id", dest="draft_id", help="Target a specific draft (e.g. a mock) instead of your league's")
    draft_p.add_argument("--replay", action="store_true", help="Replay a completed draft instead of polling live")

    args = parser.parse_args()

    try:
        if args.command == "sync":
            cmd_sync(args)
        elif args.command == "roster":
            cmd_roster(args)
        elif args.command == "transactions":
            cmd_transactions(args)
        elif args.command == "projections":
            cmd_projections(args)
        elif args.command == "backtest":
            cmd_backtest(args)
        elif args.command == "lineup":
            cmd_lineup(args)
        elif args.command == "waivers":
            cmd_waivers(args)
        elif args.command == "trades":
            cmd_trades(args)
        elif args.command == "draft":
            cmd_draft(args)
    except SleeperError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
