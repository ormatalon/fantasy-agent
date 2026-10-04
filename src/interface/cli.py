"""CLI entry point.

Usage (from repo root):
    uv run python -m src.interface.cli sync [--league NAME] [--all]
    uv run python -m src.interface.cli leagues
    uv run python -m src.interface.cli use "league name"
    uv run python -m src.interface.cli roster [--team "name"]
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
from src.decisions.lineup import optimize_lineup
from src.decisions.results import last_completed_week, summarize_week
from src.decisions.trades import evaluate_trade
from src.decisions.waivers import suggest_waivers_by_position, unavailable_this_week
from src.evaluation.backtest import run_backtest
from src.ingestion import storage
from src.ingestion.news import fetch_player_news
from src.ingestion.schedule import load_kickoffs
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

    table = build_projection_table(conn, season, week, league_id)
    projections = {row.player_id: row for row in table}

    player_meta = {}
    for pid in roster_player_ids:
        p = conn.execute("SELECT first_name, last_name, position FROM players WHERE player_id = ?", (pid,)).fetchone()
        if p:
            name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) or pid
            player_meta[pid] = {"name": name, "position": p["position"]}

    lineup = optimize_lineup(roster_positions, roster_player_ids, projections, player_meta)

    if args.live or args.apply:
        from src.execution.browser import probe_moves, read_lineup, set_lineup, surname
        from src.execution.approval import ApprovalDenied, ProposedWrite, require_approval

        print("Reading your current lineup from Sleeper...\n")
        # Slots repeat (two RB, two WR, two TE), so these must be matched
        # positionally - a dict keyed by slot name silently collapses them.
        live_rows = read_lineup(league_id)
        live_starters = [(s, n, lk) for s, n, lk in live_rows if s not in {"BN", "IR"}]
        # A swap is blocked if EITHER side has kicked off: you can't bench
        # someone who already played, and you can't start someone who has.
        # The incoming player is usually on the bench, so this covers all rows.
        locked_by_surname = {surname(n): lk for _s, n, lk in live_rows}

        # Two RBs swapping which RB slot they occupy is not a change. Compare
        # per slot *type* (surnames, since Sleeper abbreviates first names),
        # otherwise a reordering reads as work that needs doing.
        live_by_slot: dict[str, set[str]] = {}
        for slot_name, player, _locked in live_starters:
            live_by_slot.setdefault(slot_name, set()).add(surname(player))

        print(f"Week {week}: current vs recommended\n")
        print(f"{'Slot':<12}{'Currently':<22}{'Recommended':<22}")
        moves = []          # (slot_row_index, player_name) for the browser
        descriptions = []
        locked_out = []     # real differences that kickoff has put out of reach
        for i, slot in enumerate(lineup):
            now, outgoing_locked = (live_starters[i][1], live_starters[i][2]) if i < len(live_starters) else ("?", False)
            incoming_locked = locked_by_surname.get(surname(slot.name), False)
            already_starting = surname(slot.name) in live_by_slot.get(slot.slot, set())
            if already_starting:
                note = ""
            elif incoming_locked or outgoing_locked:
                who = slot.name if incoming_locked else now
                locked_out.append(f"{slot.slot}: {now} -> {slot.name} ({who} already played)")
                note = f"  <-- locked ({who} already played)"
            else:
                moves.append((i, slot.name))
                descriptions.append(f"{slot.slot}: {now} -> {slot.name}")
                note = "  <-- change"
            print(f"{slot.slot:<12}{now:<22}{slot.name:<22}{note}")

        if not moves:
            if locked_out:
                # Not the same thing as matching - the difference is real, it
                # just can't be acted on any more.
                print(f"\nNothing actionable: {len(locked_out)} difference(s) locked by kickoff.")
                for description in locked_out:
                    print(f"  - {description}")
            else:
                print("\nLineup already matches the recommendation.")
            return

        print(f"\n{len(moves)} change(s) needed:")
        for description in descriptions:
            print(f"  - {description}")

        if not args.apply:
            print("\nRe-run with --apply to enact these (you'll be asked to confirm).")
            return

        # Ask Sleeper which of these it will accept before proposing them - a
        # player whose game has kicked off can't be moved, and that should be
        # known now rather than after approval.
        print("\nChecking which changes Sleeper will accept...")
        actionable, blocked = probe_moves(league_id, moves)

        if blocked:
            print("\nCannot be changed:")
            for slot_index, name, reason in blocked:
                print(f"  - {lineup[slot_index].slot}: {name} ({reason})")

        if not actionable:
            print("\nNothing left to apply.")
            return

        descriptions = [f"{lineup[i].slot}: {name}" for i, name in actionable]
        print(f"\nWill apply {len(actionable)} change(s).")

        write = ProposedWrite(
            action="set_lineup",
            summary=f"Set your week {week} Sleeper lineup ({len(actionable)} change(s)).",
            changes=descriptions,
            payload={"week": week, "moves": [[i, name] for i, name in actionable]},
        )
        moves = actionable
        try:
            approval = require_approval(write)
        except ApprovalDenied as e:
            print(f"\n{e}")
            return

        print("\nApplying...")
        final = set_lineup(league_id, moves, write, approval)
        print("\nLineup now set on Sleeper:")
        for slot_name, player, _locked in final:
            if slot_name not in {"BN", "IR"}:
                print(f"  {slot_name:<12}{player}")
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

    league_row = conn.execute("SELECT roster_positions FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    roster_positions = json.loads(league_row["roster_positions"])

    excluded: dict[str, str] = {}
    if args.season_long:
        table = build_season_projection_table(conn, season, league_id, from_week=current_week)
        print(f"Rest-of-season waiver targets (weeks {current_week}+)\n")
    else:
        table = build_projection_table(conn, season, week, league_id)
        if week == current_week:
            excluded = unavailable_this_week(table, load_kickoffs(season, week))
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
        roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
        row = storage.get_roster(conn, league_id, roster_id) if roster_id is not None else None
        if not row:
            print("Could not find your roster in this league.", file=sys.stderr)
            sys.exit(1)
        player_ids = json.loads(row["players"])

    printed = 0
    for pid in player_ids:
        items = storage.get_player_news(conn, pid, limit=args.limit)
        if not items:
            continue
        status = storage.get_injury_status(conn, pid)
        print(f"\n{storage.player_name(conn, pid)}{f' [{status}]' if status else ''}")
        for item in items:
            when = (
                datetime.fromtimestamp(item["published"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                if item["published"] else "?"
            )
            print(f"  [{when}] {item['title']}")
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

    roster_id = storage.get_roster_id_for_user(conn, league_id, user_id)
    if roster_id is None:
        print("Could not find your roster in this league.", file=sys.stderr)
        sys.exit(1)
    league_row = conn.execute("SELECT roster_positions FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    roster_positions = json.loads(league_row["roster_positions"])

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


def cmd_login(args: argparse.Namespace) -> None:
    # Imported lazily so the rest of the CLI works without playwright present.
    from src.execution.browser import CHROME_CDP_HINT, cdp_available, check_session, login

    if args.check:
        via = "your running Chrome" if cdp_available() else "the saved profile"
        if check_session():
            print(f"Session is valid (via {via}).")
        else:
            print(f"No valid session (checked {via}).")
            if not cdp_available():
                print(f"\nSleeper's login blocks automated browsers. {CHROME_CDP_HINT}")
        return

    if cdp_available():
        print("Attached to your running Chrome - no separate login needed.")
        print("Session is valid." if check_session() else "That Chrome isn't logged in to Sleeper.")
        return

    print(f"Sleeper's login blocks automated browsers.\n\n{CHROME_CDP_HINT}\n")
    print("Attempting the isolated-profile login anyway (may be blocked)...")
    if not login():
        sys.exit(1)


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
    roster_p.add_argument("--team", help="Team/owner name to look up (defaults to your own roster)")

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
                          help="Enact the changes on Sleeper (asks you to confirm first)")

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

    login_p = sub.add_parser("login", help="Log in to Sleeper once in a browser; the session is saved")
    login_p.add_argument("--check", action="store_true", help="Only report whether a saved session is still valid")

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
        if args.command == "sync":
            cmd_sync(args)
        elif args.command == "leagues":
            cmd_leagues(args)
        elif args.command == "use":
            cmd_use(args)
        elif args.command == "graph":
            cmd_graph(args)
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
        elif args.command == "login":
            cmd_login(args)
        elif args.command == "digest":
            cmd_digest(args)
        elif args.command == "news":
            cmd_news(args)
        elif args.command == "trending":
            cmd_trending(args)
        elif args.command == "results":
            cmd_results(args)
        elif args.command == "ask":
            cmd_ask(args)
        elif args.command == "chat":
            cmd_chat(args)
    except (SleeperError, AgentError, NotifyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
