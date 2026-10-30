"""Build the player-game modeling dataset without any weekly (per-game) projection as a feature.

Rows: QB/RB/WR/TE on their team's 53-man roster that week (nflverse weekly rosters: status ACT or INA),
in a week the team played, who played in one of the team's last 4 games or were preseason-ranked, and
were not listed Out on the injury report. Target: actual PPR points (0 if the player did not play).

Features are known before kickoff: the player's earlier games (as-of join that excludes the target week),
usage quality from nflverse ff_opportunity (lagged), receiving/rushing efficiency (EPA, WOPR), the expected
starting QB's passing ability, team offense context, opponent defense strength (raw, by EPA, and
schedule-adjusted by position), next man up (injury report + roster status), game context and Vegas lines,
the injury report,
and yearly preseason rankings frozen at week 1 (FantasyPros' last preseason PPR consensus, Sleeper's
week-1 ADP). Sleeper's stored season projections are not used: they are end-of-season versions.

Sleeper's weekly projection is written to a separate benchmark file, read only by the evaluation.

Output: data/player_games_{train,test}.parquet, data/benchmark_sleeper.parquet
Run: uv run python build_dataset.py
"""

import numpy as np
import pandas as pd

from common import DATA, POSITIONS, SCORING, TO_NFLVERSE, _injuries, _schedule_long, score

CONTEXT_COLS = ["home", "team_spread", "total_line", "implied_total", "roof", "div_game", "rest"]
ACTIVE_WINDOW = 4  # team games


def time_index(season, week):
    # consecutive across seasons (week 1 directly follows week 18), so gaps count missed weeks only
    return season * 18 + week


def sleeper_ids() -> pd.DataFrame:
    ids = pd.read_parquet(DATA / "playerids.parquet")[["sleeper_id", "gsis_id", "fantasypros_id", "draft_year", "birthdate"]]
    ids = ids.dropna(subset=["gsis_id"]).drop_duplicates("gsis_id")
    ids["sleeper_id"] = ids.sleeper_id.map(lambda x: str(int(x)) if pd.notna(x) else None)
    ids["draft_year"] = ids.draft_year.replace(0, np.nan)  # 0 is nflverse's "unknown"
    return ids


def roster_rows() -> pd.DataFrame:
    r = pd.read_parquet(DATA / "rosters_weekly.parquet")
    r = r[(r.game_type == "REG") & r.position.isin(POSITIONS) & r.status.isin(["ACT", "INA"])]
    r = r[["season", "week", "team", "position", "gsis_id", "sleeper_id", "full_name"]].rename(
        columns={"team": "nfl_team", "full_name": "name"})
    ids = sleeper_ids()
    r = r.merge(ids, on="gsis_id", how="left", suffixes=("_roster", ""))
    r["player_id"] = r.pop("sleeper_id_roster").fillna(r.pop("sleeper_id"))
    r = r.dropna(subset=["player_id", "gsis_id"]).drop_duplicates(["player_id", "season", "week"])
    return r


def played_games() -> pd.DataFrame:
    """Every game a QB/RB/WR/TE took the field, 2021-2026, with fantasy points and usage."""
    a = pd.read_parquet(DATA / "sleeper_stats.parquet")
    a = a[a.player_position.isin(POSITIONS)].copy()
    stat_cols = list(SCORING) + ["rec_tgt", "rush_att", "pass_att", "rec_rz_tgt", "rush_rz_att", "rec_air_yd",
                                 "off_snp", "tm_off_snp"]
    a[stat_cols] = a[stat_cols].fillna(0)
    a["pts"] = score(a, "")
    a["team"] = a.team.replace(TO_NFLVERSE)
    a["opponent"] = a.opponent.replace(TO_NFLVERSE)
    team_tgt = a.groupby(["season", "week", "team"]).rec_tgt.transform("sum")
    a["tgt_share"] = np.where(team_tgt > 0, a.rec_tgt / team_tgt, np.nan)
    a["snap_share"] = np.where(a.tm_off_snp > 0, a.off_snp / a.tm_off_snp, np.nan)
    a["rz_opps"] = a.rec_rz_tgt + a.rush_rz_att
    a = a[a.gp.eq(1)]
    a["t"] = time_index(a.season, a.week)
    return a[["player_id", "season", "week", "t", "team", "opponent", "player_position", "pts", "rec_tgt",
              "rush_att", "pass_att", "rz_opps", "rec_air_yd", "tgt_share", "snap_share"]]


