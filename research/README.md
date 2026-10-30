# Research: Sleeper projections and an independent model

A separate uv project (its own `pyproject.toml`, so the app's pinned dependencies are untouched).

```
cd research
uv sync
uv run python fetch_data.py      # Sleeper projections + actuals, nflverse schedules/injuries/ids/rosters/rankings/ff_opportunity -> data/
uv run python build_dataset.py   # point-in-time player-game features + the Sleeper benchmark file
```

Then run the notebooks in order. `data/`, `models/` and MLflow files (`mlflow.db`, `mlruns/`) are gitignored.

| Step | Where | Question |
|---|---|---|
| 1 | `01_sleeper_vs_actual.ipynb` | How accurate is Sleeper (2022-2025), and where is it biased? |
| 2a | `02_eda/` (notebook, README, `eda_decisions.yml`) | EDA of the player-game dataset (`DATA_DICTIONARY.md`) |
| 2b | `02_processing/` (notebook, module, README, record) | Cleaning, time split, features for LightGBM (`dsprep`) |
| 3 | `03_model.ipynb` | Can an independent model beat Sleeper? (MLflow experiment `fantasy-points-independent`) |

**Rule: no weekly (per-game) projection from any platform is a model input.** Yearly preseason rankings are
allowed, frozen at week 1 (FantasyPros' last preseason PPR consensus, Sleeper's week-1 ADP). Sleeper's weekly
projection is kept in `data/benchmark_sleeper.parquet` and read only to score the benchmark. Rows are defined
from facts (on the roster, recently active or preseason-ranked, not listed Out), not from Sleeper's projection.

## Results

- **Sleeper:**
  - Strong: MAE 4.09 on the players it projects, 8% better than a season-average baseline.
  - Small recurring biases: week 1 (+1.0 points), Questionable (+0.8), wind 15+ mph (+0.5), long rest (+0.4), big underdogs, away and outdoor games, top QB projections.
  - No player-specific bias.
- **Data pitfalls found:**
  - Sleeper's stored QB `pts_ppr` is about 2 points off its own stat line (2021-2025).
  - Sleeper's 2021 projections and its stored *season* projections are end-of-season versions (survivorship, already reflecting injuries). The season projections are not used.
  - Sleeper's in-season ADP is updated weekly, so only week 1 is used.
  - nflverse weather is filled post-game.
  - Playoff weeks in ff_opportunity collided with the next season in the time index (fixed).
- **Independent model** (average of a LightGBM Tweedie and a hurdle model, 71 features):
  - 13-16% better than a naive baseline.
  - **3.4% worse than Sleeper on RMSE** on the 2026 weeks 1-7 holdout (5.313 against 5.137; 2.3% on players who played).
  - **Better on close start/sit calls (0.562 against 0.554)**; about a tie in CV.
  - Yearly preseason ranks and next man up (who starts, who's out) carry the model.
  - Opponent defense strength and the QB's passing ability are real but small signals (about 0.005 RMSE each), because a player's own history already contains most of them.
  - The remaining gap is game-day availability (Questionable decisions, inactives) and QB benchings, which Sleeper's final snapshot knows and a pre-game fact feed doesn't.
