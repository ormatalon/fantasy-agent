"""Flexible splitting. Every scheme is optional - ``how="none"`` returns the whole frame as "train".

make_split(df, how=..., sizes=...)  ->  {"train": df, "val": df, "test": df}  (keys depend on sizes)
  how: "none" | "random" | "stratified" | "group" | "time" | "group_stratified"
  sizes: (train, test) or (train, val, test) fractions, e.g. (0.8, 0.2) or (0.7, 0.15, 0.15)
  For "time", the latest rows go to val/test; ``cutoffs=("2024-01-01", "2024-07-01")`` uses fixed dates instead.

  Stratifying on a numeric target with many values bins it into ``stratify_bins`` quantiles first.
  Group splits are sized by rows (not by number of groups), as close to ``sizes`` as the groups allow.

make_cv_folds(df, how=..., n_splits=5) -> fold id per row. Not used by the processing skill (CV belongs
  to the modeling step, where the feature processors are refit inside each fold); kept here so both
  steps share one implementation.

separate_unlabeled(df, target) -> (labeled, unlabeled) for rows with a missing target.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.model_selection import (GroupKFold, GroupShuffleSplit, KFold, StratifiedGroupKFold,
                                     StratifiedKFold, TimeSeriesSplit, train_test_split)

NAMES = {2: ("train", "test"), 3: ("train", "val", "test")}


def _strat_key(df, col, bins):
    """Stratification labels: the column itself, or quantile bins of a numeric column with many values."""
    y = df[col]
    if pd.api.types.is_numeric_dtype(y) and y.nunique() > bins:
        return pd.qcut(y.rank(method="first"), q=bins, labels=False).astype(str).to_numpy()
    return y.astype(str).to_numpy()


def _group_two_way(df, group_col, frac_test, seed):
    """Assign whole groups to the test part until its row share reaches ``frac_test``.
    Groups are visited in random order; a group that would overshoot the target is skipped."""
    sizes = df.groupby(group_col, dropna=False, sort=False).size()
    order = np.random.default_rng(seed).permutation(len(sizes))
    target = frac_test * len(df)
    test_groups, n_test = [], 0
    for i in order:
        g, k = sizes.index[i], sizes.iloc[i]
        if n_test + k <= target or n_test == 0 and k <= 2 * target:
            test_groups.append(g)
            n_test += k
        if n_test >= target:
            break
    m = df[group_col].isin(test_groups).to_numpy()
    return df[~m], df[m]


def _two_way(df, how, frac_test, stratify_col, group_col, time_col, seed, bins):
    if how == "random":
        return train_test_split(df, test_size=frac_test, random_state=seed)
    if how == "stratified":
        return train_test_split(df, test_size=frac_test, random_state=seed,
                                stratify=_strat_key(df, stratify_col, bins))
    if how == "group":
        return _group_two_way(df, group_col, frac_test, seed)
    if how == "group_stratified":
        k = max(2, int(round(1 / frac_test)))
        sgk = StratifiedGroupKFold(n_splits=k, shuffle=True, random_state=seed)
        tr, te = next(sgk.split(df, _strat_key(df, stratify_col, bins), groups=df[group_col].astype(str)))
        return df.iloc[tr], df.iloc[te]
    if how == "time":
        d = df.sort_values(time_col, kind="mergesort")
        n_test = int(round(len(d) * frac_test))
        cut = d[time_col].iloc[len(d) - n_test]
        # keep equal timestamps on one side so no time point is split
        return d[d[time_col] < cut], d[d[time_col] >= cut]
    raise ValueError(f"unknown split '{how}'")


def make_split(df: pd.DataFrame, how: str = "random", sizes=(0.8, 0.2), stratify_col=None,
               group_col=None, time_col=None, cutoffs=None, seed: int = 42,
               stratify_bins: int = 10) -> dict[str, pd.DataFrame]:
    if how == "none":
        return {"train": df.copy()}
    if how in ("stratified", "group_stratified") and stratify_col is None:
        raise ValueError("stratify_col is required")
    if how in ("group", "group_stratified") and group_col is None:
        raise ValueError("group_col is required")
    if how == "time" and time_col is None:
        raise ValueError("time_col is required")

    if how == "time" and cutoffs:
        t = pd.to_datetime(df[time_col])
        cuts = [pd.Timestamp(c) for c in cutoffs]
        names = NAMES[len(cuts) + 1]
        bounds = [pd.Timestamp.min] + cuts + [pd.Timestamp.max]
        return {n: df[(t >= lo) & (t < hi)].copy() for n, lo, hi in zip(names, bounds[:-1], bounds[1:])}

    sizes = tuple(sizes)
    if abs(sum(sizes) - 1) > 1e-6 or len(sizes) not in (2, 3):
        raise ValueError("sizes must have 2 or 3 fractions summing to 1")
    names = NAMES[len(sizes)]
    rest, test = _two_way(df, how, sizes[-1], stratify_col, group_col, time_col, seed, stratify_bins)
    if how in ("group", "group_stratified"):
        got = len(test) / len(df)
        if abs(got - sizes[-1]) > 0.05:
            warnings.warn(f"Group split: test share is {got:.1%} instead of {sizes[-1]:.0%} - large groups "
                          f"cannot be divided. Check split_report().")
    if len(sizes) == 2:
        out = {"train": rest, "test": test}
    else:
        val_frac = sizes[1] / (sizes[0] + sizes[1])
        train, val = _two_way(rest, how, val_frac, stratify_col, group_col, time_col, seed, stratify_bins)
        out = {"train": train, "val": val, "test": test}
    return {k: v.copy() for k, v in out.items() if k in names}


def split_report(splits: dict[str, pd.DataFrame], target=None, group_col=None, time_col=None) -> pd.DataFrame:
    """Rows, share, target summary, time range per split, and group overlap with train."""
    total = sum(len(v) for v in splits.values())
    rows = []
    train_groups = set(splits["train"][group_col]) if group_col and "train" in splits else set()
    for name, d in splits.items():
        r = {"split": name, "rows": len(d), "share": len(d) / total}
        if target is not None and target in d:
            y = d[target]
            if pd.api.types.is_numeric_dtype(y) and y.nunique() > 10:
                r["target_mean"] = y.mean()
            else:
                r["target_dist"] = y.value_counts(normalize=True).round(3).to_dict()
        if time_col and time_col in d:
            t = pd.to_datetime(d[time_col])
            r["time_min"], r["time_max"] = t.min(), t.max()
        if group_col and name != "train":
            g = set(d[group_col])
            r["groups_also_in_train"] = len(g & train_groups)
        rows.append(r)
    return pd.DataFrame(rows)


def make_cv_folds(df: pd.DataFrame, how: str = "random", n_splits: int = 5, stratify_col=None,
                  group_col=None, time_col=None, seed: int = 42) -> np.ndarray:
    """Fold id per row of ``df`` (usually the train split). For how="time", fold k validates on the
    k-th later block and rows never used for validation get -1 (they are always training rows)."""
    folds = np.full(len(df), -1, dtype=int)
    if how == "random":
        it = KFold(n_splits, shuffle=True, random_state=seed).split(df)
    elif how == "stratified":
        it = StratifiedKFold(n_splits, shuffle=True, random_state=seed).split(df, df[stratify_col])
    elif how == "group":
        it = GroupKFold(n_splits).split(df, groups=df[group_col])
    elif how == "group_stratified":
        it = StratifiedGroupKFold(n_splits, shuffle=True, random_state=seed).split(df, df[stratify_col], df[group_col])
    elif how == "time":
        order = np.argsort(pd.to_datetime(df[time_col]).to_numpy(), kind="mergesort")
        it = ((order[tr], order[va]) for tr, va in TimeSeriesSplit(n_splits).split(order))
    else:
        raise ValueError(f"unknown fold scheme '{how}'")
    for k, (_, va) in enumerate(it):
        folds[va] = k
    return folds


def separate_unlabeled(df: pd.DataFrame, target) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split off rows whose target is missing (all target columns missing, for a list of columns).
    Returns (labeled, unlabeled). What to do with the unlabeled rows is the user's decision."""
    cols = [target] if isinstance(target, str) else list(target)
    miss = df[cols].isna().all(axis=1)
    return df[~miss].copy(), df[miss].copy()