def player_history(g: pd.DataFrame) -> pd.DataFrame:
    """Rolling state *after* each played game; joined to later weeks with an as-of merge."""
    g = g.sort_values(["player_id", "t"]).copy()
    by = g.groupby("player_id")
    out = g[["player_id", "t", "season"]].copy()
    for n in (3, 8):
        for c in ["pts", "snap_share", "tgt_share", "rec_tgt", "rush_att", "pass_att", "rz_opps", "rec_air_yd"]:
            out[f"{c}_l{n}"] = by[c].transform(lambda s: s.rolling(n, min_periods=1).mean())
    out["pts_std_l8"] = by.pts.transform(lambda s: s.rolling(8, min_periods=3).std())
    out["pts_max_l8"] = by.pts.transform(lambda s: s.rolling(8, min_periods=1).max())
    out["pts_season_avg"] = g.groupby(["player_id", "season"]).pts.transform(lambda s: s.expanding().mean())
    out["games_season"] = g.groupby(["player_id", "season"]).cumcount() + 1
    out["last_played_t"] = g.t
    return out.rename(columns={"season": "hist_season"})


def opportunity_history() -> pd.DataFrame:
    """Expected PPR points of each past game (nflverse ff_opportunity) and actual minus expected."""
    o = pd.read_parquet(DATA / "ff_opportunity.parquet")
    o = o[o.position.isin(POSITIONS)][["player_id", "season", "week", "total_fantasy_points_exp", "total_fantasy_points"]]
    o = o.rename(columns={"player_id": "gsis_id"}).dropna(subset=["gsis_id"])
    o["season"], o["week"] = o.season.astype(int), o.week.astype(int)
    # regular season only, like every other history source; playoff weeks 19-22 would also collide with
    # next season's weeks 1-4 in the time index
    o = o[o.week <= 18]
    o["t"] = time_index(o.season, o.week)
    o = o.sort_values(["gsis_id", "t"])
    by = o.groupby("gsis_id")
    o["xfp_l3"] = by.total_fantasy_points_exp.transform(lambda s: s.rolling(3, min_periods=1).mean())
    o["xfp_l8"] = by.total_fantasy_points_exp.transform(lambda s: s.rolling(8, min_periods=1).mean())
    eff = o.total_fantasy_points - o.total_fantasy_points_exp
    o["fp_over_xfp_l8"] = eff.groupby(o.gsis_id).transform(lambda s: s.rolling(8, min_periods=1).mean())
    return o[["gsis_id", "t", "xfp_l3", "xfp_l8", "fp_over_xfp_l8"]]


def defense_history(g: pd.DataFrame) -> pd.DataFrame:
    """Fantasy points the defense allowed to each position per game, trailing 8 games."""
    d = g.groupby(["opponent", "player_position", "season", "week", "t"]).pts.sum().reset_index()
    d = d.sort_values(["opponent", "player_position", "t"])
    d["opp_pts_allowed_l8"] = d.groupby(["opponent", "player_position"]).pts.transform(
        lambda s: s.rolling(8, min_periods=2).mean())
    return d.rename(columns={"player_position": "position"})[["opponent", "position", "t", "opp_pts_allowed_l8"]]


def adjusted_defense(g: pd.DataFrame) -> pd.DataFrame:
    """Schedule-adjusted defense: per game, how many points players at a position scored against this defense
    above their own average over their previous 8 games; trailing 8 games of the defense. Removes the
    "they just faced good receivers" confound that raw points allowed carries."""
    g = g.sort_values(["player_id", "t"]).copy()
    prior = g.groupby("player_id").pts.transform(lambda s: s.shift().rolling(8, min_periods=2).mean())
    g["over_avg"] = g.pts - prior
    d = g.dropna(subset=["over_avg"]).groupby(["opponent", "player_position", "t"]).over_avg.sum().reset_index()
    d = d.sort_values(["opponent", "player_position", "t"])
    d["opp_pos_over_avg_l8"] = d.groupby(["opponent", "player_position"]).over_avg.transform(
        lambda s: s.rolling(8, min_periods=2).mean())
    return d.rename(columns={"player_position": "position"})[["opponent", "position", "t", "opp_pos_over_avg_l8"]]


