"""Tool results as text for the model: SUMMARY, AS OF, labeled facts (one player per line), WARNINGS.

Every number the model may quote is printed here, already rounded. The model never computes.
"""

import logging
from datetime import datetime

from ingestion.nfl import service

log = logging.getLogger(__name__)

GAME_STATUS = {"not_started": "not started", "in_progress": "in progress", "complete": "game over"}
ZERO_REASON = {
    "ir": "on IR",
    "out": "ruled out",
    "pup": "on PUP",
    "suspended": "suspended",
    "bye": "bye week",
}
HORIZON = {"week": "THIS WEEK", "ros": "REST OF SEASON", "full_season": "FULL SEASON"}

# Sleeper scoring keys as plain words, so the model never sees (or repeats) a key (TEST_QUESTIONS T1 rule 7).
SCORING_LABELS = {
    "pass_yd": "passing yard", "pass_td": "passing touchdown", "pass_int": "interception thrown",
    "pass_2pt": "passing two-point conversion", "pass_cmp": "completed pass", "pass_inc": "incomplete pass",
    "pass_att": "pass attempt", "pass_sack": "sack taken", "pass_int_td": "interception returned for a touchdown",
    "rush_yd": "rushing yard", "rush_td": "rushing touchdown", "rush_2pt": "rushing two-point conversion",
    "rush_att": "rushing attempt", "rush_fd": "rushing first down",
    "rec": "reception", "rec_yd": "receiving yard", "rec_td": "receiving touchdown",
    "rec_2pt": "receiving two-point conversion", "rec_tgt": "target", "rec_fd": "receiving first down",
    "bonus_rec_te": "reception by a tight end (TE premium)", "bonus_rec_rb": "reception by a running back",
    "bonus_rec_wr": "reception by a wide receiver",
    "bonus_pass_yd_300": "300-399 passing yard game", "bonus_pass_yd_400": "400+ passing yard game",
    "bonus_rush_yd_100": "100-199 rushing yard game", "bonus_rush_yd_200": "200+ rushing yard game",
    "bonus_rec_yd_100": "100-199 receiving yard game", "bonus_rec_yd_200": "200+ receiving yard game",
    "bonus_pass_td_50p": "passing touchdown of 50+ yards", "bonus_rush_td_40p": "rushing touchdown of 40+ yards",
    "bonus_rec_td_40p": "receiving touchdown of 40+ yards",
    "fum": "fumble", "fum_lost": "fumble lost", "fum_rec": "fumble recovered", "fum_rec_td": "fumble recovery touchdown",
    "ff": "forced fumble", "int": "interception (team defense)", "sack": "sack (team defense)",
    "safe": "safety (team defense)", "blk_kick": "blocked kick (team defense)",
    "def_td": "touchdown by the team defense", "def_st_td": "defensive or special teams touchdown",
    "def_st_ff": "forced fumble on special teams (team defense)",
    "def_st_fum_rec": "fumble recovered on special teams (team defense)",
    "st_td": "special teams touchdown", "st_ff": "special teams forced fumble",
    "st_fum_rec": "special teams fumble recovered",
    "pts_allow_0": "zero points allowed", "pts_allow_1_6": "1-6 points allowed", "pts_allow_7_13": "7-13 points allowed",
    "pts_allow_14_20": "14-20 points allowed", "pts_allow_21_27": "21-27 points allowed",
    "pts_allow_28_34": "28-34 points allowed", "pts_allow_35p": "35 or more points allowed",
    "yds_allow_0_100": "under 100 yards allowed", "yds_allow_100_199": "100-199 yards allowed",
    "fgm": "field goal made", "fgm_0_19": "field goal of 0-19 yards", "fgm_20_29": "field goal of 20-29 yards",
    "fgm_30_39": "field goal of 30-39 yards", "fgm_40_49": "field goal of 40-49 yards",
    "fgm_50_59": "field goal of 50-59 yards", "fgm_60p": "field goal of 60+ yards", "fgm_50p": "field goal of 50+ yards",
    "fgmiss": "missed field goal", "fgmiss_0_19": "missed field goal of 0-19 yards", "xpm": "extra point made",
    "xpmiss": "missed extra point",
    "idp_tkl_solo": "solo tackle", "idp_tkl_ast": "assisted tackle", "idp_tkl_loss": "tackle for loss",
    "idp_sack": "sack (individual defender)", "idp_qb_hit": "quarterback hit",
    "idp_int": "interception (individual defender)", "idp_ff": "forced fumble (individual defender)",
    "idp_fum_rec": "fumble recovered (individual defender)", "idp_pass_def": "pass defended",
    "idp_safe": "safety (individual defender)", "idp_blk_kick": "blocked kick (individual defender)",
    "idp_def_td": "defensive touchdown",
}


