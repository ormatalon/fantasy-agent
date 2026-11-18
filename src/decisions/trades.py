"""Trade evaluation and trade ideas (docs/data-contract.md sections 9.4 and 9.5).

A roster's value over a horizon is the sum, over its weeks, of the best starting lineup's projected points.
"""

import sqlite3
from functools import cache

from decisions import artifact_view
from decisions.assign import best_total
from decisions.league import NoData, View, r1, reason, why
from decisions.value import slot_demand

MIN_IDEA_GAIN = 0.1  # a trade idea must raise both starting lineups by at least this
TARGETS_PER_POSITION = 3  # when no player or position is named, each fantasy team's best at every position
FREE_AGENTS_PER_POSITION = 3  # the best of these fills a roster spot


class Market:
    """Roster values for one horizon; one instance per request so repeated lineups are computed once."""

    def __init__(self, v: View, horizon: str):
        self.v, self.horizon, self.weeks = v, horizon, v.weeks(horizon)
        self.positions = {}
        self.value = cache(self._value)

    def _value(self, players: frozenset) -> float:
        total = 0.0
        for w in self.weeks:
            pos = {p: self.pos(p) for p in players if self.pos(p)}
            total += best_total(self.v.slots, pos, {p: self.v.mean(p, w) for p in pos})
        return total

    def pos(self, pid: str) -> str | None:
        if pid not in self.positions:
            self.positions[pid] = self.v.position(pid)
        return self.positions[pid]

    def mean(self, pid: str) -> float:
        return self.v.horizon_mean(pid, self.horizon)


def _best_free_agents(m: Market, pool: set, count: int) -> list[str]:
    """Add free agents one at a time, each the one that raises the lineup value most."""
    demand = slot_demand(m.v.slots)
    by_pos: dict[str, list[str]] = {}
    for pid in sorted(m.v.free_agents(), key=lambda p: -m.mean(p)):
        pos = m.pos(pid)
        if pos in demand and m.v.ros_row(pid) and len(by_pos.setdefault(pos, [])) < FREE_AGENTS_PER_POSITION:
            by_pos[pos].append(pid)
    chosen = []
    for _ in range(count):
        options = [p for ps in by_pos.values() for p in ps if p not in chosen]
        if not options:
            break
        chosen.append(max(options, key=lambda p: (m.value(frozenset(pool | {*chosen, p})), m.mean(p))))
    return chosen


def _side(m: Market, roster_id: int, gives: list[str], receives: list[str]) -> dict:
    """One fantasy team's side: roster spots settled, then the starting lineup value before and after."""
    v = m.v
    r = v.rosters[roster_id]
    active = v.active(roster_id)  # IR and taxi players do not count against the roster limit
    before = m.value(frozenset(active))
    gone = [p for p in gives if p in active]
    pool = {*(p for p in active if p not in gives), *receives}
    count_after = len(active) - len(gone) + len(receives)
    adjustments = []
    if count_after > v.fmt["roster_size"]:  # the side receiving more drops its lowest-value bench players
        bench = sorted((p for p in r["bench"] if p not in gives), key=m.mean)
        for pid in bench[: count_after - v.fmt["roster_size"]]:
            pool.discard(pid)
            adjustments.append({"action": "drop", "player": v.ref(pid), "reason": "roster_spot"})
    elif len(gone) > len(receives) and count_after < v.fmt["roster_size"]:  # an open spot goes to the best free agent
        spots = min(len(gone) - len(receives), v.fmt["roster_size"] - count_after)
        for pid in _best_free_agents(m, pool, spots):
            pool.add(pid)
            adjustments.append({"action": "add_free_agent", "player": v.ref(pid), "reason": "roster_spot"})
    after = m.value(frozenset(pool))
    name = v.names[roster_id]
    return {
        "fantasy_team": name, "fantasy_team_id": roster_id,
        "receives": [v.ref(p) for p in receives], "gives": [v.ref(p) for p in gives], "adjustments": adjustments,
        "lineup_value_before": r1(before), "lineup_value_after": r1(after), "value_change": r1(after - before),
        "why": why(f"{name}'s starting lineup value changes by {after - before:+.1f}.",
                   reason("lineup_value", [*gives, *receives], {"before": r1(before), "after": r1(after), "change": r1(after - before)},
                          f"{name}'s best lineup is worth {before:.1f}, and {after:.1f} after the trade."),
                   *(reason("roster_spot", [a["player"]["player_id"]], {"action": a["action"]},
                            f"{name} {'drops' if a['action'] == 'drop' else 'adds the best free agent,'} {a['player']['name']} to stay within the roster size.")
                     for a in adjustments)),
    }  # fmt: skip


def _verdict(mine: float, theirs: float) -> str:
    if mine > 0 and theirs > 0:
        return "both gain"
    if mine < 0 and theirs < 0:
        return "both lose"
    if mine == 0 and theirs == 0:
        return "no change for either"
    return "I gain more" if mine >= theirs else "they gain more"


def _evaluation(v: View, m: Market, give: list[str], get: list[str], team_id: int, horizon: str, warnings: list[str]) -> dict:
    mine, theirs = _side(m, v.me, give, get), _side(m, team_id, get, give)
    names = lambda ids: " and ".join(v.ref(p)["name"] for p in ids)  # noqa: E731
    sentence = lambda change: ("gain " if change >= 0 else "lose ") + f"{abs(change):.1f}"  # noqa: E731
    label = "rest of season" if horizon == "ros" else "this week"
    summary = (f"Trading {names(give)} for {names(get)}: you {sentence(mine['value_change'])}, "
               f"{v.names[team_id]} {sentence(theirs['value_change']).replace('gain', 'gains').replace('lose', 'loses')} ({label}).")  # fmt: skip
    deadline = v.deadline()
    if deadline["passed"]:
        warnings = [*warnings, f"trade deadline passed (week {deadline['week']})"]
    return {**v.envelope("trade_evaluation", summary, warnings), "horizon": horizon, "mine": mine, "theirs": theirs,
            "deadline": deadline, "verdict": _verdict(mine["value_change"], theirs["value_change"])}  # fmt: skip


