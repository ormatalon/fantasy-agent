"""Feature screening on the processed train matrix. Every function returns a report; nothing is
dropped automatically - the user (or the skill, after asking) decides what to remove.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from .base import level_str


def constant_features(X: pd.DataFrame, max_top_share: float = 0.995) -> pd.DataFrame:
    """Columns with one value, or where the top value covers more than ``max_top_share`` of rows."""
    rows = []
    for c in X.columns:
        vc = X[c].value_counts(dropna=False, normalize=True)
        top = float(vc.iloc[0]) if len(vc) else 1.0
        if len(vc) <= 1 or top > max_top_share:
            rows.append({"feature": c, "n_unique": int(X[c].nunique(dropna=False)), "top_share": round(top, 4)})
    return pd.DataFrame(rows, columns=["feature", "n_unique", "top_share"])


def duplicate_features(X: pd.DataFrame) -> pd.DataFrame:
    """Columns that are exact copies of an earlier column."""
    seen, rows = {}, []
    for c in X.columns:
        try:
            key = pd.util.hash_pandas_object(X[c], index=False).sum()
        except TypeError:
            key = hash(tuple(X[c].astype(str)))
        if key in seen and X[c].equals(X[seen[key]]):
            rows.append({"feature": c, "duplicate_of": seen[key]})
        else:
            seen.setdefault(key, c)
    return pd.DataFrame(rows, columns=["feature", "duplicate_of"])


def correlated_pairs(X: pd.DataFrame, threshold: float = 0.95, method: str = "spearman", max_cols: int = 2000) -> pd.DataFrame:
    """Numeric pairs with |corr| above threshold."""
    num = X.select_dtypes(include=[np.number])
    num = num.loc[:, num.nunique() > 1].iloc[:, :max_cols]
    if num.shape[1] < 2:
        return pd.DataFrame(columns=["feature_a", "feature_b", "corr"])
    c = num.corr(method=method).abs()
    iu = np.triu_indices_from(c, k=1)
    vals = c.to_numpy()[iu]
    m = vals > threshold
    return (pd.DataFrame({"feature_a": c.columns[iu[0][m]], "feature_b": c.columns[iu[1][m]], "corr": vals[m]})
            .sort_values("corr", ascending=False).reset_index(drop=True))


def _oof_level_mean(x: pd.Series, y: pd.Series, n_folds=5, seed=42) -> pd.Series:
    """Out-of-fold mean of y per level of x (so a unique ID scores ~0.5, not a perfect 1.0)."""
    from sklearn.model_selection import KFold
    lv = pd.Series([level_str(v) or "__MISSING__" for v in x.to_numpy(dtype=object)], index=x.index)
    out = pd.Series(np.nan, index=x.index)
    prior = y.mean()
    for tr, va in KFold(n_folds, shuffle=True, random_state=seed).split(x):
        m = y.iloc[tr].groupby(lv.iloc[tr].to_numpy()).mean()
        out.iloc[va] = lv.iloc[va].map(m).fillna(prior).to_numpy()
    return out


def leakage_scan(X: pd.DataFrame, y, task: str, auc_threshold: float = 0.95, corr_threshold: float = 0.95) -> pd.DataFrame:
    """Single-feature strength against the target. Very strong single features are leakage suspects -
    especially engineered ones (aggregates, target encodings, dates computed after the event).

    Numeric columns are scored as they are. Non-numeric columns (native categoricals, text) are scored
    through an out-of-fold mean of the target per value.
    binary      -> AUC (max(auc, 1-auc)); NaN filled with the median for scoring only
    multiclass  -> mean one-vs-rest AUC
    regression / count -> |Spearman|
    """
    y = pd.Series(np.asarray(y), index=X.index)
    ok = y.notna()
    rows = []
    for c in X.columns:
        x = X[c][ok]
        yy = y[ok]
        numeric = pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x)
        if x.nunique(dropna=False) < 2:
            continue
        if task == "multiclass":
            aucs = []
            for k in yy.unique():
                yk = (yy == k).astype(int)
                if yk.nunique() < 2:
                    continue
                s_ = x.fillna(x.median()) if numeric else _oof_level_mean(x, yk.astype(float))
                a = roc_auc_score(yk, s_)
                aucs.append(max(a, 1 - a))
            score, measure = (float(np.mean(aucs)) if aucs else np.nan), "mean_ovr_auc"
            flag = score > auc_threshold
        else:
            yv = yy.astype(float)
            s_ = x.fillna(x.median()) if numeric else _oof_level_mean(x, yv)
            if task == "binary":
                a = roc_auc_score(yv, s_)
                score, measure = max(a, 1 - a), "auc"
                flag = score > auc_threshold
            else:
                score, measure = abs(spearmanr(s_, yv).statistic), "abs_spearman"
                flag = score > corr_threshold
        rows.append({"feature": c, "kind": "numeric" if numeric else "categorical/text (oof level mean)",
                     "measure": measure, "score": round(float(score), 4), "suspect": bool(flag)})
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)


def null_importance(model_fn, X: pd.DataFrame, y, n_runs: int = 10, seed: int = 42) -> pd.DataFrame:
    """Null-importance test: compare each feature's real importance with its importance when the target
    is shuffled. ``model_fn()`` must return an unfitted model exposing ``feature_importances_``.
    Features whose real importance does not beat the 75th percentile of the null runs are weak."""
    rng = np.random.default_rng(seed)
    real = model_fn().fit(X, y).feature_importances_
    nulls = np.vstack([model_fn().fit(X, rng.permutation(np.asarray(y))).feature_importances_ for _ in range(n_runs)])
    p75 = np.percentile(nulls, 75, axis=0)
    return (pd.DataFrame({"feature": X.columns, "importance": real, "null_p75": p75,
                          "beats_null": real > p75})
            .sort_values("importance", ascending=False).reset_index(drop=True))


def screening_report(X: pd.DataFrame, y=None, task: str | None = None, corr_threshold: float = 0.95) -> dict:
    """Run the cheap checks together and return a dict of tables."""
    rep = {"constant": constant_features(X), "duplicates": duplicate_features(X),
           "correlated": correlated_pairs(X, corr_threshold)}
    if y is not None and task in ("binary", "multiclass", "regression", "count"):
        rep["leakage"] = leakage_scan(X, y, task)
    return rep