def when(iso: str | None) -> str:
    """ISO UTC as local time, e.g. 'Tue 11-03 14:29'."""
    if not iso:
        return "unknown time"
    return datetime.fromisoformat(iso).astimezone().strftime("%a %m-%d %H:%M")


def pts(x: float | None) -> str:
    return "none" if x is None else f"{x:.1f}"


def signed(x: float) -> str:
    return f"{x:+.1f}"


def pct(p: float) -> str:
    return f"{p:.0%}"


# labels

def status_label(ref: dict) -> str:
    s = ref["fantasy_status"]
    if s == "mine_starter":
        return f"MY ROSTER (starter, {ref['lineup_slot']})"
    if s == "mine_bench":
        return "MY ROSTER (bench)"
    if s == "mine_ir":
        return "MY ROSTER (IR)"
    if s == "free_agent":
        return "FREE AGENT"
    return "FANTASY TEAM: " + s.removeprefix("fantasy_team:")


def game_text(game: dict) -> str:
    if game["status"] == "bye":
        return "bye this week"
    side = "vs" if game["home"] else "at"
    return f"{side} {game['opponent_nfl_team']} {when(game['kickoff'])}, {GAME_STATUS[game['status']]}"


def player_line(ref: dict) -> str:
    parts = [ref["name"], ref["position"], f"NFL TEAM: {ref['nfl_team'] or 'none'}", status_label(ref)]
    if ref["injury_status"]:
        parts.append(f"injury: {ref['injury_status']}")
    if ref.get("game"):
        parts.append(game_text(ref["game"]))
    return ", ".join(parts)


def roster_lines(league, roster_id: int, notes: dict[str, str] | None = None) -> list[str]:
    """A fantasy team's slots as set in Sleeper, one line each (tool 2, tool 3, context MY ROSTER).

    notes: extra text per player id (game-day inactive)."""
    out = []
    for item in league.roster(roster_id):
        p = item["player"]
        if p is None:
            out.append(f"{item['slot']}: (empty)")
            continue
        line = f"{item['slot']}: {player_line(league.ref(p))}"
        if p["practice_participation"]:
            line += f", practice: {p['practice_participation']} (Sleeper catalog)"
        if notes and p["player_id"] in notes:
            line += f", {notes[p['player_id']]}"
        out.append(line)
    return out


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


WAIVER_ORDER = {  # what each Sleeper waiver type means, so the model never supplies the rule itself
    "rolling": "rolling waiver order (no FAAB): a fantasy team whose claim succeeds moves to the end of the order",
    "reverse_standings": "reverse-standings waiver order (no FAAB): the fantasy team lowest in the standings claims "
                         "first",
}
WAIVER_GAP = "my place in the waiver order is not available"


