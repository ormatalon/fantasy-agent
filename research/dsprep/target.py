"""Target preparation for any problem type. Every step is optional - ``mode="none"`` leaves y as is.

task="binary"      map to 0/1. ``positive_class`` sets which value is 1 (asked for explicitly
                   when labels are strings, so metrics never use the wrong class).
task="multiclass"  integer codes 0..K-1 (needed by XGBoost/LightGBM; CatBoost and sklearn accept
                   strings, so ``encode=False`` keeps the labels).
task="multilabel"  multi-hot matrix, one 0/1 column per label. Input: lists, or delimited strings (sep).
task="regression" / "count"
                   optional transform: None | "log1p" | "sqrt" | "yeo-johnson" | "box-cox".
                   ``inverse_transform`` maps predictions back to the original scale.
task="time_to_event"  y is a frame with duration and event columns; validated and passed through.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import PowerTransformer

from .lists import parse_list


class TargetProcessor:
    TASKS = ("binary", "multiclass", "multilabel", "regression", "count", "time_to_event")

    def __init__(self, task: str, positive_class=None, encode: bool = True, transform: str | None = None,
                 sep: str = "|", duration_col: str | None = None, event_col: str | None = None,
                 mode: str = "auto"):
        if task not in self.TASKS:
            raise ValueError(f"task must be one of {self.TASKS}")
        self.task = task
        self.positive_class = positive_class
        self.encode = encode
        self.transform_name = transform
        self.sep = sep
        self.duration_col = duration_col
        self.event_col = event_col
        self.mode = mode
        self.log_: list[dict] = []

    def _log(self, action, detail=""):
        self.log_.append({"processor": "TargetProcessor", "column": "<target>", "action": action, "detail": detail})

    # ---------------- fit ----------------
    def fit(self, y):
        self.fitted_ = True
        if self.mode == "none":
            self._log("none", "target left unchanged by user choice")
            return self
        t = self.task
        if t == "binary":
            vals = sorted(pd.Series(y).dropna().unique(), key=str)
            if len(vals) != 2:
                raise ValueError(f"binary target must have 2 values, found {len(vals)}: {vals[:10]}")
            if self.positive_class is None:
                if set(vals) <= {0, 1, True, False}:
                    self.positive_class = 1
                else:
                    raise ValueError(f"Binary target has labels {vals}. Set positive_class= explicitly.")
            if self.positive_class not in vals:
                raise ValueError(f"positive_class {self.positive_class!r} not in target values {vals}")
            self.classes_ = [v for v in vals if v != self.positive_class] + [self.positive_class]
            self._log("binary_map", f"{self.classes_[0]!r} -> 0, {self.classes_[1]!r} -> 1")
        elif t == "multiclass":
            vc = pd.Series(y).value_counts()
            self.classes_ = sorted(vc.index, key=str)
            if self.encode:
                self._log("label_encode", ", ".join(f"{c!r}->{i}" for i, c in enumerate(self.classes_)))
            else:
                self._log("labels_kept", f"{len(self.classes_)} classes kept as labels")
        elif t == "multilabel":
            lists = pd.Series(y).map(lambda v: parse_list(v, self.sep, normalize=False) or [])
            self.classes_ = sorted({c for l in lists for c in l}, key=str)
            self._log("multilabel_binarize", f"{len(self.classes_)} labels")
        elif t in ("regression", "count"):
            tr = self.transform_name
            ys = pd.to_numeric(pd.Series(y), errors="coerce")
            if tr in ("log1p", "sqrt", "box-cox") and (ys.dropna() < 0).any():
                raise ValueError(f"transform {tr} needs a non-negative target")
            if tr in ("yeo-johnson", "box-cox"):
                arr = ys.dropna().to_numpy().reshape(-1, 1)
                if tr == "box-cox":
                    arr = np.clip(arr, 1e-9, None)
                self.power_ = PowerTransformer(method=tr, standardize=False).fit(arr)
            self._log("target_transform", tr or "none (original scale)")
        elif t == "time_to_event":
            if not isinstance(y, pd.DataFrame) or not {self.duration_col, self.event_col} <= set(y.columns):
                raise ValueError("time_to_event needs y as a DataFrame with duration_col and event_col")
            if (y[self.duration_col] < 0).any():
                raise ValueError("negative durations found")
            self._log("time_to_event", f"duration={self.duration_col}, event={self.event_col}, "
                                       f"event rate {y[self.event_col].mean():.2%}")
        return self

    # ---------------- transform ----------------
    def transform(self, y):
        if self.mode == "none":
            return y
        t = self.task
        idx = getattr(y, "index", None)
        if t == "binary":
            unknown = set(pd.Series(y).dropna().unique()) - set(self.classes_)
            if unknown:
                raise ValueError(f"Unknown target values: {unknown}")
            return pd.Series(np.where(pd.Series(y).isna(), np.nan, (pd.Series(y) == self.positive_class).astype(float)),
                             index=idx, name="target")
        if t == "multiclass":
            if not self.encode:
                return y
            m = {c: i for i, c in enumerate(self.classes_)}
            out = pd.Series(y).map(m)
            unknown = pd.Series(y)[out.isna() & pd.Series(y).notna()].unique()
            if len(unknown):
                raise ValueError(f"Unknown classes at transform: {list(unknown)[:10]}")
            return pd.Series(out.to_numpy(), index=idx, name="target")
        if t == "multilabel":
            lists = pd.Series(y).map(lambda v: parse_list(v, self.sep, normalize=False) or [])
            data = {f"label__{c}": lists.map(lambda l, c=c: int(c in l)).astype("int8") for c in self.classes_}
            return pd.DataFrame(data, index=idx)
        if t in ("regression", "count"):
            ys = pd.to_numeric(pd.Series(y), errors="coerce").astype(float)
            tr = self.transform_name
            if tr == "log1p":
                ys = np.log1p(ys)
            elif tr == "sqrt":
                ys = np.sqrt(ys)
            elif tr in ("yeo-johnson", "box-cox"):
                m = ys.notna()
                arr = ys[m].to_numpy().reshape(-1, 1)
                if tr == "box-cox":
                    arr = np.clip(arr, 1e-9, None)
                ys[m] = self.power_.transform(arr).ravel()
            return pd.Series(ys.to_numpy(), index=idx, name="target")
        return y

    def fit_transform(self, y):
        return self.fit(y).transform(y)

    def inverse_transform(self, pred):
        """Back to the original scale/labels. For multiclass, pass class codes (argmax of probabilities)."""
        if self.mode == "none":
            return pred
        t = self.task
        p = np.asarray(pred)
        if t == "binary":
            return np.where(p >= 0.5, self.classes_[1], self.classes_[0]) if p.dtype.kind == "f" else \
                np.array(self.classes_, dtype=object)[p.astype(int)]
        if t == "multiclass":
            return np.array(self.classes_, dtype=object)[p.astype(int)] if self.encode else p
        if t in ("regression", "count"):
            tr = self.transform_name
            if tr == "log1p":
                return np.expm1(p)
            if tr == "sqrt":
                return np.square(p)
            if tr in ("yeo-johnson", "box-cox"):
                return self.power_.inverse_transform(p.reshape(-1, 1)).ravel()
            return p
        return pred

    def class_weights(self, y_encoded) -> dict:
        """Balanced class weights (n / (k * n_c)) for binary/multiclass encoded targets."""
        vc = pd.Series(y_encoded).value_counts()
        n, k = vc.sum(), len(vc)
        return {c: float(n / (k * cnt)) for c, cnt in vc.items()}

    def describe(self) -> dict:
        d = {"task": self.task, "mode": self.mode}
        if hasattr(self, "classes_"):
            d["classes"] = [str(c) for c in self.classes_]
        if self.task == "binary":
            d["positive_class"] = str(self.positive_class)
        if self.task in ("regression", "count"):
            d["transform"] = self.transform_name
        return d
