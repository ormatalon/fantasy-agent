"""FeatureProcessor - runs several column processors together and keeps their bookkeeping.

    fp = FeatureProcessor(
        steps={
            "num":  NumericProcessor({"age": None, "income": {"cap": (0.01, 0.99), "transform": "log1p"}},
                                     impute="median"),
            "cat":  CategoricalProcessor({"city": "target", "color": "onehot"}),
            "text": TextProcessor({"review": ["stats", "tfidf"]}, svd_components=50),
        },
        passthrough=["already_clean_flag"],
        task="binary",
    )
    X_train = fp.fit_transform(train, y_train)     # target-based encodings are out-of-fold here
    X_test  = fp.transform(test)

Columns not listed in any step or in ``passthrough`` are dropped (and listed in ``fp.unused_columns_``).
``fit(train)`` + ``transform(train)`` equals ``fit_transform(train)``: target encodings of the training
rows are out-of-fold either way.
Using a pipeline is optional: ``fp.to_sklearn()`` returns an sklearn Pipeline-compatible object.
"""
from __future__ import annotations

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class FeatureProcessor(BaseEstimator, TransformerMixin):
    """Runs column processors together.

    group_col / time_col : folds for out-of-fold encodings (target encoding, group stats on the target)
        follow the split - pass the entity column for a group split, the time column for a time split.
        Default: random (stratified for classification) folds.
    """

    def __init__(self, steps: dict | None = None, passthrough=(), task: str | None = None,
                 drop_unused: bool = True, group_col: str | None = None, time_col: str | None = None):
        self.steps = steps
        self.passthrough = passthrough
        self.task = task
        self.drop_unused = drop_unused
        self.group_col = group_col
        self.time_col = time_col

    def fit(self, X, y=None):
        self._run(X, y, fit=True)
        return self

    def fit_transform(self, X, y=None, **_):
        return self._run(X, y, fit=True)

    def transform(self, X):
        """Same columns, same order as in fit. Missing input columns raise; nothing is filled silently.
        The training frame itself gets its out-of-fold target encodings."""
        if not hasattr(self, "feature_names_"):
            raise RuntimeError("FeatureProcessor is not fitted")
        out = self._run(X, None, fit=False)
        if list(out.columns) != self.feature_names_:
            missing = [c for c in self.feature_names_ if c not in out.columns]
            extra = [c for c in out.columns if c not in self.feature_names_]
            if missing or extra:
                raise ValueError(f"Output columns differ from fit: missing {missing[:10]}, extra {extra[:10]}")
            out = out[self.feature_names_]
        return out

    def _run(self, X, y, fit):
        steps = self.steps or {}
        absent = [c for c in self.passthrough if c not in X.columns]
        if absent:
            raise KeyError(f"passthrough columns not in data: {absent}")
        parts, used = [], set()
        for name, proc in steps.items():
            if fit:
                res = proc.fit_transform(X, y, task=self.task, group_col=self.group_col, time_col=self.time_col)
            else:
                res = proc.transform(X)
            used |= set(proc.columns)
            parts.append(res)
        pt = list(self.passthrough)
        if pt:
            parts.insert(0, X[pt])
            used |= set(pt)
        out = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=X.index)
        if not self.drop_unused:
            rest = [c for c in X.columns if c not in used and c not in out.columns]
            out = pd.concat([out, X[rest]], axis=1)
        dup = out.columns[out.columns.duplicated()].tolist()
        if dup:
            raise ValueError(f"Duplicate output column names: {dup[:10]}. Rename or adjust the specs.")
        if fit:
            self.feature_names_ = list(out.columns)
            self.unused_columns_ = [c for c in X.columns if c not in used]
            overlap = self._overlap()
            if overlap:
                self.overlap_warning_ = overlap
        return out

    def _overlap(self):
        seen, dup = {}, []
        for name, proc in (self.steps or {}).items():
            for c in proc.columns:
                if c in seen:
                    dup.append((c, seen[c], name))
                seen[c] = name
        return dup

    # ---------- bookkeeping ----------
    @property
    def log(self) -> pd.DataFrame:
        rows = []
        for name, proc in (self.steps or {}).items():
            for r in getattr(proc, "log_", []):
                rows.append({"step": name, **r})
        return pd.DataFrame(rows)

    def feature_dictionary(self) -> pd.DataFrame:
        rows = []
        for name, proc in (self.steps or {}).items():
            for f in getattr(proc, "feature_info_", []):
                rows.append({"step": name, **f, "params": _short_params(proc.col_specs_.get(f["source"], {}))})
        for c in self.passthrough:
            rows.append({"step": "passthrough", "feature": c, "source": c, "processor": "-", "method": "passthrough",
                         "dtype": "", "params": ""})
        fd = pd.DataFrame(rows)
        if len(fd) and hasattr(self, "feature_names_"):
            fd = fd[fd["feature"].isin(self.feature_names_)]
        return fd.reset_index(drop=True)

    def cat_features(self) -> list[str]:
        """Output columns to pass to CatBoost ``cat_features`` / LightGBM ``categorical_feature``."""
        out = []
        for proc in (self.steps or {}).values():
            if hasattr(proc, "get_cat_features"):
                out += proc.get_cat_features()
        return out

    def text_features(self) -> list[str]:
        out = []
        for proc in (self.steps or {}).values():
            if hasattr(proc, "get_text_features"):
                out += proc.get_text_features()
        return out

    def target_encoded_features(self) -> list[str]:
        """Outputs built from the target (target encoding, group stats on '__target__'). Their train values
        are out-of-fold, so train-vs-test drift checks on them are not meaningful - exclude them."""
        out = []
        for proc in (self.steps or {}).values():
            for f in getattr(proc, "feature_info_", []):
                p = proc.col_specs_.get(f["source"], {})
                m = f["method"] if isinstance(f["method"], (list, tuple)) else [f["method"]]
                if "target" in m or p.get("value") == "__target__":
                    out.append(f["feature"])
        return out

    def to_sklearn(self):
        """The processor already follows the sklearn fit/transform API; returned as is for use in a Pipeline."""
        return self


def _short_params(p: dict) -> str:
    keep = {k: v for k, v in p.items() if v not in (None, False, (), []) and k not in ("random_state",)}
    return ", ".join(f"{k}={v}" for k, v in keep.items())