def format_lines(fmt: dict, my_roster_id: int | None, this_week: int | None) -> list[str]:
    """FORMAT and WAIVERS lines from LeagueFormat (section 8)."""
    defense = {"idp": "IDP (individual defensive players)", "team_def": "team DEF (team defense)",
               "none": "no defense slot"}[fmt["defense"]]
    rec = fmt["reception_points"]
    scoring = {1.0: "full PPR (1 point per reception)", 0.5: "half PPR (0.5 per reception)",
               0.0: "standard (no points per reception)"}.get(rec, f"{rec:g} points per reception")
    te = f"TE premium {fmt['te_premium']:g} per reception" if fmt["te_premium"] else "no TE premium"
    parts = [defense, scoring, "superflex" if fmt["superflex"] else "no superflex", te,
             "kicker" if fmt["kicker"] else "no kicker", f"{fmt['num_fantasy_teams']} fantasy teams",
             "starting slots " + " ".join(fmt["slots"]), f"{fmt['bench_size']} bench", f"{fmt['ir_slots']} IR"]
    if fmt["ir_slots"]:
        parts.append("IR slots allow these designations: " + ", ".join(fmt["ir_allowed_designations"]))
    if fmt["taxi_slots"]:
        parts.append(f"{fmt['taxi_slots']} taxi")
    if fmt["waiver_type"] == "faab":
        left = fmt["faab_remaining"].get(str(my_roster_id), fmt["faab_remaining"].get(my_roster_id))
        waivers = f"FAAB, ${left} of ${fmt['faab_budget']} left"
    else:
        waivers = f"{WAIVER_ORDER[fmt['waiver_type']]}; {WAIVER_GAP}"
    deadline = fmt["trade_deadline_week"]
    if deadline is None:
        trade = "none"
    else:
        trade = f"week {deadline} ({'passed' if this_week and this_week > deadline else 'not passed'})"
    return [f"FORMAT: {'; '.join(parts)}", f"WAIVERS: {waivers}. TRADE DEADLINE: {trade}"]


def why_text(why: dict) -> str:
    texts = [why["summary"]] + [r["text"] for r in why["reasons"] if r["text"] != why["summary"]]
    return "Why: " + " ".join(texts)


# envelope

def sources_of(result: dict) -> list[dict]:
    """The labeled as_of entries of a decision result, as API `sources` items."""
    return [{"label": v["source_label"], "as_of": v["as_of"]} for v in result["as_of"].values() if v]


def as_of_line(sources: list[dict]) -> str:
    if not sources:
        return "AS OF: no stored data"
    return "AS OF: " + "; ".join(f"{s['label']} {when(s['as_of'])}" for s in sources)


def scoring_label(key: str) -> str:
    if key not in SCORING_LABELS:
        log.warning("no plain label for scoring key %s", key)
    return SCORING_LABELS.get(key) or key.replace("_", " ")


def unprojected(items: list[dict]) -> str | None:
    if not items:
        return None
    labels = ", ".join(f"{scoring_label(i['key'])} ({i['value']:g} points each)" for i in items)
    return f"league scoring rules no projection includes: {labels}"


# NFL facts (docs/data-contract.md section 4)

def fact_source(f: dict) -> str:
    return f"per {f['source_label']}, {when(f['as_of'])}"


def fact_sources(facts: list[dict]) -> list[dict]:
    """API `sources` items from NFL facts: one per label, the oldest as_of kept."""
    out = {}
    for f in facts:
        if f["as_of"] and (f["source_label"] not in out or f["as_of"] < out[f["source_label"]]):
            out[f["source_label"]] = f["as_of"]
    return [{"label": k, "as_of": v} for k, v in out.items()]


def usage_text(f: dict) -> str:
    v = f["value"]
    parts = [f"{name} {v[k]:.0%}" for k, name in (("offense_pct", "offensive snaps"), ("defense_pct", "defensive snaps"),
                                                   ("st_pct", "special teams snaps")) if v[k]]
    parts += [f"{name} {v[k]}" for k, name in (("targets", "targets"), ("carries", "carries")) if v[k] is not None]
    if v["target_share"] is not None:
        parts.append(f"target share {v['target_share']:.0%}")
    return f"week {f['week']}: {', '.join(parts) or 'no snaps or touches'} ({fact_source(f)})"


