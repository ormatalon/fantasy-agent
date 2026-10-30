# EDA: player-game fantasy points

Notebook: `eda_player_games.ipynb`. Handoff for data processing: `eda_decisions.yml`.

## Problem

Fantasy managers in a full-PPR Sleeper league choose which QB/RB/WR/TE to start each week. The goal is an
independent model that predicts a player's actual fantasy points in a game (`act`, continuous) more accurately
than Sleeper's weekly projection. **No weekly (per-game) projection from any platform is a feature.** Yearly
preseason rankings are allowed. Sleeper's weekly projection is only in `data/benchmark_sleeper.parquet` and
isn't read here.

One row = one player in one regular-season week, if he was on the 53-man roster, recently active or
preseason-ranked, and not listed Out. Key: `player_id, season, week`. Every feature is known before kickoff.

## Dataset

| | rows | seasons |
|---|---|---|
| train (`data/player_games_train.parquet`) | 31,269 | 2022-2025, weeks 1-18 |
| test (`data/player_games_test.parquet`) | 3,069 | 2026, weeks 1-7 |

75 columns: 54 continuous, 10 low-cardinality numeric, 6 categorical, 3 identifiers, 1 datetime, the target.
1,012 players. Definitions are in `../DATA_DICTIONARY.md`.

## Key findings

- **Target:** mean 5.80, median 2.30, **37.2% zeros** (about 13% of rows are players who don't play), 290 valid negatives. No trend across seasons.
- **Availability signals:**
  - Doubtful players play 0.5% of the time and Questionable 68%, against 89% for players not listed.
  - The zero share rises from 30% to 74% as `weeks_since_played` goes from 1 to 4.
- **Best predictors (Spearman):**
  - `pts_season_avg` 0.67, `pts_l8` 0.63.
  - Preseason ranks: `sleeper_adp` -0.59, `fp_ecr` -0.58.
  - `xfp_l8` 0.58, second-highest mutual information.
  - `report_status` epsilon-squared 0.082.
- **Role this week:** `is_expected_qb` (last game's starter, if available) has Spearman +0.74 and matches the real starter 89.8% of the time. `team_pos_share` is +0.57.
- **Beyond the player himself** (partial Spearman given `pts_l8`, within position):
  - Role (`team_pos_share` +0.17 to +0.37).
  - WOPR (+0.08 WR, +0.10 TE).
  - Vacated teammate usage (+0.05 to +0.08).
  - Receiving efficiency regresses (-0.06).
  - **Defense strength is small but consistent at every position** (+0.02 to +0.05).
  - **The QB's passing ability adds almost nothing for receivers** once their own points are known, because the same QB is in their history.
- **Simpson's paradox:** `pass_att_l8` is 0.12 pooled but 0.54 for QBs. `tgt_share_l8` is 0.41 pooled but 0.62-0.65 for RB/WR/TE. Position has to interact with the volume features.
- **Missingness carries information:** unranked or undrafted players (1.4 / 2.3 points against about 7), no history (1.6 against 6.0), no expected-points history (1.0 against 6.1).
- **Train/test:** time order is clean. With game-grouped folds, 2025 vs 2026 adversarial AUC is 0.756, below every training-season pair (0.81-0.86), so the test set is representative. Team, QB and defense values are league-relative, which removed season drift (`team_plays` PSI 0.23 to 0.12). `qb_cpoe_rel_l8` still drifts (PSI 0.21) and is dropped.

## Data quality actions

| step | column | rows | action | status |
|---|---|---|---|---|
| null tokens | `practice_status` (whitespace) | 26 | NaN | applied |
| invalid value | `snap_share_l3` > 1 | 4 | set NaN | recommended |

No outliers are removed. A first rule flagging negative `xfp_l8` was wrong (expected interceptions and fumbles make small negatives valid), so it was corrected.

## Leakage verdict

- **No weekly projection is in the data.**
- The yearly ranks are true preseason snapshots, one value per player-season.
- All history, ff_opportunity, defense, team-stats and efficiency features were re-audited by recomputation (0 mismatches in every sample).
- A first expected-QB rule (rank by usage) flagged a false "QB change" in 21% of team-weeks against the true 5.7%. It was fixed to "last game's starter, if available".
- The audit caught and fixed one bug: playoff weeks colliding with the next season in the time index.
- `kickoff` is dropped.

## Modeling recommendations

- **Drop:** `kickoff`, `name`, `gsis_id`, `team`, `opponent`, `rec_tgt_l3`, `rec_tgt_l8`, `qb_cpoe_rel_l8`.
- **Missing indicators:** `pts_l8` (no history), `pts_prev_season`, `xfp_l8`, `fp_ecr`, `sleeper_adp`. Not for `pts_season_avg`, because `games_season == 0` already says it.
- **Injury fields:** `report_status`/`practice_status` keep a "not listed" level. **Doubtful is not grouped as rare.**
- **Target:** no transform. Squared-error loss, plus Tweedie as an experiment for the zero mass.
- **Model:** a tree model with `position`.
- **Validation:** an expanding season split (validate on 2024 and 2025), 2026 weeks 1-7 as the holdout. **RMSE primary**, then MAE, within-week Spearman, bias and played-only RMSE. Sleeper is scored from the benchmark file on the same rows.

## Figures

- [target_distribution](figures/target_distribution.png)
- [missingness](figures/missingness.png)
- [feature_correlations](figures/feature_correlations.png)
- [feature_target_ranking](figures/feature_target_ranking.png)
- [binned_means_top_features](figures/binned_means_top_features.png)
- [simpson_check](figures/simpson_check.png)
- [matchup_partial_correlations](figures/matchup_partial_correlations.png)
- [train_test_shift](figures/train_test_shift.png)
