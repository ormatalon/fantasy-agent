"""Feature processing for the player-game dataset (configures the bundled dsprep library).

One feature set, `gbm` (LightGBM: numeric left as is with NaN, native categoricals). No weekly projection
from any platform is an input; Sleeper's weekly projection is read only by the evaluation, from
data/benchmark_sleeper.parquet. Specs start from `dp.suggest_specs(dec, "gbm")` with two changes: `season`
is not a feature (no trend, and a tree can't extrapolate an unseen season), and categoricals stay native.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # research/ holds dsprep
import dsprep as dp
import pandas as pd

NUMERIC = {
    "team_spread": None, "total_line": None, "implied_total": None, "home": None, "div_game": None, "rest": None,
    "week": None, "age": None, "years_exp": None,
    "pts_l3": None, "snap_share_l3": None, "tgt_share_l3": None, "rush_att_l3": None, "pass_att_l3": None,
    "rz_opps_l3": None, "rec_air_yd_l3": None,
    "pts_l8": {"missing_indicator": True},  # flags the whole history block (debuts)
    "snap_share_l8": None, "tgt_share_l8": None, "rush_att_l8": None, "pass_att_l8": None, "rz_opps_l8": None,
    "rec_air_yd_l8": None, "pts_std_l8": None, "pts_max_l8": None,
    "pts_season_avg": None, "games_season": None,  # games_season == 0 already marks a missing season average
    "weeks_since_played": None,
    "pts_prev_season": {"missing_indicator": True}, "games_prev_season": None,
    "xfp_l3": None, "xfp_l8": {"missing_indicator": True}, "fp_over_xfp_l8": None,
    # player efficiency (nflverse weekly stats, last 8 games)
    "wopr_l8": None, "air_yards_share_l8": None, "rec_epa_tgt_l8": None, "rush_epa_carry_l8": None,
    # opponent defense, relative to the league that week; schedule-adjusted points allowed by position
    "opp_pts_allowed_rel_l8": None, "opp_pos_over_avg_l8": None, "opp_pass_epa_allowed_rel_l8": None,
    "opp_rush_epa_allowed_rel_l8": None, "opp_sack_rate_rel_l8": None, "opp_points_allowed_rel_l8": None,
    "opp_plays_allowed_rel_l8": None,
    # own team offense and the expected starting QB, relative to the league that week
    "team_plays_rel_l8": None, "team_pass_rate_rel_l8": None, "team_pass_epa_rel_l8": None,
    "team_rush_epa_rel_l8": None, "team_points_rel_l8": None,
    "qb_epa_db_rel_l8": None, "qb_ppr_rel_l8": None, "qb_change": None, "is_expected_qb": None,
    # yearly preseason rankings, frozen at week 1; missing = unranked / undrafted
    "fp_ecr": {"missing_indicator": True}, "fp_pos_rank": None,
    "sleeper_adp": {"missing_indicator": True}, "sleeper_pos_adp": None,
    # next man up: usage rank/share among available teammates; recent points of teammates who are unavailable
    # this week (Out, Doubtful, IR, cut, traded) (build_dataset)
    "team_pos_rank": None, "team_pos_share": None, "vacated_pts": None,
    # ordinal encodings of the injury report (add_domain_features)
    "injury_level": None, "practice_level": None,
}
INJURY_LEVEL = {"Questionable": 1, "Doubtful": 2}
PRACTICE_LEVEL = {"Limited Participation in Practice": 1, "Did Not Participate In Practice": 2}
# Doubtful is rare (0.6%) but almost never plays: no rare-level grouping
CATEGORICAL = {"position": None, "roof": None, "report_status": None, "practice_status": None}
# Kept next to X for evaluation, never fed to the model
EVAL_COLUMNS = ["player_id", "season", "week", "position"]


def add_domain_features(df):
    """Ordinal versions of the injury report. The categorical columns alone left "Doubtful" (0.6% of rows,
    0.5% of them play) too rare for the trees to isolate: the first model predicted 4.0 points for Doubtful
    players who scored 0.0. A severity number lets one threshold split cover it."""
    df = df.copy()
    df["injury_level"] = df.report_status.map(INJURY_LEVEL).fillna(0)
    df["practice_level"] = df.practice_status.map(PRACTICE_LEVEL).fillna(0)
    return df


def align_categories(X, X_train):
    """Give every category column the train levels. The processor sets each frame's categories from the
    levels present in that frame, so codes differ between splits and a concat silently turns the columns
    into strings. Levels unseen in train become NaN."""
    X = X.copy()
    for c in X_train.select_dtypes("category").columns:
        X[c] = pd.Categorical(X[c].astype(object), categories=X_train[c].cat.categories)
    return X


def build_feature_processor(feature_set: str = "gbm") -> dp.FeatureProcessor:
    """Fresh, unfitted processor; the training step refits it inside each CV fold."""
    if feature_set != "gbm":
        raise ValueError(feature_set)
    steps = {"num": dp.NumericProcessor(NUMERIC),
             "cat": dp.CategoricalProcessor(CATEGORICAL, method="native", native_dtype="category", group_rare=False)}
    return dp.FeatureProcessor(steps=steps, task="regression")


if __name__ == "__main__":  # usage example
    # dec = dp.load_decisions("../02_eda/eda_decisions.yml")
    # train, _ = dp.apply_cleaning(train_raw, dec)
    # fp = build_feature_processor("gbm"); X_train = fp.fit_transform(train, train.act)
    # X_test = align_categories(fp.transform(test), X_train)
    pass
