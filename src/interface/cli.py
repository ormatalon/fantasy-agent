"""CLI entry point.

Usage (from repo root):
    uv run python -m src.interface.cli sync [--league NAME] [--all]
    uv run python -m src.interface.cli leagues
    uv run python -m src.interface.cli use "league name"
    uv run python -m src.interface.cli roster [--fantasy-team "name"]
    uv run python -m src.interface.cli transactions [--limit N]
    uv run python -m src.interface.cli projections [--week N] [--limit N]
    uv run python -m src.interface.cli backtest [--season YYYY] [--weeks START-END]
    uv run python -m src.interface.cli lineup [--week N]
    uv run python -m src.interface.cli waivers [--limit N]
    uv run python -m src.interface.cli trades --give "name,name" --receive "name,name"
    uv run python -m src.interface.cli draft [--draft-id ID] [--replay]
    uv run python -m src.interface.cli ask "should I start X or Y?"
    uv run python -m src.interface.cli chat
"""

import argparse
import json
import sys
from datetime import datetime, timezone

import config
from src.agent.graph import draw_mermaid
from src.agent.orchestrator import AgentError, ask, chat
from src.decisions.draft import find_league_draft_id, run_draft_assistant, run_draft_replay
from src.decisions.lineup import NON_STARTING_SLOTS, optimize_lineup
from src.decisions.results import last_completed_week, summarize_week
from src.decisions.trades import evaluate_trade
from src.decisions.waivers import suggest_waivers_by_position, unavailable_this_week
from src.evaluation.backtest import run_backtest
from src.execution.approval import Approval, ApprovalDenied, ProposedWrite, require_approval
from src.execution.sleeper_writes import (
    EMPTY_SLOT, add_drop, cancel_claim, describe_claim, pending_claims, set_lineup, set_reserve, submit_claim,
)
from src.ingestion import storage
from src.ingestion.news import fetch_player_news
from src.ingestion.schedule import PLAYED, game_status, load_game_statuses
from src.ingestion.sleeper import SleeperClient, SleeperError
from src.ingestion.sync import run_sync
from src.interface.digest import build_digest
from src.interface.notify import NotifyError, notify
from src.projections.engine import build_projection_table, build_season_projection_table, weeks_remaining


