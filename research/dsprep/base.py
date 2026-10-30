"""Shared base class and helpers for all column processors.

Every processor:
- takes a per-column ``spec`` (column -> method name, or column -> dict of params),
- falls back to processor-level defaults for anything the spec does not set,
- is fitted on train only (``fit``) and applied to any frame (``transform``),
- records what it did in ``self.log_`` and describes its outputs in ``self.feature_info_``.

Target-based methods (target encoding, group stats on the target) always compute out-of-fold values
for the training rows during fit. ``fit_transform(train)`` and ``fit(train)`` followed by
``transform(train)`` therefore return the same, leak-free values. Any other frame gets the encoding
fitted on the full train set. The folds follow the split: pass ``group_col`` (rows of one entity stay in
one fold) or ``time_col`` (each row is encoded only with earlier rows) to ``fit``.

Processors are sklearn-compatible (BaseEstimator + TransformerMixin): ``get_params`` / ``set_params``
/ ``clone`` work, so they can go into a Pipeline or a hyperparameter search later.
"""
from __future__ import annotations

import copy
import re
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.model_selection import GroupKFold, KFold, StratifiedGroupKFold, StratifiedKFold

MISSING_TOKEN = "__MISSING__"
OTHER_TOKEN = "__OTHER__"
UNSEEN_TOKEN = "__UNSEEN__"

_UNSAFE = re.compile(r"[^0-9A-Za-z_\-\.֐-׿]+")


def safe_name(x) -> str:
    """Make a level / item usable in a column name (keeps Latin, digits, Hebrew, _ - .)."""
    return _UNSAFE.sub("_", str(x)) or "blank"


def level_str(v):
    """Stable string for a categorical value, or None when missing.

    Whole-number floats are written as integers, so a code stored as 1 in one file and as 1.0 in
    another (pandas turns an int column with any missing value into float) maps to the same level "1".
    """
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        if np.isnan(v):
            return None
        return str(int(v)) if float(v).is_integer() else str(float(v))
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def normalize_spec(spec: Any, default_method) -> dict[str, dict]:
    """Turn the user spec into {column: {method: ..., **params}}.

    Accepted forms:
      ["a", "b"]                       -> default method for each
      {"a": "onehot"}                  -> method only
      {"a": {"method": "target", "smoothing": 20}}
      {"a": None}                      -> default method
      {"a": ["stats", "tfidf"]}        -> several methods (processors that support it)
    """
    if spec is None:
        return {}
    if isinstance(spec, str):
        spec = [spec]
    if isinstance(spec, (list, tuple)):
        return {c: {"method": copy.deepcopy(default_method)} for c in spec}
    out = {}
    for col, val in spec.items():
        if val is None:
            out[col] = {"method": copy.deepcopy(default_method)}
        elif isinstance(val, str):
            out[col] = {"method": val}
        elif isinstance(val, (list, tuple)):
            out[col] = {"method": list(val)}  # several methods for one column (text, lists)
        elif isinstance(val, dict):
            v = copy.deepcopy(val)
            v.setdefault("method", copy.deepcopy(default_method))
            out[col] = v
        else:
            raise TypeError(f"Spec for column '{col}' must be str, list, dict or None, got {type(val)}")
    return out


def oof_folds(n: int, n_folds: int = 5, seed: int = 42, strat=None, groups=None, times=None):
    """Folds for out-of-fold encodings, matching the split scheme.

    times  -> ordered folds: rows are cut into ``n_folds`` consecutive time blocks (equal timestamps
              stay in one block); each block is encoded only with the blocks before it. The first block
              has no history and gets the prior (empty train part).
    groups -> GroupKFold (StratifiedGroupKFold when ``strat`` is given): an entity's rows never
              encode each other.
    else   -> StratifiedKFold when ``strat`` is given, otherwise KFold.
    Returns a list of (train_positions, val_positions).
    """
    pos = np.arange(n)
    if times is not None:
        t = pd.to_datetime(pd.Series(times)).to_numpy()
        order = np.argsort(t, kind="mergesort")
        uniq = np.unique(t[order])
        cuts = [uniq[int(round(i * len(uniq) / n_folds))] for i in range(1, n_folds) if
                int(round(i * len(uniq) / n_folds)) < len(uniq)]
        edges = [None] + sorted(set(cuts)) + [None]
        folds = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = np.ones(n, bool)
            if lo is not None:
                m &= t >= lo
            if hi is not None:
                m &= t < hi
            va = pos[m]
            tr = pos[t < lo] if lo is not None else np.array([], dtype=int)
            if len(va):
                folds.append((tr, va))
        return folds
    if groups is not None:
        g = pd.Series(groups).astype(str).to_numpy()
        k = max(2, min(n_folds, len(np.unique(g))))
        if strat is not None:
            try:
                return list(StratifiedGroupKFold(k, shuffle=True, random_state=seed).split(pos, strat, g))
            except ValueError:
                pass
        return list(GroupKFold(k).split(pos, groups=g))
    if strat is not None:
        try:
            return list(StratifiedKFold(n_folds, shuffle=True, random_state=seed).split(pos, strat))
        except ValueError:
            pass
    return list(KFold(n_folds, shuffle=True, random_state=seed).split(pos))