def injury_text(f: dict | None) -> str:
    if f is None:
        return "injury: no designation and no practice status listed by any source"
    v = f["value"]
    body = f" ({v['body_part']})" if v["body_part"] else ""
    text = f"injury: {v['designation'] or 'no designation'}{body}"
    if v["practice_status"]:
        text += f", practice: {v['practice_status']} (latest in the report, not a per-day history)"
    text += f" ({fact_source(f)})"
    for a in f["also"]:
        text += f"; {a['source']} lists {a['value'] or 'no designation'} ({when(a['as_of'])})"
    return text


def block(summary: str, sources: list[dict], lines: list[str], warnings: list[str]) -> str:
    out = [f"SUMMARY: {summary}", as_of_line(sources), *lines]
    if warnings:
        out.append("WARNINGS: " + "; ".join(warnings))
    return "\n".join(out)


def envelope(result: dict, lines: list[str], extra_warnings: list[str] = ()) -> str:
    warnings = [*result["warnings"], *extra_warnings]
    if item := unprojected(result["unprojected_scoring_items"]):
        warnings.append(item)
    return block(result["summary"], sources_of(result), lines, warnings)


# decisions (docs/data-contract.md section 9)

def lineup(r: dict) -> str:
    m = r["matchup"]
    if m:
        lines = [f"WEEK {r['week']} MATCHUP: vs {m['opponent_fantasy_team']} (fantasy team), "
                 f"their projected total {pts(m['opponent']['mean'])} (their lineup as set in Sleeper)"]
    else:
        lines = [f"WEEK {r['week']} MATCHUP: no opponent stored (bye or not scheduled), so no spread and no win "
                 "probability"]
    names = {"current": "YOUR LINEUP AS SET IN SLEEPER", "expected": "EXPECTED-POINTS LINEUP",
             "high_risk": "HIGH-RISK, HIGH-REWARD LINEUP"}
    for key in ("current", "expected", "high_risk"):
        plan = r[key]
        line = f"{names[key]}: projected {pts(plan['mine']['mean'])}"
        if plan["spread"] is not None:
            line += f", spread {signed(plan['spread'])}, win probability {pct(plan['win_probability'])}"
        lines.append(line)
        if key == "current":
            continue
        if not plan["changes"] and not plan["slot_changes"]:
            lines.append("  changes: none, keep the lineup set in Sleeper")
        for c in plan["changes"]:  # moves against the lineup set in Sleeper
            out = f"benching {c['player_out']['name']}" if c["player_out"] else "filling an empty slot"
            lines.append(
                f"  change: start {player_line(c['player_in'])} at {c['slot']}, {out}, projected points "
                f"{signed(c['mean_gain'])}. {why_text(c['why'])}"
            )
        for x in plan["slot_changes"]:  # starters who stay in, in another slot
            lines.append(f"  move {x['player']['name']} from {x['from_slot']} to {x['to_slot']} (still starting)")
        lines.append(f"  {why_text(plan['why'])}")
    lines.append("EXPECTED-POINTS LINEUP BY SLOT:")
    for s in r["expected"]["lineup"]:
        if s["player"] is None:
            lines.append(f"  {s['slot']}: (empty)")
        else:
            lines.append(f"  {s['slot']}: {player_line(s['player'])}, projected {pts(s['mean'])}")
    for x in r["locked"]:
        lines.append(f"LOCKED ({x['side']}, {'game started' if x['reason'] == 'game_started' else 'game over'}): "
                     f"{player_line(x['player'])}")
    for x in r["ir_flags"]:
        lines.append(f"IR FLAG: {player_line(x['player'])}: {x['reason']}")
    if r["empty_slots"]:
        lines.append("EMPTY SLOTS (no eligible player): " + ", ".join(r["empty_slots"]))
    return envelope(r, lines)