def _check(v: View, give: list[str], get: list[str], team_id: int) -> None:
    if v.me is None:
        raise NoData("my fantasy team is not stored for this league")
    if team_id == v.me or team_id not in v.names:
        raise NoData("the trade partner must be another fantasy team in this league")
    for pid in give:
        if v.held.get(pid, (None,))[0] != v.me:
            raise NoData(f"{v.ref(pid)['name']} is not on my roster")
    for pid in get:
        if v.held.get(pid, (None,))[0] != team_id:
            raise NoData(f"{v.ref(pid)['name']} is not on {v.names[team_id]}'s roster")
    if not give or not get:
        raise NoData("a trade needs players on both sides")


def evaluate_trade(conn: sqlite3.Connection, league_id: str, give: list[str], get: list[str], with_fantasy_team_id: int,
                   horizon: str = "ros") -> dict:  # fmt: skip
    warnings = artifact_view.ensure(conn, league_id)
    v = View(conn, league_id)
    _check(v, give, get, with_fantasy_team_id)
    return _evaluation(v, Market(v, horizon), give, get, with_fantasy_team_id, horizon, warnings)


def find_trade_ideas(conn: sqlite3.Connection, league_id: str, target_player_id: str | None = None, need_position: str | None = None,
                     with_fantasy_team_id: int | None = None, my_tradeable: list[str] | None = None, horizon: str = "ros",
                     limit: int = 5) -> dict:  # fmt: skip
    """One-for-one offers that raise my starting lineup value and the other fantasy team's.

    target_player_id (a player I want) or need_position (the best players at that position); with neither, only
    my_tradeable (what could I get for him?) and every position is searched, the best players of each. with_fantasy_team_id
    searches only that fantasy team, otherwise every other one (then my_tradeable is required). my_tradeable
    defaults to my whole active roster when a fantasy team is named."""
    warnings = artifact_view.ensure(conn, league_id)
    v = View(conn, league_id)
    if v.me is None:
        raise NoData("my fantasy team is not stored for this league")
    if not target_player_id and not need_position and not my_tradeable:
        raise NoData("a target player, a position of need, or the players I would trade is required")
    if with_fantasy_team_id is None and not my_tradeable:
        raise NoData("say which of my players I am willing to trade, or which fantasy team to search")
    teams = [with_fantasy_team_id] if with_fantasy_team_id is not None else [t for t in v.names if t != v.me]
    if target_player_id:
        teams = [t for t in teams if v.held.get(target_player_id, (None,))[0] == t]
    deadline = v.deadline()
    base = {"searched_fantasy_teams": [v.names[t] for t in teams], "deadline": deadline}
    if deadline["passed"]:
        warnings.append(f"trade deadline passed (week {deadline['week']})")
        return {**v.envelope("trade_ideas", f"No trade ideas: the trade deadline passed (week {deadline['week']}).", warnings),
                "ideas": [], **base}  # fmt: skip
    mine_ids = my_tradeable or v.active(v.me)
    for pid in mine_ids:
        if v.held.get(pid, (None,))[0] != v.me:
            raise NoData(f"{v.ref(pid)['name']} is not on my roster")
    m = Market(v, horizon)
    my_before = m.value(frozenset(v.active(v.me)))
    found = []
    for team in teams:
        theirs_before = m.value(frozenset(v.active(team)))
        by_mean = sorted(v.active(team), key=lambda p: -m.mean(p))
        if target_player_id:
            targets = [target_player_id]
        elif need_position:
            targets = [p for p in by_mean if v.position(p) == need_position][:5]
        else:  # every position: their best players at each
            targets = [p for pos in slot_demand(v.slots) for p in [q for q in by_mean if v.position(q) == pos][:TARGETS_PER_POSITION]]
        for t in targets:
            for g in mine_ids:
                mine_after = m.value(frozenset({*(p for p in v.active(v.me) if p != g), t}))
                if mine_after - my_before < MIN_IDEA_GAIN:
                    continue
                theirs_after = m.value(frozenset({*(p for p in v.active(team) if p != t), g}))
                if theirs_after - theirs_before >= MIN_IDEA_GAIN:
                    found.append((mine_after - my_before, g, t, team))
    ideas = []
    for rank, (_, g, t, team) in enumerate(sorted(found, key=lambda x: -x[0])[:limit], 1):
        ev = _evaluation(v, m, [g], [t], team, horizon, [])
        change = ev["theirs"]["value_change"]
        ideas.append({"rank": rank, "evaluation": ev,
                      "why": why(f"Their starting lineup value rises by {change:.1f}.",
                                 reason("lineup_value", [g, t], {"their_change": change}, f"{ev['theirs']['fantasy_team']}'s starting lineup gains {change:.1f}."))})  # fmt: skip
    where = ", ".join(base["searched_fantasy_teams"]) or "any fantasy team"
    summary = (f"{len(ideas)} trade idea{'s' if len(ideas) != 1 else ''} with {where} that raise{'s' if len(ideas) == 1 else ''} both starting lineups."
               if ideas else f"No one-for-one trade with {where} raises both starting lineups.")
    return {**v.envelope("trade_ideas", summary, warnings), "ideas": ideas, **base}
