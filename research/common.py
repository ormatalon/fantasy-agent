"""Shared loader: one row per (player, season, week) that Sleeper projected above 0.

Joins Sleeper's projection to the actual result, the game's Vegas lines and
weather, and that week's official injury report. Every column here is known
before kickoff except the `act_*` columns, `played`, `off_snp` and `tm_off_snp`.

Pitfall handled: Sleeper's `player_*` columns are today's metadata copied onto
every historical row, so only `player_position` (rarely changes) and the name
are kept; team/opponent come from the row itself and injuries from nflverse.
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
POSITIONS = ["QB", "RB", "WR", "TE"]
COMPONENTS = ["pass_att", "pass_yd", "pass_td", "pass_int", "pass_2pt", "rush_att", "rush_yd", "rush_td",
              "rush_2pt", "rec_tgt", "rec", "rec_yd", "rec_td", "rec_2pt", "fum_lost"]
# The league's offensive scoring (data/fantasy.db), which is standard full PPR. Both projected and actual
# points are scored from their stat lines with it: Sleeper's stored projected `pts_ppr` for QBs in
# 2021-2025 is ~2 points above its own stat line (see 01_sleeper_vs_actual.ipynb), while actuals match.
SCORING = {"pass_yd": .04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2, "rush_yd": .1, "rush_td": 6, "rush_2pt": 2,
           "rec": 1, "rec_yd": .1, "rec_td": 6, "rec_2pt": 2, "fum_lost": -2}
# Sleeper uses LAR, nflverse uses LA.
TO_NFLVERSE = {"LAR": "LA"}


def _schedule_long() -> pd.DataFrame:
    s = pd.read_parquet(DATA / "schedules.parquet")
    s = s[s.game_type == "REG"]
    s = s[s.result.notna()]  # drops games never completed (BUF-CIN 2022 week 17)
    cols = ["season", "week", "gameday", "gametime", "total_line", "roof", "temp", "wind", "div_game"]
    home = s[cols + ["home_team", "away_team", "spread_line", "home_rest"]].rename(
        columns={"home_team": "nfl_team", "away_team": "opp", "home_rest": "rest"})
    home["home"] = 1
    away = s[cols + ["away_team", "home_team", "spread_line", "away_rest"]].rename(
        columns={"away_team": "nfl_team", "home_team": "opp", "away_rest": "rest"})
    away["home"] = 0
    away["spread_line"] = -away["spread_line"]
    long = pd.concat([home, away], ignore_index=True)
    # nflverse spread_line > 0 means the team is favored by that many points.
    long["team_spread"] = long.pop("spread_line")
    long["implied_total"] = (long.total_line + long.team_spread) / 2
    long["kickoff"] = pd.to_datetime(long.gameday + " " + long.gametime)
    return long.drop(columns=["gameday", "gametime"])


def _injuries() -> pd.DataFrame:
    inj = pd.read_parquet(DATA / "injuries.parquet")
    inj = inj[inj.game_type == "REG"].drop_duplicates(["season", "week", "gsis_id"], keep="last")
    return inj[["season", "week", "gsis_id", "report_status", "practice_status"]]


def load_frame(seasons=range(2021, 2027)) -> pd.DataFrame:
    proj = pd.read_parquet(DATA / "sleeper_proj.parquet")
    proj = proj[proj.season.isin(seasons) & proj.player_position.isin(POSITIONS)]
    proj = proj[["player_id", "season", "week", "team", "opponent", "player_position", "player_first_name",
                 "player_last_name"] + COMPONENTS].rename(
        columns={"player_position": "position"} | {c: f"proj_{c}" for c in COMPONENTS})
    proj["name"] = proj.pop("player_first_name") + " " + proj.pop("player_last_name")
    proj[[f"proj_{c}" for c in COMPONENTS]] = proj[[f"proj_{c}" for c in COMPONENTS]].fillna(0)
    proj["proj"] = score(proj, "proj_")
    proj = proj[proj.proj > 0]

    act = pd.read_parquet(DATA / "sleeper_stats.parquet")
    act = act[["player_id", "season", "week", "gp", "off_snp", "tm_off_snp"] + COMPONENTS].rename(
        columns={c: f"act_{c}" for c in COMPONENTS})
    df = proj.merge(act, on=["player_id", "season", "week"], how="left")
    # Sleeper omits zero stats; a missing stats row means the player did not take the field.
    df["played"] = df.pop("gp").eq(1)
    df[[f"act_{c}" for c in COMPONENTS]] = df[[f"act_{c}" for c in COMPONENTS]].fillna(0)
    df["act"] = score(df, "act_")
    df["off_snp"] = df.off_snp.fillna(0)

    df["nfl_team"] = df.team.replace(TO_NFLVERSE)
    df = df.merge(_schedule_long(), on=["season", "week", "nfl_team"], how="inner")

    ids = pd.read_parquet(DATA / "playerids.parquet")[["sleeper_id", "gsis_id", "draft_year", "birthdate"]]
    ids = ids.dropna(subset=["sleeper_id"]).assign(sleeper_id=lambda d: d.sleeper_id.astype(int).astype(str))
    ids = ids.drop_duplicates("sleeper_id")
    df = df.merge(ids, left_on="player_id", right_on="sleeper_id", how="left").drop(columns="sleeper_id")
    df["draft_year"] = df.draft_year.replace(0, np.nan)  # 0 is nflverse's "unknown"
    df["years_exp"] = df.season - df.draft_year
    df["age"] = (df.kickoff - pd.to_datetime(df.birthdate)).dt.days / 365.25
    df = df.drop(columns="birthdate")

    df = df.merge(_injuries(), on=["season", "week", "gsis_id"], how="left")
    df["report_status"] = df.report_status.fillna("None")
    df["resid"] = df.proj - df.act
    return df.sort_values(["season", "week", "player_id"]).reset_index(drop=True)


def score(df: pd.DataFrame, prefix: str) -> pd.Series:
    return sum(w * df[prefix + k] for k, w in SCORING.items())


def metrics(proj: pd.Series, act: pd.Series) -> pd.Series:
    err = proj - act
    return pd.Series({"n": len(err), "bias": err.mean(), "mae": err.abs().mean(),
                      "rmse": np.sqrt((err ** 2).mean()), "r2": 1 - (err ** 2).sum() / ((act - act.mean()) ** 2).sum()})
