"""Entity-level features for data with repeated rows per entity (customer, device, patient).

Two tools, with different leakage rules:

1. ``add_history_features`` - stateless, past-only. For each row, aggregates of the SAME entity's
   EARLIER rows (strictly before the row's timestamp). Safe to run on the full, unsplit frame,
   because a row never sees itself or its future. Use for lags, rolling means, counts so far,
   days since previous event.
   Caveat: if a value column is the target (or derived from it), make sure the earlier rows'
   labels were actually known at prediction time (label delay) - pass ``min_gap`` if not.

2. ``GroupStatsProcessor`` - fitted on train only. Maps each row's group to statistics of a
   value column computed on the train set (e.g. mean price per category). Train rows can get
   out-of-fold values (``fit_transform``) when the value column is the target.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseColumnProcessor, level_str, safe_name


def add_history_features(df: pd.DataFrame, entity_col: str, time_col: str, value_cols=(),
                         aggs=("mean", "max", "last"), windows=(None,), add_count=True,
                         add_recency=True, min_gap=None) -> pd.DataFrame:
    """Return a copy of ``df`` with past-only entity aggregates added (vectorized).

    For each row, the history is the same entity's rows that are strictly earlier in time. Rows with an
    equal timestamp never see each other. With ``min_gap`` (pandas offset, e.g. "30D") the history is
    only rows at least that much older - use it when a past row's value (e.g. its label) becomes known
    only after a delay.

    windows : row-count windows over the history, None = all of it. E.g. (3, None).
    aggs    : any of mean, sum, min, max, std, count, last
    Outputs : <entity>__n_prev, <entity>__days_since_prev (time since the previous event, ignoring
              min_gap - the event itself is known), <col>__last, <col>__<all|lastK>_<agg>.
    Rows with a missing entity or time get NaN.
    """
    out = df.copy()
    n = len(out)
    d = pd.DataFrame({"_ec": pd.factorize(out[entity_col])[0],
                      "_t": pd.to_datetime(out[time_col]).to_numpy().astype("datetime64[ns]"),
                      "_row": np.arange(n)})
    for c in value_cols:
        d[c] = pd.to_numeric(out[c], errors="coerce").to_numpy()
    d = d[(d["_ec"] >= 0) & d["_t"].notna()]
    d = d.sort_values(["_ec", "_t", "_row"], kind="mergesort").reset_index(drop=True)
    d["_k"] = d.groupby("_ec", sort=False).cumcount()

    def last_before(gap):
        """Row (position in d) of the last history row for every row of d, or -1."""
        cut = (d["_t"] - (gap if gap is not None else pd.Timedelta(0))).astype("datetime64[ns]")
        left = pd.DataFrame({"_ec": d["_ec"], "_cut": cut,
                             "_i": np.arange(len(d))}).sort_values("_cut", kind="mergesort")
        right = pd.DataFrame({"_ec": d["_ec"], "_t": d["_t"].astype("datetime64[ns]"), "_j": np.arange(len(d))}).sort_values("_t", kind="mergesort")
        m = pd.merge_asof(left, right, left_on="_cut", right_on="_t", by="_ec", direction="backward",
                          allow_exact_matches=gap is not None)
        j = np.full(len(d), -1)
        j[m["_i"].to_numpy()] = m["_j"].fillna(-1).astype(int).to_numpy()
        return j

    gap = pd.Timedelta(min_gap) if min_gap is not None else None
    j_hist = last_before(gap)
    j_prev = j_hist if gap is None else last_before(None)
    has = j_hist >= 0
    feats: dict[str, np.ndarray] = {}

    def take(arr, j):
        res = np.full(len(d), np.nan)
        ok = j >= 0
        res[ok] = np.asarray(arr, dtype=float)[j[ok]]
        return res

    if add_count:
        feats[f"{entity_col}__n_prev"] = np.where(has, d["_k"].to_numpy()[np.maximum(j_hist, 0)] + 1, 0).astype(float)
    if add_recency:
        tp = np.full(len(d), np.datetime64("NaT"), dtype="datetime64[ns]")
        okp = j_prev >= 0
        tp[okp] = d["_t"].to_numpy()[j_prev[okp]]
        feats[f"{entity_col}__days_since_prev"] = (d["_t"].to_numpy() - tp) / np.timedelta64(1, "D")
    g = d.groupby("_ec", sort=False)
    for col in value_cols:
        if "last" in aggs:
            feats[f"{col}__last"] = take(d[col], j_hist)
        for w in windows:
            tag = "all" if w is None else f"last{w}"
            for a in aggs:
                if a == "last":
                    continue
                win = g[col].expanding() if w is None else g[col].rolling(int(w), min_periods=1)
                stat = getattr(win, a)().droplevel(0).sort_index()   # stat over rows up to and including j
                feats[f"{col}__{tag}_{a}"] = take(stat.to_numpy(), j_hist)
    full = pd.DataFrame(np.nan, index=range(n), columns=list(feats))
    full.iloc[d["_row"].to_numpy()] = pd.DataFrame(feats).to_numpy()
    full.index = out.index
    return pd.concat([out, full], axis=1)


class GroupStatsProcessor(BaseColumnProcessor):
    """spec: {group_col: {"value": value_col, "stats": ["mean", "std", "count"]}}
    Output: <group>__<value>_<stat>, plus <value>__minus_<group>_mean when diff_from_mean=True.
    value="__target__" uses y and is out-of-fold on train automatically (folds follow ``group_col`` /
    ``time_col`` given to fit); set ``oof=True`` for any other value column derived from the target."""

    default_method = "stats"
    valid_methods = ("stats",)

    def __init__(self, spec=None, keep_original=False, stats=("mean", "std", "count"),
                 diff_from_mean=False, min_count=5, n_folds=5, random_state=42):
        super().__init__(spec=spec, keep_original=keep_original)
        self.stats = stats
        self.diff_from_mean = diff_from_mean
        self.min_count = min_count
        self.n_folds = n_folds
        self.random_state = random_state

    def _fit_impl(self, X, y, task, group_col=None, time_col=None):
        self._frame_ = X
        self._y_ = None if y is None else np.asarray(y)
        try:
            return super()._fit_impl(X, y, task, group_col, time_col)
        finally:
            self._y_ = None

    def _transform_impl(self, X, use_oof=False, collect_info=False):
        self._frame_ = X
        try:
            return super()._transform_impl(X, use_oof, collect_info)
        finally:
            self._frame_ = None

    def _values(self, p, index):
        vc = p["value"]
        if vc == "__target__":
            if self._y_ is None:
                raise ValueError("value='__target__' needs y in fit()")
            return pd.Series(pd.to_numeric(pd.Series(self._y_), errors="coerce").to_numpy(), index=index)
        return pd.to_numeric(self._frame_[vc], errors="coerce").set_axis(index)

    @staticmethod
    def _table(keys, vals, stats, min_count):
        df = pd.DataFrame({"k": [level_str(v) for v in keys.to_numpy(dtype=object)], "v": vals.to_numpy()})
        t = df.groupby("k")["v"].agg(list(stats) + (["count"] if "count" not in stats else []))
        glob = {s: (df["v"].agg(s) if s != "count" else 0) for s in stats}
        small = t["count"] < min_count
        for s in stats:
            if s != "count":
                t.loc[small, s] = np.nan
        return t[list(stats)], glob

    def _fit_column(self, s, y, p):
        col = s.name
        if "value" not in p:
            raise ValueError(f"GroupStatsProcessor: spec for '{col}' needs a 'value' column (or '__target__')")
        stats = list(p.get("stats", ("mean", "std", "count")))
        vals = self._values(p, s.index)
        t, glob = self._table(s, vals, stats, p.get("min_count", 5))
        st = {"table": t, "glob": glob, "stats": stats}
        self._log(col, "group_stats", f"{p['value']} by {col}: {stats}, {len(t)} groups")
        if p.get("oof", p["value"] == "__target__"):
            ok = vals.notna().to_numpy()
            out = pd.DataFrame(np.nan, index=s.index, columns=self._cols(col, p, stats))
            vals_out = np.full(out.shape, np.nan)
            covered = np.zeros(len(s), bool)
            for tr, va in self._folds(None, ok, p):
                if len(tr):
                    tt, gg = self._table(s.iloc[tr], vals.iloc[tr], stats, p.get("min_count", 5))
                else:
                    tt, gg = t.iloc[:0], glob
                vals_out[va] = self._map(s.iloc[va], vals.iloc[va], tt, gg, p, stats).to_numpy()
                covered[va] = True
            lost = np.where(ok & ~covered)[0]   # missing timestamp with time folds: global stats only
            if len(lost):
                vals_out[lost] = self._map(s.iloc[lost], vals.iloc[lost], t.iloc[:0], glob, p, stats).to_numpy()
            rest = np.where(~ok)[0]
            if len(rest):
                vals_out[rest] = self._map(s.iloc[rest], vals.iloc[rest], t, glob, p, stats).to_numpy()
            out.loc[:, :] = vals_out
            self._oof_cache_[col] = out
            self._log(col, "group_stats", f"out-of-fold on train ({self.fold_scheme_} folds)")
        return st

    def _cols(self, col, p, stats):
        v = safe_name(p["value"]).strip("_")
        cols = [f"{col}__{v}_{s}" for s in stats]
        if p.get("diff_from_mean") and "mean" in stats:
            cols.append(f"{v}__minus_{col}_mean")
        return cols

    def _map(self, keys, vals, table, glob, p, stats):
        m = table.reindex([level_str(v) for v in keys.to_numpy(dtype=object)])
        m.index = keys.index
        for s in stats:
            m[s] = m[s].fillna(glob[s])
        out = m[stats].copy()
        if p.get("diff_from_mean") and "mean" in stats:
            out["diff"] = vals - out["mean"]
        out.columns = self._cols(keys.name, p, stats)
        return out.astype(float)

    def _transform_column(self, s, st):
        p = st["params"]
        vals = (pd.to_numeric(self._frame_.loc[s.index, p["value"]], errors="coerce")
                if p["value"] != "__target__" else pd.Series(np.nan, index=s.index))
        return self._map(s, vals, st["table"], st["glob"], p, st["stats"])