def waivers(r: dict) -> str:
    lines = []
    for lst in r["lists"]:
        lines.append(f"{HORIZON[lst['horizon']]}:" + ("" if lst["candidates"] else " none above replacement level"))
        for c in lst["candidates"]:
            rep = c["replacement"]
            faab = f", FAAB guide ${c['faab_guide']}" if c["faab_guide"] is not None else ""
            lines.append(
                f"  {c['rank']}. {player_line(c['player'])}, projected {pts(c['mean'])}, "
                f"value over replacement {pts(c['vorp'])} (replacement: {rep['pool']} number {rep['rank_n']}, "
                f"{pts(rep['points'])}){faab}. {why_text(c['why'])}"
            )
        for x in lst["excluded"]:
            reason = "his game already started" if x["reason"] == "game_started" else "bye week"
            lines.append(f"  left out: {x['player']['name']} ({reason})")
    b = r["budget"]
    if b["type"] == "faab":
        lines.append(f"BUDGET: FAAB ${b['remaining']} of ${b['total']} left, minimum bid ${b['min_bid']}")
    else:
        lines.append(f"BUDGET: {WAIVER_ORDER[b['type']]}; {WAIVER_GAP}")
    return envelope(r, lines)


def stash(r: dict) -> str:
    lines = []
    for c in r["candidates"]:
        inj = c["injury"]
        body = f" ({inj['body_part']})" if inj["body_part"] else ""
        lines.append(
            f"{c['rank']}. {player_line(c['player'])}, healthy rest-of-season projection {pts(c['healthy_ros_mean'])}, "
            f"healthy value over replacement {pts(c['healthy_ros_vorp'])}, injury {inj['status']}{body} "
            f"(per {inj['source']}, {when(inj['as_of'])}). {why_text(c['why'])}"
        )
        if not c["news"]:
            lines.append("  news: none stored, so no return timeline")
        for n in c["news"]:
            text = ". ".join(x for x in (n["title"], n["text"]) if x)
            lines.append(f"  news ({n['source']}, {when(n['published_at'])}): {text}")
    return envelope(r, lines)


def _side(label: str, s: dict) -> list[str]:
    lines = [
        f"{label}: {s['fantasy_team']} (fantasy team) gets "
        + "; ".join(p["name"] for p in s["receives"])
        + " and gives "
        + "; ".join(p["name"] for p in s["gives"]),
        f"  starting lineup value {pts(s['lineup_value_before'])} before, {pts(s['lineup_value_after'])} after, "
        f"change {signed(s['value_change'])}",
    ]
    for a in s["adjustments"]:
        action = "drops" if a["action"] == "drop" else "adds the best free agent"
        lines.append(f"  roster spot: {action} {player_line(a['player'])}")
    lines.append(f"  {why_text(s['why'])}")
    return lines


def _deadline(d: dict) -> str:
    if d["week"] is None:
        return "TRADE DEADLINE: none in this league"
    return f"TRADE DEADLINE: week {d['week']} ({'passed' if d['passed'] else 'not passed'})"


def trade_evaluation(r: dict) -> str:
    horizon = "rest of season" if r["horizon"] == "ros" else "this week"
    lines = [f"HORIZON: {horizon}", *_side("YOU", r["mine"]), *_side("THEM", r["theirs"]),
             f"VERDICT: {r['verdict']}", _deadline(r["deadline"])]
    return envelope(r, lines)


def trade_ideas(r: dict) -> str:
    lines = ["FANTASY TEAMS SEARCHED: " + ", ".join(r["searched_fantasy_teams"]), _deadline(r["deadline"])]
    if not r["ideas"]:
        lines.append("IDEAS: none")
    for idea in r["ideas"]:
        e = idea["evaluation"]
        lines.append(f"IDEA {idea['rank']} with {e['theirs']['fantasy_team']} (fantasy team):")
        lines += ["  " + x for x in _side("YOU", e["mine"]) + _side("THEM", e["theirs"])]
        lines.append(f"  Why it helps them: {why_text(idea['why']).removeprefix('Why: ')}")
    return envelope(r, lines)


