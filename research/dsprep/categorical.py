"""Categorical features - one adaptive processor for low- and high-cardinality columns.

Methods (per column):
  native     keep as one string column (missing -> "__MISSING__"); for CatBoost ``cat_features``
             or, with native_dtype="category", for LightGBM / XGBoost(enable_categorical)
  onehot     one 0/1 column per level (after rare grouping); unseen -> all zeros
  ordinal    integer code per level; pass ``order=[...]`` for a meaningful order,
             otherwise codes follow frequency. Unseen/missing -> -1
  target     smoothed target mean per level. Train rows get out-of-fold values (no self-leakage);
             folds follow the split when ``group_col`` / ``time_col`` is given to fit.
             Binary/regression -> 1 column, multiclass -> one column per class. Not for multilabel.
  count      number of train rows with that level
  frequency  share of train rows with that level
  hashing    n_hash_features hashed 0/1 columns (stable md5 hashing); for very high cardinality

Common per-column params:
  normalize       True -> strip, lowercase, collapse whitespace before anything else
  group_rare      "auto" (default) | True | False. "auto" groups rare levels only for onehot and
                  ordinal; native (CatBoost handles rare levels), target (smoothing handles them),
                  count / frequency (they encode rarity) and hashing keep every level.
  rare_min_frac   a level is rare below this share of train rows (default 0.01) - adapts to data size
  rare_min_count  optional absolute floor on top of the share (default None = share only)
  max_levels      keep at most this many levels for onehot (rest -> "__OTHER__")
  missing         "category" (missing is its own level, default) | "nan" (keep NaN, only for ordinal/count/frequency/target)
  smoothing       target encoding m-estimate weight (default 20)
  n_folds         folds for out-of-fold target encoding (default 5)
  n_hash_features number of hash buckets (default 32)
  native_dtype    "str" (default) | "category"
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .base import MISSING_TOKEN, OTHER_TOKEN, UNSEEN_TOKEN, BaseColumnProcessor, level_str


def _clean_str(s: pd.Series, normalize: bool) -> pd.Series:
    """Object series of str / None (works for object, string, category and numeric dtypes).
    Whole-number floats become integers, so 1 and 1.0 are the same level."""
    vals = [level_str(v) for v in s.to_numpy(dtype=object)]
    if normalize:
        vals = [v if v is None else " ".join(v.strip().lower().split()) for v in vals]
    return pd.Series(vals, index=s.index, dtype=object, name=s.name)


class CategoricalProcessor(BaseColumnProcessor):
    default_method = "onehot"
    valid_methods = ("native", "onehot", "ordinal", "target", "count", "frequency", "hashing")
    needs_target = ("target",)

    def __init__(self, spec=None, keep_original=False, method="onehot", normalize=False,
                 group_rare="auto", rare_min_frac=0.01, rare_min_count=None, max_levels=None,
                 missing="category", smoothing=20.0, n_folds=5, n_hash_features=32,
                 native_dtype="str", order=None, random_state=42):
        super().__init__(spec=spec, keep_original=keep_original)
        self.method = method
        self.normalize = normalize
        self.group_rare = group_rare
        self.rare_min_frac = rare_min_frac
        self.rare_min_count = rare_min_count
        self.max_levels = max_levels
        self.missing = missing
        self.smoothing = smoothing
        self.n_folds = n_folds
        self.n_hash_features = n_hash_features
        self.native_dtype = native_dtype
        self.order = order
        self.random_state = random_state

    # ---------- helpers ----------
    def _prep(self, s, st, p):
        x = _clean_str(s, p.get("normalize", False))
        if p.get("missing", "category") == "category":
            x = x.map(lambda v: MISSING_TOKEN if v is None else v)
        if st.get("keep_levels") is not None:
            keep = st["keep_levels"]
            known = st["all_levels"]
            x = x.map(lambda v: v if (v is None or v in keep) else (OTHER_TOKEN if v in known else st["unseen_as"]))
        return x

    def get_cat_features(self) -> list[str]:
        """Output column names that should be passed as categorical to CatBoost/LightGBM."""
        self._check_fitted()
        return [f["feature"] for f in self.feature_info_ if f["method"] in ("native",)]

    # ---------- fit ----------
    def _fit_column(self, s, y, p):
        col = s.name
        method = p["method"]
        if method == "target" and self.task_ == "multilabel":
            raise ValueError("Target encoding is not supported for multilabel targets. Use count/frequency/onehot.")
        st: dict = {"unseen_as": OTHER_TOKEN}
        x = _clean_str(s, p.get("normalize", False))
        if p.get("missing", "category") == "category":
            x = x.map(lambda v: MISSING_TOKEN if v is None else v)
        vc = x.value_counts(dropna=True)
        st["all_levels"] = set(vc.index)

        # rare grouping
        keep = set(vc.index)
        gr = p.get("group_rare", "auto")
        if gr == "auto":
            gr = method in ("onehot", "ordinal")
        if gr and method != "hashing":
            n = len(x)
            is_rare = vc / n < (p.get("rare_min_frac") or 0)
            if p.get("rare_min_count"):
                is_rare |= vc < p["rare_min_count"]
            rare = vc[is_rare]
            rare = rare.drop(labels=[MISSING_TOKEN], errors="ignore")
            if len(rare):
                keep -= set(rare.index)
                self._log(col, "group_rare", f"{len(rare)} levels ({int(rare.sum())} rows) -> {OTHER_TOKEN}")
        if p.get("max_levels") and method == "onehot":
            top = [lvl for lvl in vc.index if lvl in keep][: p["max_levels"]]
            dropped = len(keep) - len(top)
            keep = set(top)
            if dropped > 0:
                self._log(col, "max_levels", f"kept top {p['max_levels']}, {dropped} more -> {OTHER_TOKEN}")
        st["keep_levels"] = keep
        # unseen levels at transform time join "__OTHER__" when train had a rare group, else "__UNSEEN__"
        st["unseen_as"] = OTHER_TOKEN if len(keep) < len(st["all_levels"]) else UNSEEN_TOKEN
        xp = self._prep(s, st, p)
        counts = xp.value_counts(dropna=True)

        if method == "native":
            self._log(col, "native", f"{len(counts)} levels kept as {p.get('native_dtype', 'str')}")
        elif method == "onehot":
            st["levels"] = sorted(counts.index, key=lambda v: (-counts[v], str(v)))
            self._log(col, "onehot", f"{len(st['levels'])} columns")
        elif method == "ordinal":
            order = p.get("order")
            if order:
                order = [str(o) for o in order]
                extra = [lvl for lvl in counts.index if lvl not in order]
                st["codes"] = {lvl: i for i, lvl in enumerate(order + extra)}
                if extra:
                    self._log(col, "ordinal", f"levels not in given order appended: {extra[:5]}")
            else:
                st["codes"] = {lvl: i for i, lvl in enumerate(counts.index)}
            self._log(col, "ordinal", f"{len(st['codes'])} codes")
        elif method in ("count", "frequency"):
            st["map"] = (counts if method == "count" else counts / counts.sum()).to_dict()
            self._log(col, method, f"{len(counts)} levels")
        elif method == "hashing":
            st["n_hash"] = int(p.get("n_hash_features", 32))
            self._log(col, "hashing", f"{st['n_hash']} buckets for {len(counts)} levels")
        elif method == "target":
            self._fit_target(col, xp, y, p, st)
        return st

    def _target_frame(self, y, index):
        """Return (Y matrix as DataFrame of numeric columns, suffixes)."""
        y = y.loc[index]
        if self.task_ == "multiclass":
            classes = sorted(pd.Series(y).dropna().unique(), key=str)
            Y = pd.DataFrame({f"te_{c}": (y == c).astype(float) for c in classes}, index=index)
            return Y
        yn = pd.to_numeric(y, errors="coerce").astype(float)
        if yn.notna().sum() == 0 and pd.Series(y).notna().any():
            raise ValueError("Target encoding needs a numeric target. Encode the target first "
                             "(TargetProcessor) and pass the encoded y.")
        return pd.DataFrame({"te": yn}, index=index)

    @staticmethod
    def _encode(xp, Y, m):
        prior = Y.mean()
        df = Y.copy()
        df["_lvl"] = xp.values
        g = df.groupby("_lvl", dropna=False)
        n = g.size()
        sums = g[list(Y.columns)].sum()
        enc = (sums.add(prior * m, axis=1)).div(n + m, axis=0)
        return enc, prior

    def _fit_target(self, col, xp, y, p, st):
        m = float(p.get("smoothing", 20.0))
        Y = self._target_frame(y, xp.index)
        ok = Y.notna().all(axis=1).to_numpy()
        enc, prior = self._encode(xp[ok], Y[ok], m)
        st["te_map"], st["te_prior"], st["te_cols"] = enc, prior, list(Y.columns)
        # out-of-fold values for the training rows (folds follow the split scheme)
        out = pd.DataFrame(np.nan, index=xp.index, columns=[self._name(col, c) for c in Y.columns])
        vals = np.full(out.shape, np.nan)
        covered = np.zeros(len(xp), bool)
        for tr, va in self._folds(y, ok, p):
            if len(tr):
                e, pr = self._encode(xp.iloc[tr], Y.iloc[tr], m)
            else:  # first time block has no history: prior only
                e, pr = enc.iloc[:0], prior
            vals[va] = e.reindex(xp.iloc[va].values).fillna(pr).to_numpy()
            covered[va] = True
        # labeled rows outside every fold (missing timestamp with time folds): prior, never their own label
        lost = np.where(ok & ~covered)[0]
        if len(lost):
            vals[lost] = np.tile(prior.to_numpy(), (len(lost), 1))
            self._log(col, "target", f"{len(lost)} rows without a fold (missing time) got the prior")
        # rows with a missing target are never in a fold: full-train encoding
        rest = np.where(~ok)[0]
        if len(rest):
            vals[rest] = enc.reindex(xp.iloc[rest].values).fillna(prior).to_numpy()
        out.loc[:, :] = vals
        self._oof_cache_[col] = out
        self._log(col, "target", f"smoothing={m}, {len(enc)} levels, out-of-fold on train "
                                 f"({self.fold_scheme_} folds)")

    # ---------- transform ----------
    def _transform_column(self, s, st):
        p = st["params"]
        col = s.name
        method = p["method"]
        xp = self._prep(s, st, p)
        if method == "native":
            if p.get("native_dtype", "str") == "category":
                return pd.DataFrame({col: pd.Categorical(xp)}, index=s.index)
            return pd.DataFrame({col: xp.astype(str)}, index=s.index)
        if method == "onehot":
            data = {self._name(col, lvl): (xp == lvl).astype("int8") for lvl in st["levels"]}
            return pd.DataFrame(data, index=s.index)
        if method == "ordinal":
            return pd.DataFrame({self._name(col, "ord"): xp.map(st["codes"]).fillna(-1).astype(int)}, index=s.index)
        if method in ("count", "frequency"):
            return pd.DataFrame({self._name(col, method): xp.map(st["map"]).fillna(0).astype(float)}, index=s.index)
        if method == "hashing":
            n = st["n_hash"]
            h = xp.map(lambda v: int(hashlib.md5(str(v).encode()).hexdigest(), 16) % n)
            data = {self._name(col, f"hash{i}"): (h == i).astype("int8") for i in range(n)}
            return pd.DataFrame(data, index=s.index)
        if method == "target":
            vals = st["te_map"].reindex(xp.values).fillna(st["te_prior"]).to_numpy()
            return pd.DataFrame(vals, index=s.index, columns=[self._name(col, c) for c in st["te_cols"]])
        raise ValueError(method)