def team_games() -> pd.DataFrame:
    """One row per team-game (regular season): offensive volume and efficiency, and points scored."""
    t = pd.read_parquet(DATA / "team_stats.parquet")
    t = t[(t.season_type == "REG") & (t.week <= 18)].copy()
    t["dropbacks"] = t.attempts + t.sacks_suffered
    t["plays"] = t.dropbacks + t.carries
    t["pass_rate"] = t.dropbacks / t.plays
    t["pass_epa_db"] = t.passing_epa / t.dropbacks
    t["rush_epa_carry"] = t.rushing_epa / t.carries
    t["sack_rate"] = t.sacks_suffered / t.dropbacks
    s = pd.read_parquet(DATA / "schedules.parquet").query("game_type == 'REG'")
    pts = pd.concat([s[["season", "week", "home_team", "home_score"]].set_axis(["season", "week", "team", "points"], axis=1),
                     s[["season", "week", "away_team", "away_score"]].set_axis(["season", "week", "team", "points"], axis=1)])
    t = t.merge(pts, on=["season", "week", "team"], how="left")
    t["t"] = time_index(t.season.astype(int), t.week.astype(int))
    return t[["team", "opponent_team", "t", "plays", "pass_rate", "pass_epa_db", "rush_epa_carry", "sack_rate", "points"]]


def rolling_state(d: pd.DataFrame, by: str, cols: dict, n: int = 8) -> pd.DataFrame:
    """Trailing n-game mean of each column, as the state after each game (for the as-of join)."""
    d = d.sort_values([by, "t"]).copy()
    out = d[[by, "t"]].copy()
    for src, name in cols.items():
        out[name] = d.groupby(by)[src].transform(lambda s: s.rolling(n, min_periods=2).mean())
    return out


def offense_history(tg: pd.DataFrame) -> pd.DataFrame:
    return rolling_state(tg, "team", {"plays": "team_plays_l8", "pass_rate": "team_pass_rate_l8",
                                      "pass_epa_db": "team_pass_epa_l8", "rush_epa_carry": "team_rush_epa_l8",
                                      "points": "team_points_l8"})


def team_defense_history(tg: pd.DataFrame) -> pd.DataFrame:
    """What offenses did against each defense (the defense is the offense's opponent_team)."""
    d = tg.drop(columns="team").rename(columns={"opponent_team": "opponent"})
    return rolling_state(d, "opponent", {"pass_epa_db": "opp_pass_epa_allowed_l8", "rush_epa_carry": "opp_rush_epa_allowed_l8",
                                         "sack_rate": "opp_sack_rate_l8", "points": "opp_points_allowed_l8",
                                         "plays": "opp_plays_allowed_l8"})


def player_stat_history() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """From nflverse weekly player stats (regular season): receiving/rushing efficiency for every player,
    QB passing efficiency, and each team-game's starting QB (most dropbacks)."""
    p = pd.read_parquet(DATA / "player_stats.parquet")
    p = p[(p.season_type == "REG") & (p.week <= 18) & p.position.isin(POSITIONS)].copy()
    p["t"] = time_index(p.season.astype(int), p.week.astype(int))
    p = p.rename(columns={"player_id": "gsis_id"}).sort_values(["gsis_id", "t"])
    by = p.groupby("gsis_id")
    roll = lambda c, n=8: by[c].transform(lambda s: s.rolling(n, min_periods=1).sum())
    eff = p[["gsis_id", "t"]].copy()
    eff["wopr_l8"] = by.wopr.transform(lambda s: s.rolling(8, min_periods=1).mean())
    eff["air_yards_share_l8"] = by.air_yards_share.transform(lambda s: s.rolling(8, min_periods=1).mean())
    eff["rec_epa_tgt_l8"] = roll("receiving_epa") / roll("targets").replace(0, np.nan)
    eff["rush_epa_carry_l8"] = roll("rushing_epa") / roll("carries").replace(0, np.nan)

    q = p[(p.position == "QB") & (p.attempts >= 10)].copy()  # skip garbage-time and trick-play appearances
    q["db"] = q.attempts + q.sacks_suffered
    qb = q[["gsis_id", "t"]].copy()
    qby = q.groupby("gsis_id")
    qb["qb_epa_db_l8"] = qby.passing_epa.transform(lambda s: s.rolling(8, min_periods=1).sum()) / \
        qby.db.transform(lambda s: s.rolling(8, min_periods=1).sum())
    qb["qb_cpoe_l8"] = qby.passing_cpoe.transform(lambda s: s.rolling(8, min_periods=1).mean())
    qb["qb_ppr_l8"] = qby.fantasy_points_ppr.transform(lambda s: s.rolling(8, min_periods=1).mean())

    p["db"] = p.attempts + p.sacks_suffered
    starters = p[p.position == "QB"].sort_values("db").groupby(["team", "t"]).tail(1)[["team", "t", "gsis_id"]]
    return eff, qb, starters.rename(columns={"gsis_id": "last_starter_gsis"})


