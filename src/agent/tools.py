"""Tool registry: exposes the deterministic modules to the LLM.

Every tool here returns data computed by a Stage 0-3 module. The agent
never does arithmetic itself — if it would need to estimate a number, the
right fix is a new tool, not a cleverer prompt.

`evaluation/backtest.py` is deliberately NOT exposed: it's a dev-side
regression guard, not something to invoke mid-conversation.
"""

import json
import sqlite3
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool, tool

from src.decisions.lineup import optimize_lineup
from src.decisions.results import summarize_week
from src.decisions.trades import evaluate_trade
from src.decisions.waivers import suggest_waivers_by_position
from src.ingestion import storage
from src.ingestion.news import fetch_player_news
from src.ingestion.sleeper import SleeperClient
from src.projections.engine import (
    AdjustedProjection,
    build_projection_table,
    build_season_projection_table,
)


@dataclass
class AgentContext:
    """Everything the tools need that the model shouldn't have to supply."""

    conn: sqlite3.Connection
    league_id: str
    user_id: str
    season: str
    current_week: int
    # Both projection builds hit the network; cache so a multi-tool
    # conversation doesn't re-fetch on every call.
    _projection_cache: dict[int, list[AdjustedProjection]] = field(default_factory=dict)
    _season_cache: list[AdjustedProjection] | None = None

    def projections(self, week: int) -> list[AdjustedProjection]:
        if week not in self._projection_cache:
            self._projection_cache[week] = build_projection_table(self.conn, self.season, week)
        return self._projection_cache[week]

    def season_projections(self) -> list[AdjustedProjection]:
        if self._season_cache is None:
            self._season_cache = build_season_projection_table(self.conn, self.season)
        return self._season_cache

    def roster_positions(self) -> list[str]:
        row = self.conn.execute(
            "SELECT roster_positions FROM leagues WHERE league_id = ?", (self.league_id,)
        ).fetchone()
        return json.loads(row["roster_positions"]) if row else []


def _fmt_player(proj: AdjustedProjection) -> str:
    return f"{proj.name} ({proj.position or '?'}/{proj.team or 'FA'}): {proj.mean:.1f} pts"


