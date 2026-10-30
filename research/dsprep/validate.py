"""Checks on the processed matrices before they go to modeling."""
from __future__ import annotations

import numpy as np
import pandas as pd


def check_frames(frames: dict[str, pd.DataFrame], cat_features=(), text_features=()) -> pd.DataFrame:
    """Same columns and dtypes in every split, NaN / inf counts, object columns that are not declared
    categorical or text. Returns one row per issue (empty frame = all good)."""
    issues = []
    names = list(frames)
    ref = frames[names[0]]
    declared = set(cat_features) | set(text_features)
    for n in names[1:]:
        f = frames[n]
        if list(f.columns) != list(ref.columns):
            miss = set(ref.columns) - set(f.columns)
            extra = set(f.columns) - set(ref.columns)
            issues.append({"split": n, "issue": "column mismatch", "detail": f"missing {sorted(miss)[:10]}, extra {sorted(extra)[:10]}"})
        else:
            diff = [c for c in ref.columns if str(ref[c].dtype) != str(f[c].dtype)]
            if diff:
                issues.append({"split": n, "issue": "dtype mismatch", "detail": str(diff[:10])})
    for n, f in frames.items():
        num = f.select_dtypes(include=[np.number])
        n_inf = int(np.isinf(num.to_numpy(dtype=float, na_value=np.nan)).sum()) if num.shape[1] else 0
        if n_inf:
            issues.append({"split": n, "issue": "inf values", "detail": n_inf})
        nan_cols = f.columns[f.isna().any()].tolist()
        if nan_cols:
            issues.append({"split": n, "issue": "NaN present", "detail": f"{len(nan_cols)} columns, e.g. {nan_cols[:5]} "
                                                                       "(fine for GBMs, impute for linear/NN)"})
        obj = [c for c in f.columns if (f[c].dtype == object or str(f[c].dtype) in ("string", "category")) and c not in declared]
        if obj:
            issues.append({"split": n, "issue": "undeclared non-numeric columns", "detail": obj[:10]})
    return pd.DataFrame(issues, columns=["split", "issue", "detail"])


def psi(a: pd.Series, b: pd.Series, bins: int = 10) -> float:
    """Population stability index of b against a (a = train). Numeric: quantile bins of a; else levels."""
    a, b = a.dropna(), b.dropna()
    if len(a) == 0 or len(b) == 0:
        return np.nan
    if pd.api.types.is_numeric_dtype(a) and a.nunique() > bins:
        edges = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        pa = pd.cut(a, edges).value_counts(normalize=True, sort=False)
        pb = pd.cut(b, edges).value_counts(normalize=True, sort=False)
    else:
        pa = a.astype(str).value_counts(normalize=True)
        pb = b.astype(str).value_counts(normalize=True).reindex(pa.index.union(b.astype(str).unique()), fill_value=0)
        pa = pa.reindex(pb.index, fill_value=0)
    pa, pb = pa.to_numpy() + 1e-6, pb.to_numpy() + 1e-6
    return float(np.sum((pb - pa) * np.log(pb / pa)))


def drift_report(train: pd.DataFrame, other: pd.DataFrame, top: int | None = None, exclude=()) -> pd.DataFrame:
    """PSI per feature (flag > 0.1 moderate, > 0.25 major).
    Pass ``exclude=fp.target_encoded_features()``: out-of-fold train values and full-fit test values of a
    target encoding differ by design, so their PSI is inflated and not a real drift signal."""
    rows = [{"feature": c, "psi": psi(train[c], other[c])} for c in train.columns
            if c in other.columns and c not in set(exclude)]
    df = pd.DataFrame(rows).sort_values("psi", ascending=False).reset_index(drop=True)
    df["flag"] = pd.cut(df["psi"], [-np.inf, 0.1, 0.25, np.inf], labels=["ok", "moderate", "major"])
    return df.head(top) if top else df


def baseline_check(X_train, y_train, X_eval, y_eval, task: str, seed: int = 42) -> pd.DataFrame:
    """Dummy baseline vs. a quick HistGradientBoosting model on the processed features.
    Object / category columns are ordinal-encoded inside this check only. A sanity check, not a model:
    the model should beat the dummy clearly; a near-perfect score points to leakage."""
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.metrics import (average_precision_score, f1_score, mean_absolute_error, r2_score,
                                 roc_auc_score)
    from sklearn.preprocessing import OrdinalEncoder

    def prep(Xtr, Xev):
        Xtr, Xev = Xtr.copy(), Xev.copy()
        obj = [c for c in Xtr.columns if not pd.api.types.is_numeric_dtype(Xtr[c])]
        if obj:
            enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1)
            Xtr[obj] = enc.fit_transform(Xtr[obj].astype(str))
            Xev[obj] = enc.transform(Xev[obj].astype(str))
        return Xtr.astype(float), Xev.astype(float)

    Xtr, Xev = prep(X_train, X_eval)
    rows = []
    if task == "binary":
        for name, m in (("dummy", DummyClassifier(strategy="prior")),
                        ("hist_gb", HistGradientBoostingClassifier(random_state=seed))):
            p = m.fit(Xtr, y_train).predict_proba(Xev)[:, 1]
            rows.append({"model": name, "roc_auc": roc_auc_score(y_eval, p), "pr_auc": average_precision_score(y_eval, p)})
    elif task == "multiclass":
        for name, m in (("dummy", DummyClassifier(strategy="most_frequent")),
                        ("hist_gb", HistGradientBoostingClassifier(random_state=seed))):
            pred = m.fit(Xtr, y_train).predict(Xev)
            rows.append({"model": name, "macro_f1": f1_score(y_eval, pred, average="macro"),
                         "accuracy": float(np.mean(np.asarray(pred) == np.asarray(y_eval)))})
    elif task == "multilabel":
        from sklearn.multioutput import MultiOutputClassifier
        for name, m in (("dummy", MultiOutputClassifier(DummyClassifier(strategy="most_frequent"))),
                        ("hist_gb", MultiOutputClassifier(HistGradientBoostingClassifier(random_state=seed)))):
            pred = m.fit(Xtr, y_train).predict(Xev)
            rows.append({"model": name, "micro_f1": f1_score(y_eval, pred, average="micro", zero_division=0),
                         "macro_f1": f1_score(y_eval, pred, average="macro", zero_division=0)})
    elif task in ("regression", "count"):
        for name, m in (("dummy", DummyRegressor(strategy="median")),
                        ("hist_gb", HistGradientBoostingRegressor(random_state=seed))):
            pred = m.fit(Xtr, y_train).predict(Xev)
            rows.append({"model": name, "mae": mean_absolute_error(y_eval, pred), "r2": r2_score(y_eval, pred)})
    else:
        raise ValueError(f"baseline_check does not support task '{task}'")
    return pd.DataFrame(rows)