def preseason_ranks(ids: pd.DataFrame) -> pd.DataFrame:
    """Yearly signal frozen at week 1: FantasyPros' last PPR draft consensus before the season's first
    kickoff, and Sleeper's ADP as stored with its week-1 projections (later weeks' ADP is updated in-season)."""
    s = pd.read_parquet(DATA / "schedules.parquet").query("game_type == 'REG'")
    start = pd.to_datetime(s.groupby("season").gameday.min())
    fp = pd.read_parquet(DATA / "fp_draft_rankings.parquet")
    fp = fp[fp.pos.isin(POSITIONS)].assign(scrape_date=lambda d: pd.to_datetime(d.scrape_date))
    rows = []
    for season, kick in start.items():
        pre = fp[(fp.scrape_date < kick) & (fp.scrape_date.dt.year == season)]
        snap = pre[pre.scrape_date == pre.scrape_date.max()].copy()
        snap["season"] = season
        snap["fp_pos_rank"] = snap.groupby("pos").ecr.rank(method="first")
        rows.append(snap[["season", "id", "ecr", "fp_pos_rank"]])
    fp = pd.concat(rows).rename(columns={"id": "fantasypros_id", "ecr": "fp_ecr"})
    fp["fantasypros_id"] = fp.fantasypros_id.astype(str)
    fp = fp.merge(ids[["gsis_id", "fantasypros_id"]].dropna().assign(
        fantasypros_id=lambda d: d.fantasypros_id.astype(str).str.replace(r"\.0$", "", regex=True)), on="fantasypros_id")

    sl = pd.read_parquet(DATA / "sleeper_proj.parquet", columns=["player_id", "season", "week", "adp_dd_ppr", "pos_adp_dd_ppr"])
    sl = sl[sl.week == 1].drop(columns="week").rename(columns={"adp_dd_ppr": "sleeper_adp", "pos_adp_dd_ppr": "sleeper_pos_adp"})
    sl[["sleeper_adp", "sleeper_pos_adp"]] = sl[["sleeper_adp", "sleeper_pos_adp"]].where(sl.sleeper_adp < 1000)  # 1000 = undrafted
    return fp[["season", "gsis_id", "fp_ecr", "fp_pos_rank"]].drop_duplicates(["season", "gsis_id"]), sl


def asof(left: pd.DataFrame, right: pd.DataFrame, by) -> pd.DataFrame:
    """Attach the latest `right` state strictly before each left row's week."""
    left = left.assign(t=left.t.astype("int64")).sort_values("t")
    right = right.assign(t=right.t.astype("int64")).sort_values("t").rename(columns={"t": "t_hist"})
    return pd.merge_asof(left, right, left_on="t", right_on="t_hist", by=by, allow_exact_matches=False).drop(columns="t_hist")


def team_game_threshold(sched: pd.DataFrame, n: int = ACTIVE_WINDOW) -> pd.DataFrame:
    """For each team-week: the time index of the team's n-th previous game."""
    g = sched[["season", "week", "nfl_team"]].drop_duplicates().assign(t=lambda d: time_index(d.season, d.week))
    g = g.sort_values(["nfl_team", "t"])
    g["active_since_t"] = g.groupby("nfl_team").t.shift(n)
    return g[["season", "week", "nfl_team", "active_since_t"]]