def week_results(r: dict) -> str:
    me, opp, bench = r["mine"], r["opponent"], r["my_bench"]
    lines = [
        f"WEEK {r['week']} RESULT: {r['result'].upper()}, {me['fantasy_team']} {pts(me['score'])}, "
        f"{opp['fantasy_team']} (fantasy team) {pts(opp['score'])} (actual points)",
        "OPPONENT TOP SCORERS: "
        + "; ".join(f"{x['player']['name']} ({x['slot']}) {pts(x['points'])}" for x in r["opponent_top_scorers"]),
        f"MY BENCH: you scored {pts(bench['actual_points'])}; best possible lineup from your roster "
        f"{pts(bench['optimal_points'])} (from actual points, not a projection); "
        f"points left on your bench {pts(bench['points_left_on_bench'])}",
    ]
    for mv in bench["moves"]:
        lines.append(
            f"  better move at {mv['slot']}: start {mv['start']['name']} instead of "
            f"{mv['bench']['name'] if mv['bench'] else 'the empty slot'}, "
            f"{signed(mv['gain'])}"
        )
    return envelope(r, lines)


def leaders(r: dict) -> str:
    lines = [f"ACTUAL POINTS IN YOUR LEAGUE SCORING THROUGH WEEK {r['through_week']} (not projections):"]
    for x in r["rows"]:
        lines.append(
            f"{x['rank']}. {player_line(x['player'])}, {pts(x['points'])} points, {x['games']} games, "
            f"{pts(x['points_per_game'])} per game"
        )
    return envelope(r, lines)


def return_line(row: dict) -> str | None:
    """IR players only (docs/data-contract.md sections 7 and 11, return line)."""
    if row["zero_reason"] != "ir" and row["return_date"] is None and row["player"]["injury_status"] != "IR":
        return None
    if row["return_date"] is None:
        return "RETURN: unknown (no source has a return date)"
    estimate = (f"return date {row['return_date']}, source {service.LABEL[(row['return_date_source'], 'injury')]}, "
                f"as of {when(row['return_date_as_of'])}; a news estimate, not an official report")
    if row["return_week"] is None:
        return f"RETURN: after week 18 ({estimate}). Projected 0 for every remaining week (on IR)"
    return f"RETURN: week {row['return_week']} ({estimate}). Weeks before {row['return_week']} project 0 (on IR)"


def projections(r: dict) -> str:
    """Tool 8 result: rows read from the projection artifact (section 7)."""
    head = HORIZON[r["horizon"]] + (f" (week {r['week']})" if r["horizon"] == "week" else f" (from week {r['week']})")
    lines = [f"PROJECTIONS {head}, expected points in your league scoring:"]
    extra = []
    for x in r["rows"]:
        sd = x["variance"] ** 0.5
        low, high = max(0.0, x["mean"] - sd), x["mean"] + sd
        zero = ""
        if x["zero_reason"]:  # a week row is 0; a longer horizon counts 0 for the weeks it covers
            reason = ZERO_REASON[x["zero_reason"]]
            zero = f", projected 0 ({reason})" if r["horizon"] == "week" else f", 0 counted while {reason}"
        srcs = ", ".join(f"{s['source']} {s['weight']:.0%}" for s in x["sources"]) or "none"
        lines.append(
            f"{x['rank']}. {player_line(x['player'])}, projected {pts(x['mean'])}, likely range {pts(low)} to "
            f"{pts(high)}, {x['n_games']} games{zero}, sources: {srcs}"
        )
        if ret := return_line(x):
            lines.append("  " + ret)
            if not x.get("return_date"):
                extra.append(f"{x['player']['name']}: {ret}")
    return envelope(r, lines, extra)


def trending(r: dict) -> str:
    kind = "added" if r["type"] == "add" else "dropped"
    lines = [f"MOST {kind.upper()} ACROSS ALL SLEEPER LEAGUES, LAST {r['hours']} HOURS (context only, not a projection):"]
    for x in r["rows"]:
        lines.append(f"{x['rank']}. {player_line(x['player'])}, {kind} in {x['count']} leagues")
    return envelope(r, lines)
