"""List / multi-value columns: "a|b|c", '["a", "b"]', Python lists, or lists of numbers.

Methods (per column; a list combines them, default ["stats", "multihot"]):
  stats      list length, number of unique items, missing flag
  multihot   0/1 column per item for the top_k most frequent train items (+ "other" count)
  count      count per item (for lists with repeats) for the top_k items
  numeric    aggregates for numeric lists: mean, min, max, sum, std
  native     items joined with spaces as one text column (for CatBoost text_features or TextProcessor)

Per-column params:
  sep        delimiter for string lists (default "|"); JSON / Python-literal lists are detected automatically
  top_k      number of items to expand (default 30)
  min_frac   ignore items present in less than this share of train rows (default 0.005)
  min_count  optional absolute floor on top of min_frac (default None)
  normalize  strip + lowercase items (default True)
"""
from __future__ import annotations

import ast
import json
from collections import Counter

import numpy as np
import pandas as pd

from .base import BaseColumnProcessor


def parse_list(v, sep="|", normalize=True):
    """Parse one cell into a Python list (None for missing)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (list, tuple, set, np.ndarray)):
        items = list(v)
    else:
        t = str(v).strip()
        if t == "":
            return []
        items = None
        if t[0] in "[(" and t[-1] in "])":
            for loader in (json.loads, ast.literal_eval):
                try:
                    items = list(loader(t))
                    break
                except Exception:
                    pass
        if items is None:
            items = t.split(sep)
    out = []
    for it in items:
        if it is None:
            continue
        if isinstance(it, str):
            it = it.strip()
            if normalize:
                it = it.lower()
            if it == "":
                continue
        out.append(it)
    return out


class ListProcessor(BaseColumnProcessor):
    default_method = ("stats", "multihot")
    valid_methods = ("stats", "multihot", "count", "numeric", "native")

    def __init__(self, spec=None, keep_original=False, method=("stats", "multihot"), sep="|",
                 top_k=30, min_frac=0.005, min_count=None, normalize=True):
        super().__init__(spec=spec, keep_original=keep_original)
        self.method = method
        self.sep = sep
        self.top_k = top_k
        self.min_frac = min_frac
        self.min_count = min_count
        self.normalize = normalize

    def _parse(self, s, p):
        return s.map(lambda v: parse_list(v, p.get("sep", "|"), p.get("normalize", True)))

    def _fit_column(self, s, y, p):
        col = s.name
        methods = list(p["method"]) if isinstance(p["method"], (list, tuple)) else [p["method"]]
        st: dict = {"methods": methods}
        L = self._parse(s, p)
        if any(m in methods for m in ("multihot", "count")):
            c = Counter(it for lst in L.dropna() for it in set(lst))
            floor = max((p.get("min_frac") or 0) * len(L), p.get("min_count") or 0)
            items = [it for it, n in c.most_common() if n >= floor][: p.get("top_k", 30)]
            st["items"] = items
            self._log(col, "items", f"{len(c)} distinct items in train, {len(items)} expanded "
                                    f"(top_k={p.get('top_k', 30)}, item must appear in >= {floor:.0f} rows)")
        if "numeric" in methods:
            self._log(col, "numeric", "mean/min/max/sum/std of numeric items")
        return st

    def _transform_column(self, s, st):
        p = st["params"]
        col = s.name
        L = self._parse(s, p)
        parts = []
        m = st["methods"]
        if "stats" in m:
            parts.append(pd.DataFrame({
                self._name(col, "missing"): L.isna().astype("int8"),
                self._name(col, "len"): L.map(lambda l: np.nan if l is None else len(l)).astype(float),
                self._name(col, "n_unique"): L.map(lambda l: np.nan if l is None else len(set(map(str, l)))).astype(float),
            }, index=s.index))
        if "multihot" in m or "count" in m:
            items = st["items"]
            sets = L.map(lambda l: set() if l is None else set(l))
            if "multihot" in m:
                data = {self._name(col, f"has_{it}"): sets.map(lambda z, it=it: int(it in z)).astype("int8") for it in items}
                known = set(items)
                data[self._name(col, "n_other")] = sets.map(lambda z: len(z - known)).astype(float)
                parts.append(pd.DataFrame(data, index=s.index))
            if "count" in m:
                cnts = L.map(lambda l: Counter() if l is None else Counter(l))
                data = {self._name(col, f"cnt_{it}"): cnts.map(lambda c, it=it: c.get(it, 0)).astype(float) for it in items}
                parts.append(pd.DataFrame(data, index=s.index))
        if "numeric" in m:
            nums = L.map(lambda l: np.array([float(v) for v in l if _is_num(v)]) if l is not None else np.array([]))
            agg = {}
            for name, fn in (("mean", np.mean), ("min", np.min), ("max", np.max), ("sum", np.sum), ("std", np.std)):
                agg[self._name(col, name)] = nums.map(lambda a, fn=fn: float(fn(a)) if len(a) else np.nan)
            parts.append(pd.DataFrame(agg, index=s.index))
        if "native" in m:
            parts.append(pd.DataFrame({col: L.map(lambda l: "" if l is None else " ".join(map(str, l)))}, index=s.index))
        return pd.concat(parts, axis=1)


def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