def add_depth_features(df: pd.DataFrame, games: pd.DataFrame, qb: pd.DataFrame, starters: pd.DataFrame,
                       sched: pd.DataFrame) -> pd.DataFrame:
    """Who is next in line this week, and who throws the ball, from the injury report, weekly roster status
    and past usage only (no depth chart, no projection). Computed on the full roster before Out players leave
    the universe, because an injured starter is exactly what promotes his backup."""
    unavailable = df.report_status.isin(["Out", "Doubtful"])
    grp = [df.season, df.week, df.team, df.position]
    avail_usage = df.xfp_l3.fillna(0).where(~unavailable)
    df["team_pos_rank"] = avail_usage.groupby(grp).rank(ascending=False, method="first")
    df["team_pos_share"] = avail_usage / avail_usage.groupby(grp).transform("sum").replace(0, np.nan)

    # vacated usage: teammates who played for this team in its last 2 games but are not available this week
    # (listed Out/Doubtful, or off the active roster: IR, cut, traded). Usage = his last-3-games points.
    g = games.sort_values(["player_id", "t"]).copy()
    g["usage"] = g.groupby("player_id").pts.transform(lambda s: s.rolling(3, min_periods=1).mean())
    tw = df[["season", "week", "team", "t"]].drop_duplicates()
    tw = tw.merge(team_game_threshold(sched, 2).rename(columns={"nfl_team": "team"}), on=["season", "week", "team"])
    cand = tw.merge(g[["player_id", "team", "t", "player_position", "usage"]].rename(columns={"t": "t_game"}), on="team")
    cand = cand[(cand.t_game >= cand.active_since_t) & (cand.t_game < cand.t)]
    cand = cand.sort_values("t_game").drop_duplicates(["player_id", "season", "week"], keep="last")
    avail = df.loc[~unavailable, ["player_id", "season", "week", "team"]].assign(available=True)
    cand = cand.merge(avail, on=["player_id", "season", "week", "team"], how="left")
    gone = cand[cand.available.isna()].groupby(["season", "week", "team", "player_position"]).usage.sum()
    gone = gone.rename("vacated_pts").reset_index().rename(columns={"player_position": "position"})
    df = df.merge(gone, on=["season", "week", "team", "position"], how="left")
    df["vacated_pts"] = df.vacated_pts.fillna(0)

    # the expected starting QB this week = the QB who started the team's previous game if he is still available,
    # otherwise the available QB with the most recent usage. (Ranking by usage alone put a 1-game starter such
    # as Joe Flacco 2023 behind a teammate with more history and flagged a "QB change" in 21% of team-weeks.)
    # His passing ability goes to every teammate, plus a flag when he is not last game's starter.
    qbs = df[(df.position == "QB") & ~unavailable][["season", "week", "team", "t", "gsis_id", "team_pos_rank"]]
    qbs = asof(qbs, starters, by="team")
    qbs["is_last_starter"] = qbs.gsis_id == qbs.last_starter_gsis
    qbs = qbs.sort_values(["is_last_starter", "team_pos_rank"], ascending=[False, True])
    qb1 = qbs.drop_duplicates(["season", "week", "team"])[["season", "week", "team", "t", "gsis_id", "last_starter_gsis"]]
    qb1 = asof(qb1.rename(columns={"gsis_id": "qb1_gsis"}), qb.rename(columns={"gsis_id": "qb1_gsis"}), by="qb1_gsis")
    qb1["qb_change"] = (qb1.qb1_gsis != qb1.last_starter_gsis).astype(float).where(qb1.last_starter_gsis.notna())
    keep = ["season", "week", "team", "qb1_gsis", "qb_epa_db_l8", "qb_cpoe_l8", "qb_ppr_l8", "qb_change"]
    df = df.merge(qb1[keep], on=["season", "week", "team"], how="left")
    df["is_expected_qb"] = (df.gsis_id == df.pop("qb1_gsis")).astype(float).where(df.position == "QB")
    return df


TEAM_REL = ["team_plays_l8", "team_pass_rate_l8", "team_pass_epa_l8", "team_rush_epa_l8", "team_points_l8",
            "qb_epa_db_l8", "qb_cpoe_l8", "qb_ppr_l8"]
OPP_REL = ["opp_pass_epa_allowed_l8", "opp_rush_epa_allowed_l8", "opp_sack_rate_l8", "opp_points_allowed_l8",
           "opp_plays_allowed_l8"]


def relative_to_league(df: pd.DataFrame) -> pd.DataFrame:
    """Team, QB and defense values minus the league average of the same value that week. League pace and
    passing efficiency move between seasons (team_plays_l8 PSI 0.23, qb_cpoe_l8 0.17 in 2026), and a tree can't
    extrapolate an absolute level it never saw; relative values keep the meaning ("a top-5 pass defense") fixed.
    Every team's trailing value is known before the week's games, so this stays point-in-time."""
    for cols, key in ((TEAM_REL, "team"), (OPP_REL, "opponent")):
        per_team = df.drop_duplicates(["season", "week", key])
        league = per_team.groupby(["season", "week"])[cols].mean().add_suffix("__lg").reset_index()
        df = df.merge(league, on=["season", "week"], how="left")
        for c in cols:
            df[c.replace("_l8", "_rel_l8")] = df.pop(c) - df.pop(f"{c}__lg")
    lg = df.drop_duplicates(["season", "week", "opponent", "position"]).groupby(["season", "week", "position"]).opp_pts_allowed_l8.mean()
    df = df.merge(lg.rename("lg").reset_index(), on=["season", "week", "position"], how="left")
    df["opp_pts_allowed_rel_l8"] = df.pop("opp_pts_allowed_l8") - df.pop("lg")
    return df