def build_tools(ctx: AgentContext) -> list[BaseTool]:
    @tool
    def get_league_state() -> str:
        """Current season, week, my team name, and my opponent this week.
        Call this first when a question depends on 'this week' or 'my matchup'."""
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        me = storage.team_label(ctx.conn, ctx.league_id, ctx.user_id)
        opponent = storage.get_opponent_roster(ctx.conn, ctx.league_id, ctx.current_week, roster_id)
        opp_label = "none scheduled"
        if opponent:
            opp_roster = storage.get_roster(ctx.conn, ctx.league_id, opponent["roster_id"])
            if opp_roster:
                opp_label = storage.team_label(ctx.conn, ctx.league_id, opp_roster["owner_id"])
        return (
            f"Season {ctx.season}, week {ctx.current_week}. Your team: {me}. "
            f"Week {ctx.current_week} opponent: {opp_label}. "
            f"Roster slots: {', '.join(ctx.roster_positions())}"
        )

    @tool
    def get_my_roster() -> str:
        """List every player on my roster with position, team, and projected points."""
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        row = storage.get_roster(ctx.conn, ctx.league_id, roster_id)
        player_ids = json.loads(row["players"])
        by_id = {p.player_id: p for p in ctx.projections(ctx.current_week)}
        lines = []
        for pid in player_ids:
            proj = by_id.get(pid)
            lines.append(_fmt_player(proj) if proj else f"{storage.player_name(ctx.conn, pid)}: no projection")
        return "\n".join(lines)

    @tool
    def get_team_roster(team_name: str) -> str:
        """List another manager's roster. `team_name` is matched loosely against
        team and display names in the league."""
        row = storage.find_roster_by_team_query(ctx.conn, ctx.league_id, team_name)
        if not row:
            return f"No team matching '{team_name}'."
        label = storage.team_label(ctx.conn, ctx.league_id, row["owner_id"])
        by_id = {p.player_id: p for p in ctx.projections(ctx.current_week)}
        lines = [f"{label}:"]
        for pid in json.loads(row["players"]):
            proj = by_id.get(pid)
            lines.append("  " + (_fmt_player(proj) if proj else storage.player_name(ctx.conn, pid)))
        return "\n".join(lines)

    @tool
    def get_recent_transactions(limit: int = 10) -> str:
        """Recent adds, drops, waiver claims and trades across the league."""
        txns = storage.get_recent_transactions(ctx.conn, ctx.league_id, limit=limit)
        if not txns:
            return "No transactions recorded."
        lines = []
        for t in txns:
            adds = ", ".join(storage.player_name(ctx.conn, pid) for pid in (json.loads(t["adds"]) or {}))
            drops = ", ".join(storage.player_name(ctx.conn, pid) for pid in (json.loads(t["drops"]) or {}))
            lines.append(f"{t['type']} ({t['status']}): +[{adds or 'none'}] -[{drops or 'none'}]")
        return "\n".join(lines)

    @tool
    def get_projections(position: str = "", limit: int = 20, week: int = 0) -> str:
        """Top projected players for a week, optionally filtered to one position
        (QB/RB/WR/TE/K/DL/LB/DB). Each line includes the mean projection, its
        uncertainty (std dev), and how many sources agreed. Defaults to the
        current week."""
        target_week = week or ctx.current_week
        table = ctx.projections(target_week)
        if position:
            table = [p for p in table if (p.position or "").upper() == position.upper()]
        lines = []
        for p in table[:limit]:
            lines.append(
                f"{_fmt_player(p)}, std {p.variance ** 0.5:.1f}, {p.num_sources} source(s)"
            )
        return "\n".join(lines) or "No projections found."

    @tool
    def get_season_projections(position: str = "", limit: int = 20) -> str:
        """Full-SEASON projected point totals (not weekly), optionally filtered to
        one position. Use this for questions about a player's overall/rest-of-year
        value, keeper or dynasty value, or comparing players beyond this week.
        For 'who do I start this week', use get_projections or recommend_lineup
        instead."""
        table = ctx.season_projections()
        if position:
            table = [p for p in table if (p.position or "").upper() == position.upper()]
        lines = [
            f"{_fmt_player(p)} for the full season, std {p.variance ** 0.5:.1f}"
            for p in table[:limit]
        ]
        return "\n".join(lines) or "No season projections found."

    @tool
    def recommend_lineup(week: int = 0) -> str:
        """The optimal start/sit for my roster, solved against my league's roster
        slots. Returns each starting slot, who fills it, and why. Defaults to the
        current week."""
        target_week = week or ctx.current_week
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        row = storage.get_roster(ctx.conn, ctx.league_id, roster_id)
        player_ids = json.loads(row["players"])

        projections = {p.player_id: p for p in ctx.projections(target_week)}
        player_meta = {}
        for pid in player_ids:
            p = ctx.conn.execute(
                "SELECT first_name, last_name, position FROM players WHERE player_id = ?", (pid,)
            ).fetchone()
            if p:
                name = " ".join(x for x in [p["first_name"], p["last_name"]] if x) or pid
                player_meta[pid] = {"name": name, "position": p["position"]}

        slots = optimize_lineup(ctx.roster_positions(), player_ids, projections, player_meta)
        total = sum(s.mean for s in slots)
        lines = [f"{s.slot}: {s.name} ({s.mean:.1f} pts) - {s.why}" for s in slots]
        lines.append(f"Total projected: {total:.1f}")
        return "\n".join(lines)

    @tool
    def get_waiver_targets(position: str = "", limit_per_position: int = 5, week: int = 0) -> str:
        """Best available (unrostered) players, grouped by position, ranked by
        value over replacement, with a suggested FAAB bid percentage. Optionally
        filter to one position."""
        target_week = week or ctx.current_week
        grouped = suggest_waivers_by_position(
            ctx.conn, ctx.league_id, ctx.roster_positions(), ctx.projections(target_week), limit_per_position
        )
        if position:
            grouped = {k: v for k, v in grouped.items() if k.upper() == position.upper()}
        if not grouped:
            return "No waiver targets found."
        lines = []
        for group, suggestions in grouped.items():
            baseline = suggestions[0].proj.mean - suggestions[0].vor
            lines.append(f"{group} (replacement level {baseline:.1f} pts):")
            for s in suggestions:
                lines.append(f"  {_fmt_player(s.proj)}, VOR {s.vor:+.1f}, suggested bid {s.bid_pct}% of FAAB")
        return "\n".join(lines)

    @tool
    def evaluate_proposed_trade(give: str, receive: str, horizon: str = "season") -> str:
        """Score a trade by projected value change. `give` and `receive` are
        comma-separated player names, e.g. give='Saquon Barkley', receive='Josh Allen'.
        `horizon` is 'season' (full-season value, the default and usually what a
        trade should be judged on) or 'week' (this week's projection only)."""
        def resolve(names: str) -> tuple[list[str], list[str]]:
            ids, missing = [], []
            for name in [n.strip() for n in names.split(",") if n.strip()]:
                pid = storage.find_player_id_by_name(ctx.conn, name)
                (ids if pid else missing).append(pid or name)
            return ids, missing

        give_ids, give_missing = resolve(give)
        recv_ids, recv_missing = resolve(receive)
        if give_missing or recv_missing:
            return f"Could not find player(s): {', '.join(give_missing + recv_missing)}"

        use_season = horizon.lower() != "week"
        table = ctx.season_projections() if use_season else ctx.projections(ctx.current_week)
        label = "full season" if use_season else f"week {ctx.current_week}"
        projections = {p.player_id: p for p in table}
        result = evaluate_trade(give_ids, recv_ids, projections)
        return (
            f"Horizon: {label}\n"
            f"Give: {', '.join(p.name for p in result.give)} ({result.give_total:.1f} pts)\n"
            f"Receive: {', '.join(p.name for p in result.receive)} ({result.receive_total:.1f} pts)\n"
            f"Verdict: {result.verdict} ({result.delta:+.1f} pts over the {label})"
        )

    def _format_news(rows, header: str) -> str:
        lines = [header]
        for r in rows:
            lines.append(f"- {r['title']}")
            if r["description"]:
                lines.append(f"  {r['description']}")
            if r["analysis"]:
                lines.append(f"  Analysis: {r['analysis']}")
        return "\n".join(lines)

    @tool
    def get_player_news(player_name: str, limit: int = 3) -> str:
        """Recent news for one player, including a factual description and a
        fantasy analysis of what it means. Use this when a question is about
        a specific player's situation, role, or injury - it carries context
        the projections cannot (hype, depth-chart chatter, matchup notes)."""
        pid = storage.find_player_id_by_name(ctx.conn, player_name)
        if not pid:
            return f"No player matching '{player_name}'."
        try:
            items = fetch_player_news(pid, limit=min(limit, 5))
            if items:
                storage.save_player_news(ctx.conn, items)
        except Exception:
            pass  # fall through to whatever is cached
        rows = storage.get_player_news(ctx.conn, pid, limit=min(limit, 5))
        if not rows:
            return f"No news found for {storage.player_name(ctx.conn, pid)}."
        return _format_news(rows, f"News for {storage.player_name(ctx.conn, pid)}:")

    @tool
    def get_roster_news(items_per_player: int = 2) -> str:
        """Recent news across every player on my roster, from the local cache
        populated at sync time. Use for open-ended 'anything I should know this
        week' questions. Capped to keep the response readable."""
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        row = storage.get_roster(ctx.conn, ctx.league_id, roster_id)
        capped = max(1, min(items_per_player, 3))

        chunks = []
        for pid in json.loads(row["players"]):
            rows = storage.get_player_news(ctx.conn, pid, limit=capped)
            if rows:
                chunks.append(_format_news(rows, f"{storage.player_name(ctx.conn, pid)}:"))
        if not chunks:
            return "No cached roster news. A `sync_league` call refreshes it."
        return "\n\n".join(chunks)

    @tool
    def get_trending_players(kind: str = "add", limit: int = 15, lookback_hours: int = 24) -> str:
        """Players being added or dropped most across ALL Sleeper leagues right now.
        `kind` is 'add' or 'drop'. This is market sentiment - it reflects news and
        hype the projection system cannot see - so use it as context alongside
        projections, never as a replacement for them. Each line says whether the
        player is already rostered in this league."""
        with SleeperClient() as client:
            trending = client.get_trending(kind if kind in ("add", "drop") else "add", lookback_hours, limit)
        if not trending:
            return "No trending data available."

        rostered: set[str] = set()
        for row in ctx.conn.execute("SELECT players FROM rosters WHERE league_id = ?", (ctx.league_id,)):
            rostered.update(json.loads(row["players"]))

        lines = []
        for entry in trending:
            pid = entry["player_id"]
            status = "already rostered in this league" if pid in rostered else "available"
            lines.append(f"{storage.player_name(ctx.conn, pid)}: {entry['count']:,} leagues ({status})")
        return "\n".join(lines)

    @tool
    def get_week_results(week: int = 0) -> str:
        """What my team ACTUALLY scored in a completed week: per-player points,
        the total, the best lineup possible in hindsight, and how many points were
        left on the bench. Use for 'how did I do' or 'did I start the right guys'
        questions. Defaults to the current week."""
        target_week = week or ctx.current_week
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        result = summarize_week(ctx.conn, ctx.league_id, roster_id, target_week, ctx.roster_positions())
        if not result:
            return f"No scored results stored for week {target_week} yet."

        started = "\n".join(f"  {name}: {pts:.1f}" for name, pts in result.started)
        bench = "\n".join(f"  {name}: {pts:.1f}" for name, pts in result.bench)
        return (
            f"Week {result.week} actual results.\nStarted:\n{started}\n"
            f"Actual total: {result.actual_total:.1f}\n"
            f"Best possible with hindsight: {result.optimal_total:.1f}\n"
            f"Points left on bench: {result.points_left_on_bench:.1f}\n"
            f"Bench:\n{bench}"
        )

    @tool
    def sync_league() -> str:
        """Refresh local league data from Sleeper (rosters, matchups, transactions).
        Use when the user says data looks stale or asks to refresh."""
        with SleeperClient() as client:
            rosters = client.get_rosters(ctx.league_id)
            storage.save_rosters(ctx.conn, ctx.league_id, rosters)
            matchups = client.get_matchups(ctx.league_id, ctx.current_week)
            if matchups:
                storage.save_matchups(ctx.conn, ctx.league_id, ctx.current_week, matchups)
            txns = client.get_transactions(ctx.league_id, ctx.current_week)
            if txns:
                storage.save_transactions(ctx.conn, ctx.league_id, ctx.current_week, txns)
        ctx._projection_cache.clear()
        return f"Refreshed rosters, matchups and transactions for week {ctx.current_week}."

    return [
        get_league_state,
        get_my_roster,
        get_team_roster,
        get_recent_transactions,
        get_projections,
        get_season_projections,
        recommend_lineup,
        get_waiver_targets,
        evaluate_proposed_trade,
        get_player_news,
        get_roster_news,
        get_trending_players,
        get_week_results,
        sync_league,
    ]
