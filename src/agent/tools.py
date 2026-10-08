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

from src.decisions.leaders import season_to_date
from src.decisions.lineup import SleeperLineup, optimize_lineup, sleeper_lineup
from src.decisions.results import last_completed_week, summarize_week
from src.decisions.trades import evaluate_trade
from src.decisions.waivers import get_rostered_player_ids, suggest_waivers_by_position, unavailable_this_week
from src.ingestion import nfl_teams, schedule, storage
from src.ingestion.news import fetch_player_news
from src.ingestion.sleeper import SleeperClient, SleeperError
from src.ingestion.sync import log_to_stderr, run_sync
from src.projections.engine import (
    AdjustedProjection,
    build_projection_table,
    build_season_projection_table,
    weeks_remaining,
)
from src.projections.positions import GRANULAR_TO_GROUP, IDP_POSITIONS, ROSTER_SLOT_ELIGIBILITY, SKILL_POSITIONS

# Designations that make a player a stash candidate rather than a start.
STASH_DESIGNATIONS = {"IR", "PUP", "Out", "Doubtful", "Sus", "NA", "COV"}
# Sleeper always lets an IR-designated player into an IR slot; whether Out /
# Suspended / etc. also qualify is a per-league setting we don't sync.
IR_SLOT_DEFINITE = {"IR"}
IR_SLOT_MAYBE = {"Out", "PUP", "Sus", "NA", "COV", "DNR"}