def cmd_sync(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    run_sync(conn, league_query=args.league, all_leagues=args.all_leagues, include_news=not args.skip_news)
    league_id = storage.get_state(conn, "active_league_id")
    print(f"Synced. Active league: {_league_name(conn, league_id)}. "
          f"Season {storage.get_state(conn, 'active_season')}, week {storage.get_state(conn, 'current_week')}.")


def cmd_leagues(_args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    known = json.loads(storage.get_state(conn, "user_leagues") or "[]")
    if not known:
        print("No leagues known yet - run `sync` first.")
        return
    active = storage.get_state(conn, "active_league_id")
    synced = {lg["league_id"] for lg in storage.list_leagues(conn)}
    for lg in known:
        marks = [m for m, on in (("active", lg["league_id"] == active), ("synced", lg["league_id"] in synced)) if on]
        print(f"{lg['name']:<40}{lg['league_id']:<22}{', '.join(marks)}")
    print('\nSwitch with: use "<league name>"')


def cmd_use(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    run_sync(conn, league_query=args.league, include_news=not args.skip_news)
    print(f"Now using: {_league_name(conn, storage.get_state(conn, 'active_league_id'))}")


def _date(ms: int | None) -> str:
    """A Sleeper timestamp (milliseconds) as YYYY-MM-DD."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "?"


def _league_name(conn, league_id: str | None) -> str:
    row = conn.execute("SELECT name FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    return f"{row['name']} ({league_id})" if row else str(league_id)


def _resolve_player_or_exit(conn, name: str, league_id: str) -> str:
    pid, candidates = storage.resolve_player(conn, name, league_id)
    if pid:
        return pid
    if not candidates:
        print(f"No player matching '{name}' found.", file=sys.stderr)
    else:
        print(f"'{name}' is ambiguous. Did you mean one of these? (pass it exactly as shown)", file=sys.stderr)
        for c in candidates:
            print(f"  {storage.player_name(conn, c)}", file=sys.stderr)
    sys.exit(1)


def _require_synced_state(conn) -> tuple[str, str, int]:
    league_id = storage.get_state(conn, "active_league_id")
    user_id = storage.get_state(conn, "active_user_id")
    week = storage.get_state(conn, "current_week")
    if not league_id or not user_id or not week:
        print("No local data yet - run `sync` first.", file=sys.stderr)
        sys.exit(1)
    return league_id, user_id, int(week)


def _my_roster_id(conn, league_id: str, user_id: str) -> int:
    roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
    if roster_id is None:
        print("Could not find your roster in this league.", file=sys.stderr)
        sys.exit(1)
    return roster_id


def _print_roster(conn, league_id: str, roster_row, week: int) -> None:
    owner_label = storage.fantasy_team_label(conn, league_id, roster_row["owner_id"])
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
        opp_label = storage.fantasy_team_label(conn, league_id, opp_roster["owner_id"]) if opp_roster else f"roster {opponent['roster_id']}"
        print(f"\nWeek {week} opponent: {opp_label}")
    else:
        print(f"\nWeek {week} opponent: none found (bye or not yet scheduled)")


def cmd_roster(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, week = _require_synced_state(conn)

    if args.fantasy_team:
        roster_row = storage.find_roster_by_fantasy_team(conn, league_id, args.fantasy_team)
        if not roster_row:
            print(f"No fantasy team matching '{args.fantasy_team}' found.", file=sys.stderr)
            sys.exit(1)
    else:
        roster_row = storage.get_roster(conn, league_id, _my_roster_id(conn, league_id, user_id))

    _print_roster(conn, league_id, roster_row, week)


def cmd_transactions(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, _user_id, _week = _require_synced_state(conn)

    txns = storage.get_recent_transactions(conn, league_id, limit=args.limit)
    if not txns:
        print("No transactions found.")
        return

    for t in txns:
        when = _date(t["created"])
        adds = json.loads(t["adds"]) or {}
        drops = json.loads(t["drops"]) or {}
        print(f"\n[{when}] {t['type']} ({t['status']})")
        for pid in adds:
            print(f"  + {storage.player_name(conn, pid)}")
        for pid in drops:
            print(f"  - {storage.player_name(conn, pid)}")


def cmd_projections(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, _user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    if args.season_long:
        table = build_season_projection_table(conn, season, league_id, from_week=current_week)
        print(f"Season {season} rest-of-season projections (weeks {current_week}+)\n")
    else:
        table = build_projection_table(conn, season, week, league_id)
    if not table:
        print("No projections available (try a different week, or run `sync` first).")
        return

    header = f"{'Player':<25}{'Pos':<5}{'Team':<6}{'Mean':>8}{'StdDev':>8}{'Matchup':>9}{'Injury':>8}{'Srcs':>6}  Status"
    print(header)
    print("-" * len(header))
    for row in table[: args.limit]:
        std = row.variance**0.5
        print(
            f"{row.name:<25}{row.position or '':<5}{row.team or '':<6}{row.mean:>8.1f}"
            f"{std:>8.1f}{row.matchup_multiplier:>9.2f}{row.injury_multiplier:>8.2f}{row.num_sources:>6}"
            f"  {row.injury_status or ''}"
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


def _live_roster(league_id: str, roster_id: int) -> dict:
    """My roster as Sleeper has it this second (not the local copy)."""
    with SleeperClient() as client:
        return next(r for r in client.get_rosters(league_id) if r["roster_id"] == roster_id)


def _team(conn, pid: str) -> str | None:
    row = conn.execute("SELECT team FROM players WHERE player_id = ?", (pid,)).fetchone()
    return row["team"] if row else None


def _approve(write: ProposedWrite) -> Approval | None:
    try:
        return require_approval(write)
    except ApprovalDenied as e:
        print(f"\n{e}")
        return None


def _check(label: str, sent, got) -> None:
    """Report a write's read-back. A failed read-back is not a failed write."""
    if isinstance(got, Exception):
        print(f"{label}: FAILED - Sleeper refused it ({got})")
    elif got is None:
        print(f"{label}: SENT, could not verify (the read-back failed, not necessarily the write). Check the app.")
    elif sent == got:
        print(f"{label}: VERIFIED")
    else:
        print(f"{label}: MISMATCH - sent {sent}, Sleeper shows {got}")


def _live_lineup(conn, league_id, roster_id, season, week, current_week, roster_positions, projections, apply: bool) -> None:
    """Compare the recommendation with the lineup set in Sleeper and, with
    `apply`, set it (after approval). Players whose game has started are
    locked: they stay where they are, as Sleeper requires."""
    live = _live_roster(league_id, roster_id)
    reserve = set(live.get("reserve") or [])
    pool = [pid for pid in live.get("players") or [] if pid not in reserve]  # IR-slotted players can't start
    meta = storage.player_meta(conn, pool)
    slots = [s for s in roster_positions if s not in NON_STARTING_SLOTS]
    current = [pid or EMPTY_SLOT for pid in (live.get("starters") or [])][: len(slots)]
    current += [EMPTY_SLOT] * (len(slots) - len(current))

    statuses = load_game_statuses(season, week)
    locked = {pid for pid in pool if game_status(_team(conn, pid), statuses) == PLAYED}
    best = optimize_lineup(roster_positions, pool, projections, meta, current_starters=current)
    target = optimize_lineup(roster_positions, pool, projections, meta, current_starters=current, locked=locked)
    starters = [s.player_id or EMPTY_SLOT for s in target]

    def name(pid: str) -> str:
        return "(empty)" if pid == EMPTY_SLOT else meta.get(pid, {}).get("name", pid)

    print(f"Week {week}: set in Sleeper vs recommended\n")
    print(f"{'Slot':<12}{'Currently':<25}{'Recommended':<25}")
    changes = []
    for i, slot in enumerate(slots):
        note = ""
        if starters[i] != current[i]:
            changes.append(f"{slot}: {name(current[i])} -> {name(starters[i])}")
            note = "  <-- change"
        elif current[i] in locked:
            note = "  (locked: game started)"
        print(f"{slot:<12}{name(current[i]):<25}{name(starters[i]):<25}{note}")

    # Not the same as matching: the difference is real, kickoff just put it
    # out of reach.
    lost = sum(s.mean for s in best) - sum(s.mean for s in target)
    if lost > 0.05:
        print(f"\nGames already started cost {lost:.1f} projected pts: the best lineup is out of reach.")
    if not changes:
        print("\nNothing else to change." if lost > 0.05 else "\nLineup already matches the recommendation.")
        return
    print(f"\n{len(changes)} change(s):")
    for line in changes:
        print(f"  - {line}")
    if not apply:
        print("\nRe-run with --apply to enact these (you'll be asked to confirm).")
        return

    write = ProposedWrite(
        action="set_lineup",
        summary=f"Set your week {week} Sleeper lineup ({len(changes)} change(s)).",
        changes=changes,
        payload={"league_id": league_id, "roster_id": roster_id, "week": week, "starters": starters},
    )
    approval = _approve(write)
    if not approval:
        return
    result = set_lineup(league_id, roster_id, week, starters, write, approval)
    print()
    _check("Week lineup (matchup)", starters, result["matchup"])
    _check("Roster lineup", starters, result["roster"])


def cmd_lineup(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    roster_id = _my_roster_id(conn, league_id, user_id)
    roster_row = storage.get_roster(conn, league_id, roster_id)
    roster_player_ids = json.loads(roster_row["players"])

    roster_positions = storage.roster_positions(conn, league_id)

    table = build_projection_table(conn, season, week, league_id)
    projections = {row.player_id: row for row in table}

    player_meta = storage.player_meta(conn, roster_player_ids)
    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta,
                             current_starters=json.loads(roster_row["starters"]))

    if args.live or args.apply:
        if week != current_week:
            # Sleeper only reports (and the roster write only sets) the
            # lineup in effect now, so another week can't be compared or set.
            print(f"--live/--apply work on the current week ({current_week}) only.", file=sys.stderr)
            sys.exit(1)
        _live_lineup(conn, league_id, roster_id, season, week, current_week, roster_positions, projections, apply=args.apply)
        return

    print(f"Week {week} lineup recommendation\n")
    total = 0.0
    for slot in lineup:
        status = storage.get_injury_status(conn, slot.player_id)
        print(f"{slot.slot:<12}{slot.name:<25}{slot.mean:>6.1f}  {slot.why}{f' [{status}]' if status else ''}")
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
            status = storage.get_injury_status(conn, pid)
            ir_note = "  <-- IR designation: can move to your IR slot" if status == "IR" else ""
            print(f"  {player_meta[pid]['name']:<25}{mean:>6.1f}  {status or ''}{ir_note}")


def cmd_waivers(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, _user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    roster_positions = storage.roster_positions(conn, league_id)

    excluded: dict[str, str] = {}
    if args.season_long:
        table = build_season_projection_table(conn, season, league_id, from_week=current_week)
        print(f"Rest-of-season waiver targets (weeks {current_week}+)\n")
    else:
        table = build_projection_table(conn, season, week, league_id)
        if week == current_week:
            excluded = unavailable_this_week(table, load_game_statuses(season, week))
        print(f"Week {week} waiver targets by position")
        if excluded:
            print(f"(excluding {len(excluded)} available players whose game already kicked off or who are on bye)")
        print()
    grouped = suggest_waivers_by_position(
        conn, league_id, roster_positions, table, limit_per_position=args.limit, exclude_ids=set(excluded),
        weeks=weeks_remaining(current_week) if args.season_long else 1,
    )

    for group, suggestions in grouped.items():
        baseline = suggestions[0].proj.mean - suggestions[0].vor
        print(f"{group} (replacement level: {baseline:.1f} pts)")
        print(f"{'Player':<25}{'Pos':<5}{'Team':<6}{'Mean':>7}{'VOR':>7}{'FAAB %':>8}  Status")
        for s in suggestions:
            p = s.proj
            print(f"{p.name:<25}{(p.position or ''):<5}{(p.team or ''):<6}{p.mean:>7.1f}{s.vor:>+7.1f}"
                  f"{s.bid_pct:>7}%  {p.injury_status or ''}")
        print()


def cmd_trades(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, _user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = current_week

    def resolve_ids(names_arg: str) -> list[str]:
        return [_resolve_player_or_exit(conn, n.strip(), league_id) for n in names_arg.split(",") if n.strip()]

    give_ids = resolve_ids(args.give)
    receive_ids = resolve_ids(args.receive)

    if args.season_long:
        table = build_season_projection_table(conn, season, league_id, from_week=current_week)
        label = f"Season {season} rest-of-season (weeks {current_week}+)"
    else:
        table = build_projection_table(conn, season, week, league_id)
        label = f"Week {week}"
    projections = {row.player_id: row for row in table}

    result = evaluate_trade(give_ids, receive_ids, projections)

    print(f"{label} trade evaluation\n")
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

    roster_positions = storage.roster_positions(conn, league_id)

    with SleeperClient() as client:
        draft_id = args.draft_id
        if not draft_id:
            draft_id = find_league_draft_id(client, league_id)
            if not draft_id:
                print("No draft found for this league. Pass --draft-id to point at a specific "
                      "(e.g. mock) draft instead.", file=sys.stderr)
                sys.exit(1)

        build_table = lambda: build_projection_table(conn, season, 1, league_id)  # noqa: E731
        if args.replay:
            run_draft_replay(client, build_table, draft_id, user_id, roster_positions)
        else:
            run_draft_assistant(client, build_table, draft_id, user_id, roster_positions)


def cmd_news(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, _week = _require_synced_state(conn)

    if args.player:
        pid = _resolve_player_or_exit(conn, args.player, league_id)
        player_ids = [pid]
        # Ad-hoc lookups hit the API live so a player outside my roster
        # (a waiver target, say) still works without a full re-sync.
        try:
            items = fetch_player_news(pid, limit=args.limit)
            if items:
                storage.save_player_news(conn, items)
        except Exception as e:
            print(f"Live news fetch failed ({e}); showing cached.", file=sys.stderr)
    else:
        row = storage.get_roster(conn, league_id, _my_roster_id(conn, league_id, user_id))
        player_ids = json.loads(row["players"])

    printed = 0
    for pid in player_ids:
        items = storage.get_player_news(conn, pid, limit=args.limit)
        if not items:
            continue
        status = storage.get_injury_status(conn, pid)
        print(f"\n{storage.player_name(conn, pid)}{f' [{status}]' if status else ''}")
        for item in items:
            print(f"  [{_date(item['published'])}] {item['title']}")
            if item["analysis"]:
                print(f"    {item['analysis']}")
        printed += 1

    if not printed:
        print("No cached news. Run `sync` (or `news --player \"name\"`) to fetch some.")


def cmd_trending(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    kind = "drop" if args.drop else "add"
    with SleeperClient() as client:
        trending = client.get_trending(kind, lookback_hours=args.hours, limit=args.limit)
    if not trending:
        print("No trending data returned.")
        return

    rostered = set()
    league_id = storage.get_state(conn, "active_league_id")
    if league_id:
        for row in conn.execute("SELECT players FROM rosters WHERE league_id = ?", (league_id,)):
            rostered.update(json.loads(row["players"]))

    print(f"Most {kind}ed players across Sleeper (last {args.hours}h)\n")
    print(f"{'Player':<28}{'Leagues':>10}   Status")
    for entry in trending:
        pid = entry["player_id"]
        tag = "rostered in your league" if pid in rostered else "available"
        print(f"{storage.player_name(conn, pid):<28}{entry['count']:>10,}   {tag}")


def cmd_results(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, current_week = _require_synced_state(conn)
    week = args.week or last_completed_week(current_week)

    roster_id = _my_roster_id(conn, league_id, user_id)
    roster_positions = storage.roster_positions(conn, league_id)

    result = summarize_week(conn, league_id, roster_id, week, roster_positions)
    if not result:
        print(f"No scored results stored for week {week}. Run `sync` (results appear once games are played).")
        return

    print(f"Week {week} results{' (in progress)' if week >= current_week else ''}\n")
    if result.opponent_name:
        print(f"{result.outcome.upper()}: {result.actual_total:.1f} - {result.opponent_total:.1f} vs. {result.opponent_name}")
        top = ", ".join(f"{name} {pts:.1f}" for name, pts in result.opponent_top)
        print(f"Their top scorers: {top or 'n/a'}\n")
    else:
        print("No opponent this week (bye).\n")
    print("Started:")
    for name, pts in result.started:
        print(f"  {name:<25}{pts:>7.1f}")
    print(f"\nActual total:  {result.actual_total:>7.1f}")
    print(f"Best possible: {result.optimal_total:>7.1f}")
    print(f"Left on bench: {result.points_left_on_bench:>7.1f}")

    if result.points_left_on_bench > 0:
        print("\nBest possible lineup was:")
        for slot in result.optimal_lineup:
            print(f"  {slot.slot:<12}{slot.name:<25}{slot.mean:>7.1f}")

    if result.bench:
        print("\nBench:")
        for name, pts in result.bench:
            print(f"  {name:<25}{pts:>7.1f}")


def _my_live_roster(conn) -> tuple[str, int, int, dict]:
    """(league_id, roster_id, current_week, my live roster)."""
    league_id, user_id, current_week = _require_synced_state(conn)
    roster_id = _my_roster_id(conn, league_id, user_id)
    return league_id, roster_id, current_week, _live_roster(league_id, roster_id)


def _resolve_in_or_exit(conn, name: str, league_id: str, pool: set[str], where: str) -> str:
    """A player id for `name`, which must be in `pool` (my roster, or the
    free agents). Resolving against a known pool, not the whole catalog,
    is what keeps a common name from landing on the wrong player."""
    pid, candidates = storage.resolve_player(conn, name, league_id)
    in_pool = [c for c in ([pid] if pid else candidates) if c in pool]
    if len(in_pool) == 1:
        return in_pool[0]
    if not in_pool:
        print(f"No player matching '{name}' {where}.", file=sys.stderr)
    else:
        print(f"'{name}' is ambiguous {where}. Pass one exactly as shown:", file=sys.stderr)
        for c in in_pool:
            print(f"  {storage.player_name(conn, c)}", file=sys.stderr)
    sys.exit(1)


def _free_agents(conn, league_id: str) -> set[str]:
    """Players on an NFL team that no roster in the league holds right now."""
    with SleeperClient() as client:
        owned = {pid for r in client.get_rosters(league_id) for pid in r.get("players") or []}
    on_teams = {r["player_id"] for r in conn.execute("SELECT player_id FROM players WHERE team IS NOT NULL")}
    return on_teams - owned


def _drop_warning(conn, live: dict, drop: str | None) -> str | None:
    if drop and drop in (live.get("starters") or []):
        return f"{storage.player_name(conn, drop)} is a starter: dropping him leaves that slot empty."
    return None


def cmd_ir(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, roster_id, _week, live = _my_live_roster(conn)
    now = live.get("reserve") or []

    def show(ids) -> str:
        return ", ".join(storage.player_name(conn, pid) for pid in ids) or "(empty)"

    if args.set is None:
        print(f"IR now: {show(now)}")
        return
    mine = set(live.get("players") or [])
    names = [n.strip() for n in args.set.split(",") if n.strip()]
    reserve = [_resolve_in_or_exit(conn, n, league_id, mine, "on your roster") for n in names]
    if sorted(reserve) == sorted(now):
        print(f"IR already holds exactly: {show(now)}")
        return

    write = ProposedWrite(
        action="set_ir",
        summary="Set which of your players occupy IR slots.",
        changes=[f"IR now:   {show(now)}", f"IR after: {show(reserve)}"]
        + [f"{storage.player_name(conn, pid)}: designation {storage.get_injury_status(conn, pid) or 'none'}"
           for pid in reserve if pid not in now],
        payload={"league_id": league_id, "roster_id": roster_id, "reserve": reserve},
    )
    approval = _approve(write)
    if not approval:
        return
    got = set_reserve(league_id, roster_id, reserve, write, approval)
    print()
    _check("IR", sorted(reserve), None if got is None else sorted(got))


def cmd_add(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, roster_id, _week, live = _my_live_roster(conn)
    if not args.player and not args.drop:
        print("Name a player to add, a player to --drop, or both.", file=sys.stderr)
        sys.exit(1)
    add = _resolve_in_or_exit(conn, args.player, league_id, _free_agents(conn, league_id), "among free agents") if args.player else None
    drop = _resolve_in_or_exit(conn, args.drop, league_id, set(live.get("players") or []), "on your roster") if args.drop else None

    changes = [f"ADD  {storage.player_name(conn, add)}"] if add else []
    changes += [f"DROP {storage.player_name(conn, drop)}"] if drop else []
    warning = _drop_warning(conn, live, drop)
    write = ProposedWrite(
        action="add_drop",
        summary="Free-agent move, effective immediately." + (f"\nWARNING: {warning}" if warning else ""),
        changes=changes,
        payload={"league_id": league_id, "roster_id": roster_id, "add": add, "drop": drop},
    )
    approval = _approve(write)
    if not approval:
        return
    players = add_drop(league_id, roster_id, add, drop, write, approval)
    print()
    if players is None:
        _check("Roster", None, None)
    else:
        landed = (not add or add in players) and (not drop or drop not in players)
        print("Roster: VERIFIED" if landed else
              "Roster: MISMATCH - the move isn't on your roster. If the player is on waivers, use `claim` instead.")


def cmd_claim(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, roster_id, current_week, live = _my_live_roster(conn)

    if args.cancel:
        claim = next((t for t in pending_claims(league_id, roster_id) if t["transaction_id"] == args.cancel), None)
        if not claim:
            print(f"No pending claim {args.cancel} - see `claims`.", file=sys.stderr)
            sys.exit(1)
        write = ProposedWrite(
            action="cancel_claim",
            summary="Withdraw a pending waiver claim.",
            changes=[describe_claim(conn, claim)],
            payload={"league_id": league_id, "transaction_id": args.cancel, "leg": claim["leg"]},
        )
        approval = _approve(write)
        if approval:
            cancel_claim(league_id, args.cancel, claim["leg"], write, approval)
            still = any(t["transaction_id"] == args.cancel for t in pending_claims(league_id, roster_id))
            print("\nClaim: MISMATCH - still pending" if still else "\nClaim: VERIFIED - cancelled")
        return

    if not args.player:
        print("Name the player to claim (or --cancel <id>).", file=sys.stderr)
        sys.exit(1)
    with SleeperClient() as client:
        settings = client.get_league(league_id).get("settings") or {}
    faab = settings.get("waiver_type") == 2
    bid = args.bid
    if bid is not None and not faab:
        print("This league doesn't use FAAB, so a claim can't carry a bid.", file=sys.stderr)
        sys.exit(1)
    if faab:
        bid = bid or 0
        remaining = (settings.get("waiver_budget") or 0) - ((live.get("settings") or {}).get("waiver_budget_used") or 0)
        if bid > remaining:
            print(f"Bid ${bid} is more than your remaining FAAB (${remaining}).", file=sys.stderr)
            sys.exit(1)

    add = _resolve_in_or_exit(conn, args.player, league_id, _free_agents(conn, league_id), "among unrostered players")
    drop = _resolve_in_or_exit(conn, args.drop, league_id, set(live.get("players") or []), "on your roster") if args.drop else None
    changes = [f"CLAIM {storage.player_name(conn, add)}"]
    changes += [f"DROP  {storage.player_name(conn, drop)}"] if drop else []
    changes += [f"BID   ${bid}"] if faab else []
    warning = _drop_warning(conn, live, drop)
    write = ProposedWrite(
        action="waiver_claim",
        summary="Waiver claim, processed at the league's next waiver run." + (f"\nWARNING: {warning}" if warning else ""),
        changes=changes,
        payload={"league_id": league_id, "roster_id": roster_id, "add": add, "drop": drop, "bid": bid if faab else None},
    )
    approval = _approve(write)
    if not approval:
        return
    txn = submit_claim(league_id, roster_id, add, drop, bid if faab else None, write, approval)
    queued = any(t["transaction_id"] == txn.get("transaction_id") for t in pending_claims(league_id, roster_id))
    print(f"\nClaim {txn.get('transaction_id')}: " + ("VERIFIED - pending" if queued else "MISMATCH - not among pending claims"))


def cmd_claims(_args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, roster_id, _week, _live = _my_live_roster(conn)
    claims = pending_claims(league_id, roster_id)
    if not claims:
        print("No pending waiver claims.")
        return
    for txn in claims:
        print(describe_claim(conn, txn))
    print('\nCancel one with: claim --cancel <id>')


def cmd_digest(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    league_id, user_id, current_week = _require_synced_state(conn)
    season = storage.get_state(conn, "active_season")
    week = args.week or current_week

    body = build_digest(conn, league_id, user_id, season, week)
    print(body)

    if args.email:
        notify(f"Fantasy digest - week {week}", body, to=args.to)


def cmd_ask(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    print(ask(conn, args.question))


def cmd_chat(args: argparse.Namespace) -> None:
    conn = storage.get_connection(config.DB_PATH)
    chat(conn, new=args.new)


def cmd_graph(_args: argparse.Namespace) -> None:
    print(draw_mermaid())


def main() -> None:
    # The agent prints model-generated text, which can contain any Unicode.
    # Windows consoles default to cp1252 and raise on anything outside it, so
    # force UTF-8 and degrade gracefully rather than crashing on a stray dash.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="fantasy-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_p = sub.add_parser("sync", help="Fetch latest league state from Sleeper into local storage")
    sync_p.add_argument("--skip-news", action="store_true", help="Skip the per-player news fetch (faster)")
    sync_p.add_argument("--league", help="League name or id to sync and make active (no prompt)")
    sync_p.add_argument("--all", dest="all_leagues", action="store_true", help="Sync every league you're in")

    sub.add_parser("leagues", help="List your leagues this season and which one is active")

    use_p = sub.add_parser("use", help="Switch the active league (syncs it first)")
    use_p.add_argument("league", help="League name (loose match) or id")
    use_p.add_argument("--skip-news", action="store_true", help="Skip the per-player news fetch (faster)")

    sub.add_parser("graph", help="Print the agent's LangGraph as Mermaid")

    roster_p = sub.add_parser("roster", help="Print a roster from local storage")
    roster_p.add_argument("--fantasy-team", help="Fantasy team or manager name in this league (defaults to yours). Not an NFL team.")

    txn_p = sub.add_parser("transactions", help="List recent league transactions from local storage")
    txn_p.add_argument("--limit", type=int, default=10)

    proj_p = sub.add_parser("projections", help="Print the adjusted projection table")
    proj_p.add_argument("--week", type=int, help="Defaults to the current week")
    proj_p.add_argument("--limit", type=int, default=30)
    proj_p.add_argument("--season", dest="season_long", action="store_true",
                        help="Full-season projected totals instead of a single week")

    backtest_p = sub.add_parser("backtest", help="Accuracy report vs. baseline over past weeks")
    backtest_p.add_argument("--season", default="2024", help="A completed season (default: 2024)")
    backtest_p.add_argument("--weeks", default="3-17", help="Week range, e.g. 3-17")

    lineup_p = sub.add_parser("lineup", help="Optimal start/sit recommendation for your roster")
    lineup_p.add_argument("--week", type=int, help="Defaults to the current week")
    lineup_p.add_argument("--live", action="store_true",
                          help="Compare against what's actually set on Sleeper right now")
    lineup_p.add_argument("--apply", action="store_true",
                          help="Enact the changes on Sleeper (needs SLEEPER_TOKEN; asks you to confirm first)")

    waivers_p = sub.add_parser("waivers", help="Ranked waiver/FAAB targets, grouped by position")
    waivers_p.add_argument("--week", type=int, help="Defaults to the current week")
    waivers_p.add_argument("--limit", type=int, default=5, help="Max targets shown per position (default: 5)")
    waivers_p.add_argument("--season", dest="season_long", action="store_true",
                           help="Rank on rest-of-season value (stashes) instead of this week's")

    trades_p = sub.add_parser("trades", help="Evaluate a proposed trade")
    trades_p.add_argument("--give", required=True, help="Comma-separated player names you'd give")
    trades_p.add_argument("--receive", required=True, help="Comma-separated player names you'd receive")
    trades_p.add_argument("--season", dest="season_long", action="store_true",
                          help="Judge on rest-of-season value instead of this week's")

    draft_p = sub.add_parser("draft", help="Real-time draft assistant (polls until it's your turn)")
    draft_p.add_argument("--draft-id", dest="draft_id", help="Target a specific draft (e.g. a mock) instead of your league's")
    draft_p.add_argument("--replay", action="store_true", help="Replay a completed draft instead of polling live")

    ir_p = sub.add_parser("ir", help="Show your IR slots, or set them (needs SLEEPER_TOKEN; asks you to confirm)")
    ir_p.add_argument("--set", help='Comma-separated names: the complete IR list ("" empties it)')

    add_p = sub.add_parser("add", help="Free-agent add and/or drop, immediate (needs SLEEPER_TOKEN; asks you to confirm)")
    add_p.add_argument("player", nargs="?", help="Free agent to add")
    add_p.add_argument("--drop", help="Player on your roster to drop")

    claim_p = sub.add_parser("claim", help="Waiver claim, or cancel one (needs SLEEPER_TOKEN; asks you to confirm)")
    claim_p.add_argument("player", nargs="?", help="Player to claim")
    claim_p.add_argument("--drop", help="Player on your roster to drop if the claim wins")
    claim_p.add_argument("--bid", type=int, help="FAAB bid (FAAB leagues only; default 0)")
    claim_p.add_argument("--cancel", metavar="ID", help="Cancel a pending claim by its id (see `claims`)")

    sub.add_parser("claims", help="Your pending waiver claims (needs SLEEPER_TOKEN)")

    digest_p = sub.add_parser("digest", help="Weekly digest: lineup, waiver board, news, last week's result")
    digest_p.add_argument("--week", type=int, help="Defaults to the current week")
    digest_p.add_argument("--email", action="store_true", help="Also send it by email")
    digest_p.add_argument("--to", help="Recipient (defaults to GMAIL_ADDRESS)")

    news_p = sub.add_parser("news", help="Recent news for your roster, or one named player")
    news_p.add_argument("--player", help="Look up one player by name (fetched live)")
    news_p.add_argument("--limit", type=int, default=3, help="Items per player (default: 3)")

    trending_p = sub.add_parser("trending", help="Most added/dropped players across all of Sleeper")
    trending_p.add_argument("--drop", action="store_true", help="Show most-dropped instead of most-added")
    trending_p.add_argument("--hours", type=int, default=24, help="Lookback window (default: 24)")
    trending_p.add_argument("--limit", type=int, default=25)

    results_p = sub.add_parser("results", help="Actual scored points for a week, and points left on your bench")
    results_p.add_argument("--week", type=int, help="Defaults to the last completed week")

    ask_p = sub.add_parser("ask", help="Ask the agent a question in natural language")
    ask_p.add_argument("question", help="e.g. \"who should I start at flex?\"")

    chat_p = sub.add_parser("chat", help="Interactive session with the agent (resumes your last conversation)")
    chat_p.add_argument("--new", action="store_true", help="Forget the saved conversation and start fresh")

    args = parser.parse_args()

    try:
        globals()[f"cmd_{args.command}"](args)
    except (SleeperError, AgentError, NotifyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
