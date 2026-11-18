"""The 17 read-only agent tools (docs/data-contract.md section 11).

Wired now to stored league state: league state, my roster, other fantasy teams' rosters, NFL team rosters
and players (Sleeper catalog and stored news), my roster's news, transactions, switch league.
Decision, projection, leader, results and trending tools call a provider from decisions.PROVIDERS; a tool
whose provider is not there yet says the feature is not available.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

import decisions
from ingestion.leagues import LeagueNotFound, resolve_league, select_league
from ingestion.nfl import service
from ingestion.sleeper import SleeperError
from storage.db import get_json
from . import data, render, resolve
from .render import block, player_line, roster_lines, when

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    conn: sqlite3.Connection
    league_id: str


@dataclass
class ToolResult:
    text: str
    sources: list[dict] = field(default_factory=list)
    ok: bool = True


@dataclass
class Tool:
    name: str
    label: str
    description: str
    parameters: dict
    fn: Callable


def error(text: str) -> ToolResult:
    return ToolResult(f"ERROR: {text}", ok=False)


PRACTICE_NOTE = "practice status is the latest one listed, not a per-day history"
RELEVANT = ("injur", "practi", "out", "questionable", "doubtful", "reserve", " ir", "return", "role", "snap",
            "start", "bench", "inactive", "ruled", "concussion", "surgery", "suspend", "released", "traded")


def rosters_source(conn, league_id) -> dict:
    return {"label": "Sleeper rosters", "as_of": data.league_row(conn, league_id)["fetched_at"]}


def catalog_source(conn) -> dict:
    return {"label": "Sleeper players catalog", "as_of": data.catalog_as_of(conn)}


def _ambiguous(rows: list, lg: data.League, what: str) -> ToolResult:
    lines = [f"AMBIGUOUS: several players fit '{what}'. Ask which one, or say which one you assumed and why:"]
    lines += [f"  {player_line(lg.ref(r))}" for r in rows]
    return ToolResult("\n".join(lines))


def _one_player(ctx: Ctx, lg: data.League, name: str) -> sqlite3.Row | ToolResult:
    rows = resolve.players(ctx.conn, ctx.league_id, name, lg.me)
    if not rows:
        return ToolResult(f"NOT FOUND: no player named '{name}' in the Sleeper catalog. Nothing is known about this name.")
    if len(rows) > 1:
        return _ambiguous(rows, lg, name)
    return rows[0]


def _one_fantasy_team(ctx: Ctx, name: str) -> sqlite3.Row | ToolResult:
    teams = data.fantasy_teams(ctx.conn, ctx.league_id)
    hits = resolve.fantasy_team(teams, name)
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(t["name"] for t in teams)
    if not hits:
        return error(f"no fantasy team named '{name}' in this league. Fantasy teams: {names}")
    return ToolResult(f"AMBIGUOUS: several fantasy teams fit '{name}': {', '.join(t['name'] for t in hits)}. Ask which one.")


# 1-3: league state and fantasy team rosters

def get_league_state(ctx: Ctx) -> ToolResult:
    conn, lg = ctx.conn, data.League(ctx.conn, ctx.league_id)
    league = data.league_row(conn, ctx.league_id)
    fmt = json.loads(league["format"])
    week, last = data.weeks(conn)
    teams = data.fantasy_teams(conn, ctx.league_id)
    me = next(t for t in teams if t["roster_id"] == lg.me)
    opp = data.opponent(conn, ctx.league_id, week, lg.me) if week else None
    opp_text = f"{opp['name']} (fantasy team, record {data.record(opp)})" if opp else "none stored"
    lines = [
        f"LEAGUE: {league['name']}. SEASON: {fmt['season']}. THIS WEEK: {week}. LAST COMPLETED WEEK: {last}",
        f"MY OPPONENT THIS WEEK: {opp_text}",
        "STANDINGS (fantasy teams, by wins then points for):",
    ]
    lead = teams[0]
    for i, t in enumerate(teams, 1):
        mine = " (my fantasy team)" if t["roster_id"] == lg.me else ""
        behind = ((lead["wins"] - t["wins"]) + (t["losses"] - lead["losses"])) / 2
        back = "first place" if t is lead else f"{behind:g} games behind first"
        lines.append(
            f"  {i}. {t['name']}{mine}, record {data.record(t)}, {back}, points for {render.pts(t['points_for'])}"
        )
    lines += render.format_lines(fmt, lg.me, week)
    summary = (
        f"Week {week} of the {fmt['season']} season in {league['name']}; my fantasy team {me['name']} is "
        f"{render.ordinal(teams.index(me) + 1)} of {len(teams)} fantasy teams at {data.record(me)}; opponent this week: {opp_text}."
    )
    sources = [rosters_source(conn, ctx.league_id),
               {"label": "Sleeper matchups", "as_of": data.refresh_ok_at(conn, "sleeper", "matchups")}]
    return ToolResult(block(summary, sources, lines, []), sources)


def _roster_result(ctx: Ctx, roster_id: int, summary: str) -> ToolResult:
    lg = data.League(ctx.conn, ctx.league_id)
    items = lg.roster(roster_id)
    notes, gaps, facts = {}, [], []
    for item in items:
        p = item["player"]
        if p is None or not lg.week:
            continue
        inactive, inactive_gaps = service.inactive_fact(ctx.conn, p["player_id"], lg.week)
        gaps += [g for g in inactive_gaps if g not in gaps]
        if inactive:
            facts.append(inactive)
            word = "INACTIVE" if inactive["value"]["inactive"] else "not on the inactive list"
            notes[p["player_id"]] = f"{word} this week ({render.fact_source(inactive)})"
    lines = roster_lines(lg, roster_id, notes)
    fmt = data.league_format(ctx.conn, ctx.league_id)
    on_ir = [i["player"] for i in items if i["slot"] == "IR"]
    allowed = fmt["ir_allowed_designations"]
    if fmt["ir_slots"]:
        lines.append(f"IR SLOTS: {fmt['ir_slots']} in this league, {len(on_ir)} in use; they allow only these "
                     "designations: " + ", ".join(allowed))
    else:
        lines.append("IR SLOTS: none in this league, so no player can be moved to IR")
    for p in on_ir:
        status = lg.injury_status(p["player_id"])
        if status not in allowed:
            lines.append(f"IR FLAG: {p['full_name']} is in an IR slot with designation "
                         f"{status or 'none'}, which this league's IR slots do not allow")
    if any(i["player"] and lg.ref(i["player"])["game"] is None for i in items):
        gaps.append("game time not available for players whose NFL team has no stored game this week")
    facts += [f for f in lg.injury_facts.values() if f and f["source"] != "sleeper"]  # the catalog is listed already
    sources = [rosters_source(ctx.conn, ctx.league_id), catalog_source(ctx.conn), *render.fact_sources(facts)]
    games = ctx.conn.execute("select max(as_of) from nfl_game").fetchone()[0]
    if games:
        sources.append({"label": "NFL schedule and game states", "as_of": games})
    return ToolResult(block(summary, sources, lines, [*gaps, PRACTICE_NOTE]), sources)


def get_my_roster(ctx: Ctx) -> ToolResult:
    lg = data.League(ctx.conn, ctx.league_id)
    items = lg.roster(lg.me)
    starting = items[: len(lg.slots)]
    empty = sum(i["player"] is None for i in starting)
    counts = {s: sum(i["slot"] == s for i in items) for s in ("BN", "IR", "TAXI")}
    byes = sum(bool(i["player"] and (g := lg.ref(i["player"])["game"]) and g["status"] == "bye") for i in starting)
    week, _ = data.weeks(ctx.conn)
    summary = (
        f"My lineup as set in Sleeper for week {week}: {len(starting) - empty} of {len(starting)} starting slots "
        f"filled ({empty} empty), {byes} starters on a bye, {counts['BN']} on the bench, {counts['IR']} on IR"
        + (f", {counts['TAXI']} on taxi" if counts["TAXI"] else "") + ". This is not a recommendation."
    )
    return _roster_result(ctx, lg.me, summary)


def get_fantasy_team_roster(ctx: Ctx, fantasy_team: str) -> ToolResult:
    team = _one_fantasy_team(ctx, fantasy_team)
    if isinstance(team, ToolResult):
        return team
    teams = data.fantasy_teams(ctx.conn, ctx.league_id)
    mine = team["roster_id"] == data.my_roster_id(ctx.conn, ctx.league_id)
    summary = (
        f"{team['name']} (fantasy team{', mine' if mine else ''}), record {data.record(team)}, "
        f"{render.ordinal(teams.index(team) + 1)} of {len(teams)} fantasy teams: lineup as set in Sleeper."
    )
    return _roster_result(ctx, team["roster_id"], summary)


# 4-6: NFL facts (Sleeper catalog and stored news until stage 2.2)

def get_nfl_team_roster(ctx: Ctx, nfl_team: str, position: str | None = None) -> ToolResult:
    abbrs = resolve.nfl_team(nfl_team)
    if not abbrs:
        return error(f"no NFL team matches '{nfl_team}'")
    if len(abbrs) > 1:
        return ToolResult(f"AMBIGUOUS: several NFL teams fit '{nfl_team}': {', '.join(abbrs)}. Ask which one.")
    abbr = abbrs[0]
    roster = service.team_roster(ctx.conn, abbr, position)
    lg = data.League(ctx.conn, ctx.league_id)
    lines, current, bases = [], None, set()
    for x in roster["players"]:
        if x["position"] != current:
            current = x["position"]
            lines.append(f"{current}:")
        ref = lg.ref(data.player(ctx.conn, x["player_id"])) | {"nfl_team": abbr}
        parts = [player_line(ref)]
        if x["depth"]:
            parts.append(f"depth {x['depth']['value']['slot_label']}")
            bases.add(x["depth"]["source_label"])
        else:
            parts.append("depth: not listed")
        if x["injury"]:
            parts.append(render.injury_text(x["injury"]))
        parts += [f"usage {render.usage_text(u)}" for u in x["usage"]]
        lines.append("  " + ", ".join(parts))
    city, nick = resolve.NFL_TEAMS[abbr]
    what = f"{position.upper()} players" if position else "players at fantasy positions"
    summary = f"{abbr} ({city} {nick}) NFL team: {len(roster['players'])} {what}, in depth order."
    warnings = [*roster["set"]["gaps"], PRACTICE_NOTE]
    if bases:
        warnings.append("depth order comes from " + " and ".join(sorted(bases)))
    sources = render.fact_sources(roster["set"]["facts"])
    return ToolResult(block(summary, sources, lines, warnings), sources)


def _news_line(f: dict) -> str:
    v = f["value"]
    text = " ".join(x for x in (v["title"], v["description"], v["analysis"]) if x)
    return f"NEWS ({render.fact_source(f)}, quoted as published): {text}"


def get_player(ctx: Ctx, name: str) -> ToolResult:
    lg = data.League(ctx.conn, ctx.league_id)
    p = _one_player(ctx, lg, name)
    if isinstance(p, ToolResult):
        return p
    f = service.player_facts(ctx.conn, p["player_id"])
    roster = f["roster"]
    nfl_team = roster["nfl_team"] if roster else None
    ref = lg.ref(p) | {"nfl_team": nfl_team, "game": f["game"]}
    if roster:
        city, nick = resolve.NFL_TEAMS[nfl_team]
        team_line = f"NFL TEAM: {nfl_team}, the {city} {nick} ({render.fact_source(roster)})"
        team_line += "".join(f"; {a['source']} still lists {a['value']} ({when(a['as_of'])})" for a in roster["also"])
    else:
        team_line = "NFL TEAM: none (no source lists him on an NFL team)"
    lines = [player_line(ref), team_line]
    if f["game"] is None and nfl_team:
        lines.append(f"game this week: none stored for {nfl_team}")
    lines.append(f"depth: {f['depth']['value']['slot_label']} ({render.fact_source(f['depth'])})" if f["depth"]
                 else "depth: not listed")
    lines.append(render.injury_text(f["injury"]))
    injury = f["injury"]["value"] if f["injury"] else {}
    if injury.get("return_date"):
        source = service.LABEL[(injury["return_date_source"], "injury")]
        lines.append(f"RETURN: expected around {injury['return_date']} (source {source}, as of "
                     f"{when(injury['return_date_as_of'])}; a news estimate, not an official report)")
    elif injury.get("designation") == "IR":
        lines.append("RETURN: unknown (no source has a return date)")
    if f["inactive"]:
        word = "INACTIVE" if f["inactive"]["value"]["inactive"] else "not on the inactive list"
        lines.append(f"game day: {word} ({render.fact_source(f['inactive'])})")
    lines += [f"usage {render.usage_text(u)}" for u in f["usage"]] or ["usage: none stored"]
    lines += [_news_line(n) for n in f["news"]] or ["NEWS: none stored for him"]
    summary = f"{ref['name']}: {ref['position']}, NFL team {nfl_team or 'none'}, {render.status_label(ref)}."
    sources = [catalog_source(ctx.conn), *render.fact_sources(f["set"]["facts"])]
    return ToolResult(block(summary, sources, lines, f["set"]["gaps"]), sources)


def get_my_roster_news(ctx: Ctx, days: int = 7) -> ToolResult:
    lg = data.League(ctx.conn, ctx.league_id)
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    groups, warnings = [], []
    for item in lg.roster(lg.me):
        p = item["player"]
        if p is None:
            continue
        facts, gaps = service.news_facts(ctx.conn, p["player_id"])
        warnings += [g for g in gaps if g not in warnings]
        news = [f for f in facts if f["as_of"] >= since]
        if news:
            relevant = any(s in " ".join(filter(None, (n["value"]["title"], n["value"]["description"]))).lower()
                           for n in news for s in RELEVANT)
            groups.append((relevant, p, news))
    groups.sort(key=lambda g: not g[0])
    lines, all_news = [], []
    for relevant, p, news in groups:
        mark = "DECISION-RELEVANT (injury, practice or role) " if relevant else ""
        lines.append(f"{mark}{player_line(lg.ref(p))}:")
        lines += [f"  {_news_line(n)}" for n in news]
        all_news += news
    n_rel = sum(g[0] for g in groups)
    summary = (
        f"News from the last {days} days for {len(groups)} players on my roster, {n_rel} with injury, practice or "
        "role news." if groups else f"No news for players on my roster in the last {days} days."
    )
    sources = [rosters_source(ctx.conn, ctx.league_id), *render.fact_sources(all_news)]
    return ToolResult(block(summary, sources, lines, warnings), sources)


# 7: transactions

TX_TYPES = {"free_agent": "free-agent move", "waiver": "waiver claim", "trade": "trade", "commissioner": "commissioner move"}


def get_transactions(ctx: Ctx, fantasy_team: str | None = None, days: int = 7, type: str | None = None) -> ToolResult:
    lg = data.League(ctx.conn, ctx.league_id)
    roster_filter = None
    if fantasy_team:
        team = _one_fantasy_team(ctx, fantasy_team)
        if isinstance(team, ToolResult):
            return team
        roster_filter = team["roster_id"]
    since_ms = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)
    rows = ctx.conn.execute(
        "select * from transaction_log where league_id = ? and status = 'complete' and created_ms >= ? "
        "order by created_ms desc",
        (ctx.league_id, since_ms),
    ).fetchall()

    def names(ids):
        return ", ".join((data.player(ctx.conn, i) or {"full_name": i})["full_name"] for i in ids)

    lines = []
    for t in rows:
        adds, drops = json.loads(t["adds"]) or {}, json.loads(t["drops"]) or {}
        rosters = json.loads(t["roster_ids"]) or []
        if roster_filter is not None and roster_filter not in rosters:
            continue
        if type in ("trade", "waiver") and t["type"] != type:
            continue
        if (type == "add" and not adds) or (type == "drop" and not drops):
            continue
        parts = []
        for rid in rosters:
            got = [p for p, r in adds.items() if r == rid]
            lost = [p for p, r in drops.items() if r == rid]
            side = f"{lg.names[rid]} (fantasy team)"
            if got:
                side += f" added {names(got)}"
            if lost:
                side += f"{',' if got else ''} dropped {names(lost)}"
            parts.append(side)
        when_ = datetime.fromtimestamp(t["created_ms"] / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"{when(when_)}, {TX_TYPES.get(t['type'], t['type'])}: " + "; ".join(parts))
    summary = f"{len(lines)} completed transactions in the last {days} days" + (
        f" involving {fantasy_team}" if fantasy_team else "") + "."
    warnings = ["pending waiver claims and open trade offers: not available in this version (they need a Sleeper login)",
                "not available: FAAB bid amounts (not stored)"]
    sources = [{"label": "Sleeper transactions", "as_of": data.refresh_ok_at(ctx.conn, "sleeper", "transactions")}]
    return ToolResult(block(summary, sources, lines, warnings), sources)


# 17: switch league

def switch_league(ctx: Ctx, league_name: str) -> ToolResult:
    leagues = get_json(ctx.conn, "leagues", [])
    try:
        league = resolve_league(leagues, league_name=league_name)
    except LeagueNotFound as e:
        return error(f"no league named '{league_name}'. My leagues: {', '.join(e.available)}")
    if not league["selectable"]:
        return error(f"{league['name']} cannot be used: {league['not_selectable_reason'].replace('_', ' ')}")
    try:
        select_league(ctx.conn, league["league_id"])
    except SleeperError:
        return error(f"could not load {league['name']} from Sleeper; still in the previous league")
    ctx.league_id = league["league_id"]
    return ToolResult(f"SWITCHED: now in {league['name']}. The CONTEXT on the next turn is for this league.")


# 8-16: decisions, projections, leaders, results, trending (src/decisions, docs/data-contract.md section 9)

PROVIDERS: dict[str, Callable] = decisions.PROVIDERS  # tool name -> (conn, league_id, **inputs) -> result dict
WHAT = {
    "get_projections": "projections are", "get_season_leaders": "season leaders are",
    "recommend_lineup": "lineup recommendations are", "get_waiver_targets": "waiver targets are",
    "get_injured_stash": "injured stash rankings are", "evaluate_trade": "trade evaluation is",
    "find_trade_ideas": "trade ideas are", "get_trending_players": "Sleeper trends are",
    "get_week_results": "week results are",
}
RENDER = {
    "get_projections": render.projections, "get_season_leaders": render.leaders, "recommend_lineup": render.lineup,
    "get_waiver_targets": render.waivers, "get_injured_stash": render.stash, "evaluate_trade": render.trade_evaluation,
    "find_trade_ideas": render.trade_ideas, "get_trending_players": render.trending,
    "get_week_results": render.week_results,
}


def _provided(name: str, ctx: Ctx, **inputs) -> ToolResult:
    provider = PROVIDERS.get(name)
    if provider is None:
        return error(f"{WHAT[name]} not available in this version yet; nothing was computed")
    try:
        result = provider(ctx.conn, ctx.league_id, **inputs)
    except decisions.NoData as e:  # the stored data cannot answer: say why, plainly
        return error(str(e))
    return ToolResult(RENDER[name](result), render.sources_of(result))


def _player_ids(ctx: Ctx, names: list[str]) -> list[str] | ToolResult:
    lg = data.League(ctx.conn, ctx.league_id)
    ids = []
    for n in names:
        p = _one_player(ctx, lg, n)
        if isinstance(p, ToolResult):
            return p
        ids.append(p["player_id"])
    return ids


def get_projections(ctx: Ctx, players: list[str] | None = None, position: str | None = None,
                    horizon: str = "week", week: int | None = None, limit: int = 10) -> ToolResult:
    if not players and not position:
        return ToolResult("NEED: which players, or which position?")
    ids = _player_ids(ctx, players) if players else None
    if isinstance(ids, ToolResult):
        return ids
    return _provided("get_projections", ctx, player_ids=ids, position=position, horizon=horizon, week=week,
                     limit=limit)


def get_season_leaders(ctx: Ctx, position: str | None = None, through_week: int | None = None,
                       limit: int = 10) -> ToolResult:
    return _provided("get_season_leaders", ctx, position=position, through_week=through_week, limit=limit)


def recommend_lineup(ctx: Ctx, week: int | None = None) -> ToolResult:
    return _provided("recommend_lineup", ctx, week=week)


def get_waiver_targets(ctx: Ctx, horizon: str = "both", position: str | None = None, limit: int = 10) -> ToolResult:
    return _provided("get_waiver_targets", ctx, horizon=horizon, position=position, limit=limit)


def get_injured_stash(ctx: Ctx, position: str | None = None, limit: int = 10) -> ToolResult:
    return _provided("get_injured_stash", ctx, position=position, limit=limit)


def evaluate_trade(ctx: Ctx, give: list[str], get: list[str], fantasy_team: str | None = None,
                   horizon: str = "ros") -> ToolResult:
    give_ids, get_ids = _player_ids(ctx, give), _player_ids(ctx, get)
    for ids in (give_ids, get_ids):
        if isinstance(ids, ToolResult):
            return ids
    lg = data.League(ctx.conn, ctx.league_id)
    not_mine = [n for n, i in zip(give, give_ids, strict=True) if lg.held.get(i) is None or lg.held[i]["roster_id"] != lg.me]
    if not_mine:
        return error(f"not on my roster: {', '.join(not_mine)}")
    owners = {lg.held[i]["roster_id"] for i in get_ids if i in lg.held}
    if fantasy_team:
        team = _one_fantasy_team(ctx, fantasy_team)
        if isinstance(team, ToolResult):
            return team
        other = team["roster_id"]
    elif len(owners) == 1 and len([i for i in get_ids if i in lg.held]) == len(get_ids):
        other = owners.pop()
    else:
        return ToolResult("NEED: which fantasy team is the trade with? The players asked for are not all on one.")
    off = [n for n, i in zip(get, get_ids, strict=True) if lg.held.get(i) is None or lg.held[i]["roster_id"] != other]
    if off or other == lg.me:
        return error(f"not on {lg.names[other]}'s roster: {', '.join(off) or 'those players'}")
    return _provided("evaluate_trade", ctx, give=give_ids, get=get_ids, with_fantasy_team_id=other, horizon=horizon)


def find_trade_ideas(ctx: Ctx, target_player: str | None = None, need_position: str | None = None,
                     fantasy_team: str | None = None, my_tradeable: list[str] | None = None,
                     horizon: str = "ros", limit: int = 5) -> ToolResult:
    if not target_player and not need_position:
        return ToolResult("NEED: a target player or a position of need.")
    if not fantasy_team and not my_tradeable:
        return ToolResult("NEED: which of your players are you willing to trade? No search was run.")
    lg = data.League(ctx.conn, ctx.league_id)
    target_id = None
    if target_player:
        p = _one_player(ctx, lg, target_player)
        if isinstance(p, ToolResult):
            return p
        if (held := lg.held.get(p["player_id"])) and held["roster_id"] == lg.me:
            return error(f"{p['full_name']} is on my roster, so he cannot be the target. To offer him, pass him in "
                         "my_tradeable; no search was run")
        target_id = p["player_id"]
    team_id = None
    if fantasy_team:
        team = _one_fantasy_team(ctx, fantasy_team)
        if isinstance(team, ToolResult):
            return team
        team_id = team["roster_id"]
    tradeable = _player_ids(ctx, my_tradeable) if my_tradeable else None
    if isinstance(tradeable, ToolResult):
        return tradeable
    return _provided("find_trade_ideas", ctx, target_player_id=target_id, need_position=need_position,
                     with_fantasy_team_id=team_id, my_tradeable=tradeable, horizon=horizon, limit=limit)


def get_trending_players(ctx: Ctx, type: str = "add", hours: int = 24, limit: int = 10) -> ToolResult:
    return _provided("get_trending_players", ctx, type=type, hours=hours, limit=limit)


def get_week_results(ctx: Ctx, week: int | None = None) -> ToolResult:
    return _provided("get_week_results", ctx, week=week)


# catalog

def _p(type_: str, description: str, **extra) -> dict:
    return {"type": type_, "description": description, **extra}


NAMES = {"type": "array", "items": {"type": "string"}}
HORIZON = _p("string", "rest of season (default) or this week", enum=["ros", "week"])
LIMIT = _p("integer", "how many to return")
POSITION = _p("string", "fantasy position, e.g. RB, WR, LB")

TOOLS = [
    Tool("get_league_state", "Reading league standings",
         "Season, this week, last completed week, my opponent this week, standings of every fantasy team, league "
         "format and waiver budget. Not for rosters (get_my_roster, get_fantasy_team_roster) or scores of a past "
         "week (get_week_results).", {}, get_league_state),
    Tool("get_my_roster", "Reading your roster",
         "My fantasy team's roster and lineup exactly as set in Sleeper: starters by slot, bench, IR, with each "
         "player's NFL team and injury designation. Use it for 'who are my starters'. Not a recommendation: use "
         "recommend_lineup. Not for news: use get_my_roster_news.", {}, get_my_roster),
    Tool("get_fantasy_team_roster", "Reading a fantasy team's roster",
         "Another manager's fantasy team in this league: lineup as set in Sleeper, bench, IR, record. Not for NFL "
         "teams: use get_nfl_team_roster. Not for my own: use get_my_roster.",
         {"fantasy_team": _p("string", "fantasy team name or manager name, as the user said it")},
         get_fantasy_team_roster),
    Tool("get_nfl_team_roster", "Reading an NFL team's roster",
         "Current players of a real NFL team by position in depth order, with injury designations and each "
         "player's fantasy status in my league. Not for fantasy teams: use get_fantasy_team_roster. For one player "
         "in detail use get_player.",
         {"nfl_team": _p("string", "NFL team as the user said it: SEA, Seattle or Seahawks"), "position": POSITION},
         get_nfl_team_roster),
    Tool("get_player", "Looking up a player",
         "One NFL player: current NFL team, position, depth, injury designation and practice status, fantasy status "
         "in my league (my roster, another fantasy team, or free agent), latest news. Use it for any question "
         "about a player's NFL team or news. Pass the name as the user gave it, even a surname alone: when several "
         "players fit, it returns the candidates. Not for lists (get_nfl_team_roster, get_projections, "
         "get_season_leaders) or value (get_projections).",
         {"name": _p("string", "player name as the user said it")}, get_player),
    Tool("get_my_roster_news", "Reading news for your roster",
         "Recent news for every player on my roster, injury, practice and role news first. Not for a player off "
         "my roster: use get_player.", {"days": _p("integer", "how many days back, default 7")}, get_my_roster_news),
    Tool("get_transactions", "Reading league transactions",
         "Completed adds, drops, waiver claims and trades in this league. Pending claims and open trade offers are "
         "not available in this version.",
         {"fantasy_team": _p("string", "only this fantasy team"), "days": _p("integer", "how many days back, default 7"),
          "type": _p("string", "only this kind", enum=["add", "drop", "trade", "waiver"])}, get_transactions),
    Tool("get_projections", "Reading projections",
         "Projected points in my league's scoring for players or a position: this week, rest of season or full "
         "season, with the likely range and sources, plus scoring rules no projection covers. Not for actual "
         "points so far (get_season_leaders) or pickups (get_waiver_targets).",
         {"players": {**NAMES, "description": "player names"}, "position": POSITION,
          "horizon": _p("string", "week (default), ros or full_season", enum=["week", "ros", "full_season"]),
          "week": _p("integer", "NFL week, default this week"), "limit": LIMIT}, get_projections),
    Tool("get_season_leaders", "Ranking season leaders",
         "Actual points scored so far this season in my league's scoring, games and points per game. Not "
         "projections: use get_projections.",
         {"position": POSITION, "through_week": _p("integer", "default the last completed week"), "limit": LIMIT},
         get_season_leaders),
    Tool("recommend_lineup", "Optimizing your lineup",
         "Recommended lineup for my fantasy team: my lineup as set in Sleeper, the expected-points lineup and a "
         "high-risk lineup, each with projected totals, spread and win probability against my opponent, and the "
         "changes with reasons. Not the lineup set in Sleeper: use get_my_roster.",
         {"week": _p("integer", "NFL week, default this week")}, recommend_lineup),
    Tool("get_waiver_targets", "Ranking waiver targets",
         "Free agents to pick up, ranked by value over replacement for this week and for the rest of the season, "
         "with a FAAB guide. Not for injured players to hold for later: use get_injured_stash.",
         {"horizon": _p("string", "both (default), week or ros", enum=["both", "week", "ros"]), "position": POSITION,
          "limit": LIMIT}, get_waiver_targets),
    Tool("get_injured_stash", "Ranking injured stash options",
         "Injured free agents (listed IR, Out or PUP) worth holding, ranked by their healthy rest-of-season value, "
         "with return news. Not for healthy pickups: use get_waiver_targets.", {"position": POSITION, "limit": LIMIT}, get_injured_stash),
    Tool("evaluate_trade", "Evaluating the trade",
         "Evaluate a trade the manager describes: both fantasy teams' starting lineup value before and after, roster "
         "spots, verdict, trade deadline. Not for finding trades: use find_trade_ideas.",
         {"give": {**NAMES, "description": "my players I would give"},
          "get": {**NAMES, "description": "players I would get"},
          "fantasy_team": _p("string", "the other fantasy team, if known"), "horizon": HORIZON}, evaluate_trade),
    Tool("find_trade_ideas", "Searching for trade ideas",
         "Find one-for-one trades for a target player or a position of need that help both fantasy teams. If no "
         "fantasy team is named, it needs the players the manager is willing to trade (ask first). Not for "
         "evaluating a given offer: use evaluate_trade.",
         {"target_player": _p("string", "a player on another fantasy team that the manager wants"),
          "need_position": POSITION,
          "fantasy_team": _p("string", "search only this fantasy team"),
          "my_tradeable": {**NAMES, "description": "my players I am willing to trade, e.g. X in 'what could I get "
                                                   "for X'"}, "horizon": HORIZON,
          "limit": LIMIT}, find_trade_ideas),
    Tool("get_trending_players", "Reading Sleeper trends",
         "Players most added or dropped across all Sleeper leagues recently, as context only. Not a projection or "
         "a value: use get_projections or get_waiver_targets for value.",
         {"type": _p("string", "add (default) or drop", enum=["add", "drop"]),
          "hours": _p("integer", "look-back, default 24"), "limit": LIMIT}, get_trending_players),
    Tool("get_week_results", "Reading week results",
         "Result of a completed week: both scores, win or loss, opponent's top scorers, my best possible lineup "
         "and points left on my bench. Not for the upcoming matchup: use recommend_lineup or get_league_state.",
         {"week": _p("integer", "NFL week, default the last completed week")}, get_week_results),
    Tool("switch_league", "Switching league",
         "Switch to another of my Sleeper leagues by name.",
         {"league_name": _p("string", "league name as the user said it")}, switch_league),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
REQUIRED = {"get_fantasy_team_roster": ["fantasy_team"], "get_nfl_team_roster": ["nfl_team"], "get_player": ["name"],
            "evaluate_trade": ["give", "get"], "switch_league": ["league_name"]}


def specs() -> list[dict]:
    """The tool list in OpenAI function-calling format."""
    return [
        {"type": "function", "function": {
            "name": t.name, "description": t.description,
            "parameters": {"type": "object", "properties": t.parameters, "required": REQUIRED.get(t.name, [])}}}
        for t in TOOLS
    ]


def run_tool(ctx: Ctx, name: str, arguments: dict) -> ToolResult:
    """Run one tool call. A failure comes back as ERROR text, never as an empty success."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return error(f"there is no tool named {name}")
    try:
        return tool.fn(ctx, **arguments)
    except Exception as e:  # report every tool failure to the model as a failure (PRD 4.5)
        log.exception("tool %s failed", name)
        return error(f"{tool.label.lower()} failed ({type(e).__name__}); nothing was returned")