@dataclass
class AgentContext:
    """Everything the tools need that the model shouldn't have to supply."""

    conn: sqlite3.Connection
    league_id: str
    user_id: str
    season: str
    current_week: int
    # Projection builds and the schedule hit the network; cache so a
    # multi-tool conversation doesn't re-fetch on every call.
    _cache: dict = field(default_factory=dict)

    def _cached(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def projections(self, week: int) -> list[AdjustedProjection]:
        return self._cached(("week", week), lambda: build_projection_table(self.conn, self.season, week, self.league_id))

    def season_projections(self, rest_of_season: bool = True, healthy: bool = False) -> list[AdjustedProjection]:
        return self._cached(
            ("season", rest_of_season, healthy),
            lambda: build_season_projection_table(
                self.conn, self.season, self.league_id,
                from_week=self.current_week if rest_of_season else None,
                apply_injury=not healthy,
            ),
        )

    def kickoffs(self, week: int) -> dict:
        return self._cached(("kickoffs", week), lambda: schedule.load_kickoffs(self.season, week))

    def clear_caches(self) -> None:
        self._cache.clear()

    def reload_state(self) -> None:
        """Pick up what a sync changed: the active league, season and week."""
        self.league_id = storage.get_state(self.conn, "active_league_id") or self.league_id
        self.season = storage.get_state(self.conn, "active_season") or self.season
        self.current_week = int(storage.get_state(self.conn, "current_week") or self.current_week)
        self.clear_caches()

    def _league_row(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT name, scoring_settings, roster_positions FROM leagues WHERE league_id = ?", (self.league_id,)
        ).fetchone()

    def roster_positions(self) -> list[str]:
        row = self._league_row()
        return json.loads(row["roster_positions"]) if row else []

    def scoring_settings(self) -> dict[str, float]:
        row = self._league_row()
        return json.loads(row["scoring_settings"]) if row else {}

    def league_name(self) -> str:
        row = self._league_row()
        return (row["name"] if row else None) or self.league_id

    def fantasy_team_name(self) -> str:
        return storage.fantasy_team_label(self.conn, self.league_id, self.user_id)

    def other_league_names(self) -> list[str]:
        known = json.loads(storage.get_state(self.conn, "user_leagues") or "[]")
        return [lg["name"] or lg["league_id"] for lg in known if lg["league_id"] != self.league_id]


def _status_tag(status: str | None) -> str:
    return f" [{status}]" if status else ""


def _fmt_player(proj: AdjustedProjection) -> str:
    return f"{proj.name} ({proj.position or '?'}/{proj.team or 'FA'}){_status_tag(proj.injury_status)}: {proj.mean:.1f} pts"


def _group(position: str | None) -> str:
    return GRANULAR_TO_GROUP.get(position, position) or ""


def _matches_position(proj: AdjustedProjection, position: str) -> bool:
    """A filter of 'DL' should match DE/DT too, not only a literal 'DL'."""
    wanted = position.upper()
    return (proj.position or "").upper() == wanted or _group(proj.position).upper() == wanted


def _ir_hint(status: str | None) -> str:
    if status in IR_SLOT_DEFINITE:
        return " -> eligible for your IR slot (frees a bench spot) if not already there"
    if status in IR_SLOT_MAYBE:
        return f" -> may be IR-slot eligible, depending on your league's settings for '{status}'"
    return ""


# Tools whose output depends on who is on which roster. After my roster
# changes, their earlier results are hidden from the model (see graph.py).
ROSTER_TOOLS = frozenset({
    "get_my_roster",
    "get_fantasy_team_roster",
    "get_nfl_team_roster",
    "recommend_lineup",
    "get_waiver_targets",
    "get_injured_stash_candidates",
    "get_trending_players",
})


def my_lineup(ctx: AgentContext) -> SleeperLineup | None:
    roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
    row = storage.get_roster(ctx.conn, ctx.league_id, roster_id) if roster_id is not None else None
    if not row:
        return None
    return sleeper_lineup(
        json.loads(row["players"]), json.loads(row["starters"]), json.loads(row["reserve"] or "[]"),
        ctx.roster_positions(),
    )


def format_lineup(conn: sqlite3.Connection, lineup: SleeperLineup, projections: dict | None = None) -> list[str]:
    """The lineup as set in Sleeper, grouped Starters / Bench / IR. One
    formatter for both get_my_roster and the system prompt, so the two never
    disagree. Without `projections` (the prompt), no numbers are shown."""

    def line(pid: str, hint: bool = True) -> str:
        status = storage.get_injury_status(conn, pid)
        if projections is None:
            text = storage.player_name(conn, pid) + _status_tag(status)
        else:
            proj = projections.get(pid)
            text = _fmt_player(proj) if proj else f"{storage.player_name(conn, pid)}{_status_tag(status)}: no projection"
        return text + (_ir_hint(status) if hint else "")

    lines = ["Starters:"]
    lines += [f"  {slot}: {line(pid) if pid else '(empty)'}" for slot, pid in lineup.starters]
    lines.append("Bench:" if lineup.bench else "Bench: (none)")
    lines += [f"  {line(pid)}" for pid in lineup.bench]
    if lineup.ir:
        lines.append("IR:")
        lines += [f"  {line(pid, hint=False)}" for pid in lineup.ir]
    return lines


def build_tools(ctx: AgentContext) -> list[BaseTool]:
    def resolve(name: str) -> tuple[str | None, str | None]:
        """(player_id, None) or (None, message explaining the miss/ambiguity)."""
        pid, candidates = storage.resolve_player(ctx.conn, name, ctx.league_id)
        if pid:
            return pid, None
        if not candidates:
            return None, f"No player matching '{name}'."
        options = "; ".join(storage.player_name(ctx.conn, c) for c in candidates)
        return None, (
            f"'{name}' is ambiguous: {options}. Ask the user which one, then retry with the "
            "name exactly as listed, e.g. 'First Last (POS/TEAM)'."
        )

    def my_roster_ids() -> list[str] | None:
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return None
        return json.loads(storage.get_roster(ctx.conn, ctx.league_id, roster_id)["players"])

    @tool
    def get_league_state() -> str:
        """Current season, week, league, my team name, my opponent this week, and
        the league's starting roster slots. Call this first when a question
        depends on 'this week', 'my matchup', or the league's format."""
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        opponent = storage.get_opponent_roster(ctx.conn, ctx.league_id, ctx.current_week, roster_id)
        opp_label = "none scheduled"
        if opponent:
            opp_roster = storage.get_roster(ctx.conn, ctx.league_id, opponent["roster_id"])
            if opp_roster:
                opp_label = storage.fantasy_team_label(ctx.conn, ctx.league_id, opp_roster["owner_id"])
        return (
            f"Season {ctx.season}, week {ctx.current_week}. League: {ctx.league_name()}. "
            f"Your fantasy team: {ctx.fantasy_team_name()}. Week {ctx.current_week} opponent: {opp_label}. "
            f"Roster slots: {', '.join(ctx.roster_positions())}"
        )

    @tool
    def get_my_roster() -> str:
        """My roster exactly as set in Sleeper: starters by slot, bench, and IR
        slots, each with position, team, injury designation in [brackets] and
        this week's projected points. Flags players who can move to an IR slot. Use for 'who are my starters', 'what's my lineup', 'who's on my
        team', 'is anyone hurt', and before any roster-management advice. For
        what the lineup SHOULD be, use recommend_lineup instead."""
        lineup = my_lineup(ctx)
        if lineup is None:
            return "Could not find your roster in this league."
        by_id = {p.player_id: p for p in ctx.projections(ctx.current_week)}
        return "\n".join(["My lineup as set in Sleeper:", *format_lineup(ctx.conn, lineup, by_id)])

    @tool
    def get_fantasy_team_roster(fantasy_team: str) -> str:
        """List a fantasy team's roster: another manager's team in my league
        (with injury designations and this week's projections). `fantasy_team`
        is matched loosely against the league's fantasy team names and manager
        display names. Not for NFL teams (Seattle, Chiefs, ...): use
        get_nfl_team_roster for those."""
        row = storage.find_roster_by_fantasy_team(ctx.conn, ctx.league_id, fantasy_team)
        if not row:
            if nfl_teams.resolve_nfl_team(fantasy_team):
                return (
                    f"No fantasy team in my league matches '{fantasy_team}'. It names an NFL team: "
                    "call get_nfl_team_roster for that roster."
                )
            return f"No fantasy team matching '{fantasy_team}'."
        label = storage.fantasy_team_label(ctx.conn, ctx.league_id, row["owner_id"])
        by_id = {p.player_id: p for p in ctx.projections(ctx.current_week)}
        lines = [f"{label}:"]
        for pid in json.loads(row["players"]):
            proj = by_id.get(pid)
            status = storage.get_injury_status(ctx.conn, pid)
            lines.append("  " + (_fmt_player(proj) if proj else storage.player_name(ctx.conn, pid) + _status_tag(status)))
        return "\n".join(lines)

    @tool
    def get_nfl_team_roster(team: str) -> str:
        """Who plays for an NFL team right now, from the synced Sleeper player
        catalog: fantasy-relevant positions in depth-chart order, with injury
        designations, this week's projection, and whether each player is on my
        roster, another manager's roster, or a free agent. `team` can be an
        abbreviation, city or nickname ('SEA', 'Seattle', 'Seahawks'). Use for
        'who is on <NFL team>', 'who is <team>'s WR1', or which team a player
        is on. Never answer these from memory: players change teams."""
        matches = nfl_teams.resolve_nfl_team(team)
        if not matches:
            return f"No NFL team matching '{team}'."
        if len(matches) > 1:
            options = "; ".join(nfl_teams.nfl_team_label(a) for a in matches)
            return f"'{team}' is ambiguous: {options}. Ask the user which one, then retry."
        abbr = matches[0]
        slots = set(ctx.roster_positions())
        eligible = set().union(*(ROSTER_SLOT_ELIGIBILITY.get(s, set()) for s in slots))
        positions = [p for p in [*SKILL_POSITIONS, "DEF", *IDP_POSITIONS] if p in eligible]
        players = storage.players_on_nfl_team(ctx.conn, abbr, positions)
        if not players:
            return f"No players listed for {nfl_teams.nfl_team_label(abbr)}. Try sync_league."

        # player_id -> owner label; my players also carry their slot, so the
        # model never has to guess whether one of mine starts.
        owners: dict[str, str] = {}
        for r in ctx.conn.execute("SELECT roster_id, owner_id, players FROM rosters WHERE league_id = ?", (ctx.league_id,)):
            if r["owner_id"] != ctx.user_id:
                label = (
                    storage.fantasy_team_label(ctx.conn, ctx.league_id, r["owner_id"]) if r["owner_id"]
                    else f"unclaimed fantasy team (roster {r['roster_id']})"
                )
                owners.update({pid: label for pid in json.loads(r["players"] or "[]")})
        my_slots: dict[str, str] = {}
        lineup = my_lineup(ctx)
        if lineup:
            my_slots.update({pid: "starter" for _, pid in lineup.starters if pid})
            my_slots.update({pid: "bench" for pid in lineup.bench})
            my_slots.update({pid: "IR" for pid in lineup.ir})
        owners.update({pid: "MY ROSTER" for pid in my_slots})
        by_id = {p.player_id: p for p in ctx.projections(ctx.current_week)}

        # One ownership line per fantasy team, mine first: per-player labels
        # alone get dropped when the model summarizes a long list.
        rostered: dict[str, list[str]] = {}
        for p in players:
            pid = p["player_id"]
            if pid in owners:
                slot = f" ({my_slots[pid]})" if pid in my_slots else ""
                rostered.setdefault(owners[pid], []).append(f"{p['first_name']} {p['last_name']}{slot}")
        summary = "; ".join(
            f"{owner}: {', '.join(names)}" for owner, names in sorted(rostered.items(), key=lambda kv: kv[0] != "MY ROSTER")
        )
        lines = [
            f"{nfl_teams.nfl_team_label(abbr)}, from the Sleeper player catalog "
            f"(synced {storage.players_last_synced(ctx.conn)}). Depth-chart order within each position.",
            f"Rostered in my league: {summary or 'none'}. Everyone else is a free agent.",
        ]
        for pos in positions:
            group = [p for p in players if p["position"] == pos]
            if not group:
                continue
            lines.append(f"{pos}:")
            for p in group:
                proj = by_id.get(p["player_id"])
                text = _fmt_player(proj) if proj else storage.player_name(ctx.conn, p["player_id"]) + _status_tag(p["injury_status"])
                if p["status"] and p["status"] != "Active" and not p["injury_status"]:
                    text += f" (status: {p['status']})"
                pid = p["player_id"]
                owner = owners.get(pid, "free agent") + (f" ({my_slots[pid]})" if pid in my_slots else "")
                lines.append(f"  {text} - {owner}")
        return "\n".join(lines)

    @tool
    def get_recent_transactions(limit: int = 10) -> str:
        """Recent adds, drops, waiver claims and trades across the league, newest first."""
        txns = storage.get_recent_transactions(ctx.conn, ctx.league_id, limit=limit)
        if not txns:
            return "No transactions recorded."
        lines = []
        for t in txns:
            adds = ", ".join(storage.player_name(ctx.conn, pid) for pid in (json.loads(t["adds"]) or {}))
            drops = ", ".join(storage.player_name(ctx.conn, pid) for pid in (json.loads(t["drops"]) or {}))
            lines.append(f"week {t['week']} {t['type']} ({t['status']}): +[{adds or 'none'}] -[{drops or 'none'}]")
        return "\n".join(lines)

    @tool
    def get_projections(position: str = "", limit: int = 20, week: int = 0) -> str:
        """Top projected players (rostered or not) for ONE week, optionally
        filtered to a position (QB/RB/WR/TE/K/DEF, or IDP groups DL/LB/DB). Each
        line has the mean projection, injury designation, its uncertainty (std
        dev), and how many sources agreed. Projections already include injury
        discounts (an Out/IR player projects ~0). Defaults to the current week."""
        target_week = week or ctx.current_week
        table = ctx.projections(target_week)
        if position:
            table = [p for p in table if _matches_position(p, position)]
        lines = [f"{_fmt_player(p)}, std {p.variance ** 0.5:.1f}, {p.num_sources} source(s)" for p in table[:limit]]
        return "\n".join(lines) or "No projections found."

    @tool
    def get_season_projections(position: str = "", limit: int = 20, full_season: bool = False) -> str:
        """Season-long projected point totals, optionally filtered to one
        position. By default these are REST-OF-SEASON (from the current week on),
        which is what trade, stash and 'who's better going forward' questions
        need; set full_season=True for whole-season totals (e.g. comparing to
        preseason rankings). IR/PUP/suspended players are discounted to 0 here -
        for injured stash targets use get_injured_stash_candidates instead. For
        'who do I start this week', use get_projections or recommend_lineup."""
        table = ctx.season_projections(rest_of_season=not full_season)
        if position:
            table = [p for p in table if _matches_position(p, position)]
        label = "full season" if full_season else f"rest of season (weeks {ctx.current_week}+)"
        lines = [f"{_fmt_player(p)} over the {label}, std {p.variance ** 0.5:.1f}" for p in table[:limit]]
        return "\n".join(lines) or "No season projections found."

    @tool
    def get_season_to_date_leaders(position: str = "", limit: int = 20) -> str:
        """Points players have ACTUALLY scored so far this season (all completed
        weeks), league-wide - rostered or not - under this league's scoring,
        with games played and per-game average. Use for 'top RBs so far', 'who
        has been the best TE this year', 'is X having a good season'. For future
        value use get_season_projections; for my own team's weekly scores use
        get_week_results."""
        through = last_completed_week(ctx.current_week)
        try:
            leaders = ctx._cached(
                ("leaders", through),
                lambda: season_to_date(ctx.conn, ctx.season, through, ctx.scoring_settings(), limit=500),
            )
        except Exception as e:
            return f"Season-to-date stats are unavailable right now ({e})."
        if position:
            leaders = [lead for lead in leaders if position.upper() in (
                (lead.position or "").upper(), _group(lead.position).upper())]
        if not leaders:
            return f"No {ctx.season} stats found through week {through}."
        lines = [f"{ctx.season} actual points, weeks 1-{through}:"]
        for lead in leaders[: max(1, min(limit, 50))]:
            lines.append(
                f"{lead.name} ({lead.position or '?'}/{lead.team or 'FA'}){_status_tag(lead.injury_status)}: "
                f"{lead.total:.1f} pts in {lead.games} game(s), {lead.total / max(lead.games, 1):.1f}/game"
            )
        return "\n".join(lines)

    @tool
    def recommend_lineup(week: int = 0) -> str:
        """The optimal start/sit for my roster, solved against my league's roster
        slots from projections (which already discount injured players). Returns
        each starting slot, who fills it, and why, plus injured bench players who
        could move to IR. This is what the lineup SHOULD be; for the lineup
        currently set in Sleeper use get_my_roster. Players in IR slots are not
        considered. Defaults to the current week."""
        target_week = week or ctx.current_week
        lineup = my_lineup(ctx)
        if lineup is None:
            return "Could not find your roster in this league."
        # Sleeper won't start a player sitting in an IR slot.
        player_ids = [pid for pid in my_roster_ids() if pid not in lineup.ir]

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
        started = {s.player_id for s in slots if s.player_id}
        lines = []
        for s in slots:
            status = storage.get_injury_status(ctx.conn, s.player_id) if s.player_id else None
            lines.append(f"{s.slot}: {s.name}{_status_tag(status)} ({s.mean:.1f} pts) - {s.why}")
        lines.append(f"Total projected: {sum(s.mean for s in slots):.1f}")
        for pid in player_ids:
            status = storage.get_injury_status(ctx.conn, pid)
            if pid not in started and _ir_hint(status):
                lines.append(f"Bench: {storage.player_name(ctx.conn, pid)} [{status}]{_ir_hint(status)}")
        for pid in lineup.ir:
            status = storage.get_injury_status(ctx.conn, pid)
            if status not in IR_SLOT_DEFINITE | IR_SLOT_MAYBE:
                lines.append(f"IR slot: {storage.player_name(ctx.conn, pid)}{_status_tag(status)} no longer has "
                             "an IR-eligible designation - move him out of IR to be able to start him.")
        return "\n".join(lines)

    @tool
    def get_waiver_targets(position: str = "", limit_per_position: int = 5, horizon: str = "week") -> str:
        """Best available (not on ANY roster in my league) players, grouped by
        position, ranked by value over replacement (VOR), with a suggested FAAB
        bid percentage. `horizon`:
        - 'week' (default): this week's pickups. Players whose game has ALREADY
          KICKED OFF this week, or whose team is on bye, are excluded - they
          can't score for me this week.
        - 'season': rest-of-season value - use for stashes and long-term adds.
          Not filtered by this week's games.
        Ask for both when the user wants 'pickups' without saying which. For
        injured stash targets, use get_injured_stash_candidates. Optionally
        filter to one position."""
        use_season = horizon.lower().startswith("season")
        table = ctx.season_projections() if use_season else ctx.projections(ctx.current_week)

        excluded: dict[str, str] = {}
        note = ""
        if not use_season:
            kickoffs = ctx.kickoffs(ctx.current_week)
            if kickoffs:
                excluded = unavailable_this_week(table, kickoffs)
            else:
                note = "(NFL schedule unavailable - could not exclude players whose game already started.)\n"

        grouped = suggest_waivers_by_position(
            ctx.conn, ctx.league_id, ctx.roster_positions(), table, limit_per_position, set(excluded),
            weeks=weeks_remaining(ctx.current_week) if use_season else 1,
        )
        if position:
            grouped = {k: v for k, v in grouped.items() if k.upper() == position.upper()}
        if not grouped:
            return note + "No waiver targets found."

        label = f"rest of season (weeks {ctx.current_week}+)" if use_season else f"week {ctx.current_week}"
        lines = [f"{note}Horizon: {label}."]
        if excluded:
            played = sum(1 for s in excluded.values() if s == schedule.PLAYED)
            lines.append(f"Excluded {played} player(s) whose game already started and "
                         f"{len(excluded) - played} on bye this week.")
        for group, suggestions in grouped.items():
            baseline = suggestions[0].proj.mean - suggestions[0].vor
            lines.append(f"{group} (replacement level {baseline:.1f} pts):")
            for s in suggestions:
                lines.append(f"  {_fmt_player(s.proj)}, VOR {s.vor:+.1f}, suggested bid {s.bid_pct}% of FAAB")
        return "\n".join(lines)

    @tool
    def get_injured_stash_candidates(position: str = "", limit: int = 6) -> str:
        """Injured players NOT on any roster in my league (IR, PUP, Out, Doubtful,
        suspended...) ranked by what they're projected to score over the rest of
        the season IF HEALTHY, each with their latest news. Use for 'who can I
        stash', 'injured player worth picking up'. The healthy number is an upper
        bound: weigh it against the return timeline in the news, and say so."""
        rostered = get_rostered_player_ids(ctx.conn, ctx.league_id)
        candidates = [
            p for p in ctx.season_projections(healthy=True)
            if p.injury_status in STASH_DESIGNATIONS and p.player_id not in rostered and p.mean > 0
            and (not position or _matches_position(p, position))
        ][: max(1, min(limit, 10))]
        if not candidates:
            return "No injured, unrostered players with a meaningful healthy projection."

        chunks = []
        for p in candidates:
            try:
                items = fetch_player_news(p.player_id, limit=1)
                if items:
                    storage.save_player_news(ctx.conn, items)
            except Exception:
                pass  # fall back to whatever is cached
            news = storage.get_player_news(ctx.conn, p.player_id, limit=1)
            lines = [f"{p.name} ({p.position}/{p.team or 'FA'}) [{p.injury_status}]: "
                     f"{p.mean:.1f} pts rest-of-season if healthy"]
            if news:
                lines.append(f"  Latest: {news[0]['title']}")
                if news[0]["analysis"]:
                    lines.append(f"  Analysis: {news[0]['analysis']}")
            else:
                lines.append("  No recent news found - return timeline unknown.")
            chunks.append("\n".join(lines))
        return "\n".join(chunks)

    @tool
    def evaluate_proposed_trade(give: str, receive: str, horizon: str = "season") -> str:
        """Score a trade by projected value change. `give` and `receive` are
        comma-separated player names, e.g. give='Saquon Barkley', receive='Josh Allen'.
        Use full names; if a name matches several players the tool lists them -
        ask the user and retry with the exact 'Name (POS/TEAM)' shown.
        `horizon` is 'season' (rest-of-season value, the default and usually what
        a trade should be judged on) or 'week' (this week's projection only)."""
        def resolve_all(names: str) -> tuple[list[str], list[str]]:
            ids, problems = [], []
            for name in [n.strip() for n in names.split(",") if n.strip()]:
                pid, problem = resolve(name)
                if pid:
                    ids.append(pid)
                else:
                    problems.append(problem)
            return ids, problems

        give_ids, give_problems = resolve_all(give)
        recv_ids, recv_problems = resolve_all(receive)
        if give_problems or recv_problems:
            return "\n".join(give_problems + recv_problems)

        use_season = horizon.lower() != "week"
        table = ctx.season_projections() if use_season else ctx.projections(ctx.current_week)
        label = f"rest of season (weeks {ctx.current_week}+)" if use_season else f"week {ctx.current_week}"
        result = evaluate_trade(give_ids, recv_ids, {p.player_id: p for p in table})
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
        """Latest news for ANY NFL player - on my roster, another team's, or a
        free agent - fetched live: a factual description plus a fantasy analysis.
        Use when a question is about a specific player's situation, role, or
        injury; it carries context projections cannot (timelines, depth-chart
        chatter). Use the full name; ambiguous names return a candidate list."""
        pid, problem = resolve(player_name)
        if not pid:
            return problem
        try:
            items = fetch_player_news(pid, limit=min(limit, 5))
            if items:
                storage.save_player_news(ctx.conn, items)
        except Exception:
            pass  # fall through to whatever is cached
        rows = storage.get_player_news(ctx.conn, pid, limit=min(limit, 5))
        status = storage.get_injury_status(ctx.conn, pid)
        header = f"News for {storage.player_name(ctx.conn, pid)}{_status_tag(status)}:"
        if not rows:
            return f"No news found for {storage.player_name(ctx.conn, pid)}{_status_tag(status)}."
        return _format_news(rows, header)

    @tool
    def get_roster_news(items_per_player: int = 2) -> str:
        """Recent news across every player on MY roster, from the cache refreshed
        at each sync (data older than a couple of hours is re-synced before you
        are called). Use for open-ended 'anything I should know this week'
        questions. For any other player, use get_player_news."""
        player_ids = my_roster_ids()
        if player_ids is None:
            return "Could not find your roster in this league."
        capped = max(1, min(items_per_player, 3))
        chunks = []
        for pid in player_ids:
            rows = storage.get_player_news(ctx.conn, pid, limit=capped)
            if rows:
                status = storage.get_injury_status(ctx.conn, pid)
                chunks.append(_format_news(rows, f"{storage.player_name(ctx.conn, pid)}{_status_tag(status)}:"))
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
        rostered = get_rostered_player_ids(ctx.conn, ctx.league_id)
        lines = []
        for entry in trending:
            pid = entry["player_id"]
            status = "already rostered in this league" if pid in rostered else "available"
            injury = _status_tag(storage.get_injury_status(ctx.conn, pid))
            lines.append(f"{storage.player_name(ctx.conn, pid)}{injury}: {entry['count']:,} leagues ({status})")
        return "\n".join(lines)

    @tool
    def get_week_results(week: int = 0) -> str:
        """Recap of a week that has been played: my per-player points and total,
        my opponent's name, total and top scorers, the result (win/loss/tie), the
        best lineup possible in hindsight and points left on my bench. Defaults to
        the LAST COMPLETED week (current week - 1) - use for 'how did I do',
        'did I win', 'recap last week'. Pass the current week explicitly for an
        in-progress live score."""
        target_week = week or last_completed_week(ctx.current_week)
        roster_id = storage.get_roster_id_for_user(ctx.conn, ctx.league_id, ctx.user_id)
        if roster_id is None:
            return "Could not find your roster in this league."
        result = summarize_week(ctx.conn, ctx.league_id, roster_id, target_week, ctx.roster_positions())
        if not result:
            return f"No scored results stored for week {target_week} yet."

        started = "\n".join(f"  {name}: {pts:.1f}" for name, pts in result.started)
        bench = "\n".join(f"  {name}: {pts:.1f}" for name, pts in result.bench)
        if result.opponent_name:
            top = ", ".join(f"{name} {pts:.1f}" for name, pts in result.opponent_top)
            matchup = (
                f"Result: {result.outcome.upper()} {result.actual_total:.1f} - {result.opponent_total:.1f} "
                f"vs. {result.opponent_name} (margin {result.actual_total - result.opponent_total:+.1f})\n"
                f"Opponent's top scorers: {top or 'n/a'}\n"
            )
        else:
            matchup = "No opponent that week (bye).\n"
        in_progress = " (in progress - scores may still change)" if target_week >= ctx.current_week else ""
        return (
            f"Week {result.week} results{in_progress}.\n{matchup}Started:\n{started}\n"
            f"Actual total: {result.actual_total:.1f}\n"
            f"Best possible with hindsight: {result.optimal_total:.1f}\n"
            f"Points left on bench: {result.points_left_on_bench:.1f}\n"
            f"Bench:\n{bench}"
        )

    @tool
    def sync_league() -> str:
        """Full refresh of this league from Sleeper: rosters, every week's
        matchups and scores, transactions, injury designations (when the local
        copy is more than a few hours old) and my roster's news. Data is already
        auto-refreshed when it's over ~2 hours old, so call this only when the
        user says something just happened or asks to refresh."""
        try:
            run_sync(ctx.conn, league_query=ctx.league_id, log=log_to_stderr)
        except SleeperError as e:
            return f"Sync failed: {e}"
        ctx.reload_state()
        return f"Refreshed {ctx.league_name()} from Sleeper. Season {ctx.season}, week {ctx.current_week}."

    @tool
    def switch_league(league_name: str) -> str:
        """Point every tool at a different one of the user's Sleeper leagues
        (matched by name, loosely), syncing it first. Use when the user asks
        about a league other than the current one. If the name doesn't identify
        exactly one league, the reply lists them - ask the user which."""
        try:
            run_sync(ctx.conn, league_query=league_name, log=log_to_stderr)
        except SleeperError as e:
            return f"Could not switch: {e}"
        ctx.reload_state()
        return f"Now using {ctx.league_name()} (your fantasy team: {ctx.fantasy_team_name()}), week {ctx.current_week}."

    @tool
    def remember_preference(note: str) -> str:
        """Save a LASTING preference or fact about how the user plays, so every
        future conversation applies it (e.g. 'Risk-averse at FLEX: prefers a
        high floor', 'Never recommend Dallas players'). One short sentence.
        Not for one-off requests."""
        prefs = storage.add_preference(ctx.conn, note.strip())
        return f"Saved. Remembered preferences: {len(prefs)}."

    @tool
    def forget_preference(number: int) -> str:
        """Delete remembered preference `number` (1-based, as numbered in the
        USER PREFERENCES section of your instructions)."""
        removed = storage.remove_preference(ctx.conn, number)
        return f"Forgot: {removed}" if removed else f"No preference #{number}."

    return [
        remember_preference,
        forget_preference,
        get_league_state,
        get_my_roster,
        get_fantasy_team_roster,
        get_nfl_team_roster,
        get_recent_transactions,
        get_projections,
        get_season_projections,
        get_season_to_date_leaders,
        recommend_lineup,
        get_waiver_targets,
        get_injured_stash_candidates,
        evaluate_proposed_trade,
        get_player_news,
        get_roster_news,
        get_trending_players,
        get_week_results,
        sync_league,
        switch_league,
    ]