def _fingerprint(X: pd.DataFrame, cols) -> tuple:
    """Identity of a frame (index + values of the given columns), to recognise the training frame."""
    try:
        h = int(pd.util.hash_pandas_object(X[list(cols)], index=True).sum())
    except TypeError:  # unhashable cells (lists): fall back to the index
        h = int(pd.util.hash_pandas_object(pd.Series(X.index), index=False).sum())
    return (len(X), tuple(cols), h)


class BaseColumnProcessor(BaseEstimator, TransformerMixin):
    """Base class. Subclasses implement ``_fit_column`` and ``_transform_column``.

    Subclasses define every option as an explicit ``__init__`` argument stored under the same name;
    those values are the processor-level defaults (read at fit time, so ``set_params`` works).
    """

    default_method = None
    valid_methods: tuple[str, ...] = ()
    needs_target: tuple[str, ...] = ()  # methods that require y in fit
    _aliases: dict[str, str] = {}       # init-arg name -> per-column param name
    prefix_sep = "__"

    def __init__(self, spec=None, keep_original: bool = False):
        self.spec = spec
        self.keep_original = keep_original

    # ---------- public API ----------
    def fit(self, X: pd.DataFrame, y=None, task: str | None = None, group_col: str | None = None,
            time_col: str | None = None):
        self._fit_impl(X, y, task, group_col, time_col)
        return self

    def fit_transform(self, X: pd.DataFrame, y=None, task: str | None = None, group_col: str | None = None,
                      time_col: str | None = None, **_):
        self._fit_impl(X, y, task, group_col, time_col)
        return self._transform_impl(X, use_oof=bool(self._oof_cache_))

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """Transform any frame. The training frame itself is recognised and gets the out-of-fold values."""
        self._check_fitted()
        use_oof = bool(self._oof_cache_) and _fingerprint(X, self._fp_cols_) == self._fit_fp_
        return self._transform_impl(X, use_oof=use_oof)

    def get_feature_names_out(self, input_features=None):
        self._check_fitted()
        return np.array([f["feature"] for f in self.feature_info_])

    @property
    def columns(self) -> list[str]:
        return list(self.col_specs_.keys()) if hasattr(self, "col_specs_") else list(
            normalize_spec(self.spec, self._method_default()).keys())

    # ---------- internals ----------
    def _method_default(self):
        return getattr(self, "method", type(self).default_method)

    def _param_defaults(self) -> dict:
        out = {}
        for k, v in self.get_params(deep=False).items():
            if k in ("spec", "keep_original", "method"):
                continue
            out[self._aliases.get(k, k)] = copy.deepcopy(v)
        return out

    def _fit_impl(self, X, y, task, group_col=None, time_col=None):
        self.col_specs_ = normalize_spec(self.spec, self._method_default())
        missing = [c for c in self.col_specs_ if c not in X.columns]
        if missing:
            raise KeyError(f"{type(self).__name__}: columns not in data: {missing}")
        for c in (group_col, time_col):
            if c is not None and c not in X.columns:
                raise KeyError(f"{type(self).__name__}: fold column '{c}' not in data")
        self.task_ = task
        self.fold_scheme_ = "time" if time_col else ("group" if group_col else "random")
        self._oof_groups = X[group_col].to_numpy() if group_col else None
        self._oof_times = X[time_col].to_numpy() if time_col else None
        self.log_: list[dict] = []
        self.feature_info_: list[dict] = []
        self.state_: dict[str, dict] = {}
        self._oof_cache_: dict[str, pd.DataFrame] = {}
        y_series = None
        if y is not None:
            if len(y) != len(X):
                raise ValueError(f"y has {len(y)} rows, X has {len(X)}")
            if isinstance(y, pd.DataFrame):
                y_series = y if y.index.equals(X.index) else y.set_axis(X.index)
            else:
                y_series = pd.Series(np.asarray(y), index=X.index, name=getattr(y, "name", None))
        defaults = self._param_defaults()
        for col, params in self.col_specs_.items():
            p = {**defaults, **{self._aliases.get(k, k): v for k, v in params.items()}}
            method = p.get("method")
            methods = list(method) if isinstance(method, (list, tuple)) else [method]
            bad = [m for m in methods if self.valid_methods and m not in self.valid_methods]
            if bad:
                raise ValueError(f"{type(self).__name__}: unknown method(s) {bad} for '{col}'. "
                                 f"Valid: {self.valid_methods}")
            if any(m in self.needs_target for m in methods) and y_series is None:
                raise ValueError(f"Method '{method}' for column '{col}' needs y in fit().")
            state = self._fit_column(X[col], y_series, p)
            state["params"] = p
            self.state_[col] = state
        self._oof_groups = self._oof_times = None
        self._fp_cols_ = list(self.col_specs_)
        self._fit_fp_ = _fingerprint(X, self._fp_cols_) if self._oof_cache_ else None
        # build feature_info_ by transforming a tiny slice
        self._transform_impl(X.head(min(len(X), 50)), use_oof=False, collect_info=True)

    def _folds(self, y, mask, p):
        """Out-of-fold splits over the fit rows selected by ``mask`` (bool array aligned with X),
        following the fit's fold scheme. Returns (train_positions, val_positions) into X."""
        idx = np.where(np.asarray(mask))[0]
        groups = None if self._oof_groups is None else self._oof_groups[idx]
        times = None if self._oof_times is None else self._oof_times[idx]
        strat = None
        if self.task_ in ("binary", "multiclass") and isinstance(y, pd.Series):
            strat = y.to_numpy()[idx].astype(str)
        folds = oof_folds(len(idx), int(p.get("n_folds", 5)), p.get("random_state", 42),
                          strat=strat, groups=groups, times=times)
        return [(idx[tr], idx[va]) for tr, va in folds]

    def _transform_impl(self, X, use_oof=False, collect_info=False):
        parts = []
        if collect_info:
            self.feature_info_ = []
        for col, state in self.state_.items():
            if col not in X.columns:
                raise KeyError(f"{type(self).__name__}: column '{col}' missing at transform time")
            if use_oof and col in self._oof_cache_:
                res = self._oof_cache_[col].loc[X.index]
            else:
                res = self._transform_column(X[col], state)
            if isinstance(res, pd.Series):
                res = res.to_frame()
            res.index = X.index
            if collect_info:
                for f in res.columns:
                    self.feature_info_.append({
                        "feature": f,
                        "source": col,
                        "processor": type(self).__name__,
                        "method": state["params"].get("method"),
                        "dtype": str(res[f].dtype),
                    })
            parts.append(res)
        out = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=X.index)
        if self.keep_original:
            keep = [c for c in self.state_ if c not in out.columns]
            out = pd.concat([X[keep], out], axis=1)
        return out

    def _log(self, column, action, detail=""):
        self.log_.append({"processor": type(self).__name__, "column": column,
                          "action": action, "detail": detail})

    def _check_fitted(self):
        if not hasattr(self, "state_"):
            raise RuntimeError(f"{type(self).__name__} is not fitted yet. Call fit() first.")

    def _name(self, col, suffix=None):
        """Output column name. The suffix is sanitized (LightGBM/XGBoost reject special characters)."""
        return col if suffix is None else f"{col}{self.prefix_sep}{safe_name(suffix)}"

    # to implement
    def _fit_column(self, s: pd.Series, y, p: dict) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _transform_column(self, s: pd.Series, state: dict):  # pragma: no cover
        raise NotImplementedError
