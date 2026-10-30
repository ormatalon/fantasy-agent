# Processing: player-game dataset

Notebook: `processing_player_games.ipynb`. Configuration: `processing_player_games.py` (uses `../dsprep`).
Record: `processing_record.yml`. Feature list: `feature_dictionary_gbm.csv`.

## What was done

- **No weekly projection from any platform is a feature.** The notebook fails on an assert if one appears. Sleeper's weekly projection is read only from `../data/benchmark_sleeper.parquet` for scoring.
- **Cleaning:** the EDA decisions (`../02_eda/eda_decisions.yml`) are re-applied to both raw files. 26 whitespace practice tags and 4 invalid snap shares become NaN. 8 columns are dropped (`kickoff`, identifiers, `team`, `opponent`, 2 redundant target counts, and the drifting `qb_cpoe_rel_l8`). No rows are dropped.
- **Overrides of the EDA:** `season` is not a feature (it's kept for splitting and evaluation), and the target stays untransformed.
- **Split by time:** train 2022-2024 (23,480 rows), val 2025 (7,789), test 2026 weeks 1-7 (3,069).
- **One feature set, `gbm` (71 columns):**
  - 62 numeric with NaN kept: history, ordinal injury levels, next man up, efficiency, opponent defense strength, team offense and the expected QB's passing ability.
  - 5 missing indicators: no history, no last season, no expected-points history, unranked, undrafted.
  - 4 native `category` columns, with "Doubtful" kept as its own level.
  - Categories aligned across splits (`align_categories`).
- **Screening:** nothing constant or duplicated, and no leakage suspect (max |Spearman| 0.63).
- **Validation:** frames are consistent. Drift appears only where expected (the test covers weeks 1-7). On 2025, an untuned HistGradientBoosting gets MAE 3.63 against Sleeper's 3.24 on the same rows.

## Load

```python
import pandas as pd
X_train = pd.read_parquet("data/processed/X_train_gbm.parquet")   # category dtypes preserved
y_train = pd.read_parquet("data/processed/y_train.parquet")["act"]
ev = pd.read_parquet("data/processed/eval_train.parquet")          # player_id, season, week, position, act
bench = pd.read_parquet("data/benchmark_sleeper.parquet")          # scoring only: sleeper_proj, played
cat_features = ["position", "roof", "report_status", "practice_status"]
```
