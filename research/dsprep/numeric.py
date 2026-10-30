"""Numeric features: imputation, missing indicators, capping, transforms, scaling, binning.

Per-column params (all optional, processor-level defaults apply otherwise):
  method           "auto" (default) - the steps below, in this order
                   "passthrough"     - leave the column untouched
  impute           None | "median" | "mean" | "constant" | "most_frequent"   (None keeps NaN; GBMs handle it)
  fill_value       value for impute="constant"
  missing_indicator  bool - add <col>__missing (0/1)
  cap              None | (low_pct, high_pct) e.g. (0.01, 0.99) - winsorize at train percentiles
  transform        None | "log1p" | "symlog" | "sqrt" | "yeo-johnson" | "box-cox"
                   (processor-level default is called ``transformation`` because
                   ``transform`` is the sklearn method name; in a column spec both keys work)
  scale            None | "standard" | "robust" | "minmax"
  bins             None | int (quantile bins) | list of edges - adds <col>__bin
  bins_only        bool - keep only the bin column
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import PowerTransformer

from .base import BaseColumnProcessor


class NumericProcessor(BaseColumnProcessor):
    default_method = "auto"
    valid_methods = ("auto", "passthrough")

    _aliases = {"transformation": "transform"}  # ``transform`` is the sklearn method name

    def __init__(self, spec=None, keep_original=False, impute=None, missing_indicator=False,
                 cap=None, transformation=None, scale=None, bins=None, bins_only=False, fill_value=0):
        super().__init__(spec=spec, keep_original=keep_original)
        self.impute = impute
        self.missing_indicator = missing_indicator
        self.cap = cap
        self.transformation = transformation
        self.scale = scale
        self.bins = bins
        self.bins_only = bins_only
        self.fill_value = fill_value

    def _fit_column(self, s, y, p):
        st: dict = {}
        x = pd.to_numeric(s, errors="coerce").astype(float)
        n_bad = int(x.isna().sum() - s.isna().sum())
        if n_bad:
            self._log(s.name, "coerce_to_numeric", f"{n_bad} non-numeric values set to NaN")
        if p["method"] == "passthrough":
            return st

        st["has_missing"] = bool(x.isna().any())
        if p.get("missing_indicator"):
            self._log(s.name, "missing_indicator", f"train missing rate {x.isna().mean():.3%}")

        if p.get("cap"):
            lo, hi = p["cap"]
            st["cap_lo"] = float(x.quantile(lo)) if lo is not None else None
            st["cap_hi"] = float(x.quantile(hi)) if hi is not None else None
            n = int(((x < (st["cap_lo"] if st["cap_lo"] is not None else -np.inf)) |
                     (x > (st["cap_hi"] if st["cap_hi"] is not None else np.inf))).sum())
            self._log(s.name, "cap", f"[{st['cap_lo']}, {st['cap_hi']}] at pct {p['cap']}, {n} train values capped")
            x = x.clip(st["cap_lo"], st["cap_hi"])

        t = p.get("transform")
        if t:
            if t in ("log1p", "sqrt", "box-cox") and (x.dropna() < 0).any():
                raise ValueError(f"'{s.name}': transform '{t}' needs non-negative values; use 'symlog' or 'yeo-johnson'.")
            if t in ("yeo-johnson", "box-cox"):
                xx = x.dropna().to_numpy().reshape(-1, 1)
                if t == "box-cox":
                    xx = xx + 1e-9  # box-cox needs strictly positive
                pt = PowerTransformer(method=t, standardize=False).fit(xx)
                st["power"] = pt
            x = self._apply_transform(x, t, st)
            self._log(s.name, "transform", t)

        imp = p.get("impute")
        if imp:
            if imp == "median":
                st["fill"] = float(x.median())
            elif imp == "mean":
                st["fill"] = float(x.mean())
            elif imp == "most_frequent":
                st["fill"] = float(x.mode().iloc[0]) if x.notna().any() else 0.0
            elif imp == "constant":
                st["fill"] = float(p.get("fill_value", 0))
            else:
                raise ValueError(f"Unknown impute '{imp}' for '{s.name}'")
            self._log(s.name, "impute", f"{imp} = {st['fill']:.6g} ({int(x.isna().sum())} train NaN)")
            x = x.fillna(st["fill"])

        sc = p.get("scale")
        if sc:
            if sc == "standard":
                st["center"], st["scale"] = float(x.mean()), float(x.std(ddof=0) or 1.0)
            elif sc == "robust":
                q1, q3 = x.quantile([0.25, 0.75])
                st["center"], st["scale"] = float(x.median()), float((q3 - q1) or 1.0)
            elif sc == "minmax":
                st["center"], st["scale"] = float(x.min()), float((x.max() - x.min()) or 1.0)
            else:
                raise ValueError(f"Unknown scale '{sc}' for '{s.name}'")
            x = (x - st["center"]) / st["scale"]  # bins below are computed on the scaled values
            self._log(s.name, "scale", sc)

        b = p.get("bins")
        if b is not None:
            if isinstance(b, int):
                _, edges = pd.qcut(x.dropna(), q=b, retbins=True, duplicates="drop")
            else:
                edges = np.asarray(b, dtype=float)
            edges = np.unique(edges)
            edges[0], edges[-1] = -np.inf, np.inf
            st["edges"] = edges
            self._log(s.name, "bins", f"{len(edges) - 1} bins")
        return st

    @staticmethod
    def _apply_transform(x, t, st):
        if t == "log1p":
            return np.log1p(x)
        if t == "sqrt":
            return np.sqrt(x)
        if t == "symlog":
            return np.sign(x) * np.log1p(np.abs(x))
        if t in ("yeo-johnson", "box-cox"):
            out = x.copy()
            m = x.notna()
            xx = x[m].to_numpy().reshape(-1, 1)
            if t == "box-cox":
                xx = np.clip(xx, 1e-9, None)
            out[m] = st["power"].transform(xx).ravel()
            return out
        raise ValueError(f"Unknown transform '{t}'")

    def _transform_column(self, s, st):
        p = st["params"]
        col = s.name
        x = pd.to_numeric(s, errors="coerce").astype(float)
        if p["method"] == "passthrough":
            return pd.DataFrame({col: x})
        out = {}
        if p.get("missing_indicator"):
            out[self._name(col, "missing")] = x.isna().astype("int8")
        if "cap_lo" in st or "cap_hi" in st:
            x = x.clip(st.get("cap_lo"), st.get("cap_hi"))
        if p.get("transform"):
            x = self._apply_transform(x, p["transform"], st)
        if "fill" in st:
            x = x.fillna(st["fill"])
        if "scale" in st:
            x = (x - st["center"]) / st["scale"]
        if "edges" in st:
            out[self._name(col, "bin")] = pd.cut(x, st["edges"], labels=False).astype("float")
        if not p.get("bins_only"):
            out = {col: x, **out}
        return pd.DataFrame(out, index=s.index)


def add_ratio_features(df: pd.DataFrame, pairs, kind: str = "ratio", eps: float = 1e-9) -> pd.DataFrame:
    """Stateless helper: add ratio / diff / product features for (a, b) pairs.

    kind: "ratio" (a / b), "diff" (a - b), "product" (a * b).
    Returns a copy with new columns named a__{kind}__b. Division by ~0 gives NaN.
    """
    out = df.copy()
    for a, b in pairs:
        name = f"{a}__{kind}__{b}"
        if kind == "ratio":
            denom = out[b].where(out[b].abs() > eps)
            out[name] = out[a] / denom
        elif kind == "diff":
            out[name] = out[a] - out[b]
        elif kind == "product":
            out[name] = out[a] * out[b]
        else:
            raise ValueError("kind must be 'ratio', 'diff' or 'product'")
    return out