def actuals() -> pd.DataFrame:
    a = pd.read_parquet(DATA / "sleeper_stats.parquet", columns=["player_id", "season", "week", "gp"] + list(SCORING))
    a[list(SCORING)] = a[list(SCORING)].fillna(0)
    a["act"] = score(a, "")
    a["played"] = a.gp.eq(1)
    return a[["player_id", "season", "week", "act", "played"]]


def sleeper_benchmark(rows: pd.DataFrame) -> pd.DataFrame:
    p = pd.read_parquet(DATA / "sleeper_proj.parquet", columns=["player_id", "season", "week"] + list(SCORING))
    p[list(SCORING)] = p[list(SCORING)].fillna(0)
    p["sleeper_proj"] = score(p, "")
    b = rows[["player_id", "season", "week"]].merge(p[["player_id", "season", "week", "sleeper_proj"]], how="left")
    b["sleeper_proj"] = b.sleeper_proj.fillna(0)  # no projection = Sleeper expects nothing
    return b


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    sched = _schedule_long()
    df = roster_rows().merge(sched, on=["season", "week", "nfl_team"], how="inner")  # bye weeks drop out
    df["t"] = time_index(df.season, df.week)
    df = df.rename(columns={"nfl_team": "team", "opp": "opponent"})
    df["age"] = (df.kickoff - pd.to_datetime(df.pop("birthdate"))).dt.days / 365.25
    df["years_exp"] = df.season - df.pop("draft_year")
    df = df.merge(_injuries(), on=["season", "week", "gsis_id"], how="left")

    games = played_games()
    df = asof(df, player_history(games), by="player_id")
    df["weeks_since_played"] = df.t - df.last_played_t
    df.loc[df.pop("hist_season") != df.season, ["pts_season_avg", "games_season"]] = np.nan
    df["games_season"] = df.games_season.fillna(0)

    prev = games.groupby(["player_id", "season"]).pts.agg(["mean", "size"]).reset_index()
    prev.columns = ["player_id", "season", "pts_prev_season", "games_prev_season"]
    df = df.merge(prev.assign(season=prev.season + 1), on=["player_id", "season"], how="left")
    df = asof(df, opportunity_history(), by="gsis_id")
    df = asof(df, defense_history(games), by=["opponent", "position"])
    df = asof(df, adjusted_defense(games), by=["opponent", "position"])
    tg = team_games()
    df = asof(df, offense_history(tg), by="team")
    df = asof(df, team_defense_history(tg), by="opponent")
    eff, qb, starters = player_stat_history()
    df = asof(df, eff, by="gsis_id")
    fp, sl = preseason_ranks(sleeper_ids())
    df = df.merge(fp, on=["season", "gsis_id"], how="left").merge(sl, on=["player_id", "season"], how="left")

    df = add_depth_features(df, games, qb, starters, sched)
    df = relative_to_league(df)

    # universe: recently active or preseason-ranked, and not ruled out
    thr = team_game_threshold(sched).rename(columns={"nfl_team": "team"})
    df = df.merge(thr, on=["season", "week", "team"], how="left")
    recent = df.last_played_t >= df.active_since_t.fillna(-np.inf)
    ranked = df.fp_ecr.notna() | df.sleeper_adp.notna()
    df = df[(recent | ranked) & (df.report_status != "Out")].drop(columns=["active_since_t", "last_played_t"])

    df = df.merge(actuals(), on=["player_id", "season", "week"], how="left")
    df["played"] = df.played.fillna(False).astype(bool)
    df["act"] = df.act.fillna(0)
    df = df.drop(columns=["t", "temp", "wind", "fantasypros_id"], errors="ignore")
    df = df.sort_values(["season", "week", "player_id"]).reset_index(drop=True)
    return df, sleeper_benchmark(df)


if __name__ == "__main__":
    df, bench = build()
    keep = df.season >= 2022
    df, bench = df[keep], bench[keep]
    df[df.season <= 2025].drop(columns="played").to_parquet(DATA / "player_games_train.parquet", index=False)
    df[df.season == 2026].drop(columns="played").to_parquet(DATA / "player_games_test.parquet", index=False)
    bench.assign(played=df.played.to_numpy()).to_parquet(DATA / "benchmark_sleeper.parquet", index=False)
    print(df.groupby("season").agg(rows=("act", "size"), played=("played", "mean")), df.shape)
