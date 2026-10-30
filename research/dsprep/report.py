"""Saving outputs: processed splits, fold ids, fitted processors, feature dictionary, processing log."""
from __future__ import annotations

import datetime as _dt
import os
import warnings

import pandas as pd
import yaml


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        try:
            import fastparquet  # noqa: F401
            return True
        except ImportError:
            return False


def save_frames(frames: dict[str, pd.DataFrame], out_dir: str, fmt: str = "parquet", prefix: str = "") -> dict[str, str]:
    """Write each frame as <out_dir>/<prefix><name>.<fmt>. Falls back to csv when no parquet engine
    is installed (category dtypes and list columns do not survive csv - prefer parquet)."""
    os.makedirs(out_dir, exist_ok=True)
    if fmt == "parquet" and not _has_parquet():
        warnings.warn("No parquet engine (pip install pyarrow); writing csv instead.")
        fmt = "csv"
    paths = {}
    for name, f in frames.items():
        p = os.path.join(out_dir, f"{prefix}{name}.{fmt}")
        if fmt == "parquet":
            f.to_parquet(p, index=True)
        else:
            f.to_csv(p, index=True)
        paths[name] = p
    return paths


def save_object(obj, path: str) -> str:
    """Persist fitted processors (FeatureProcessor, TargetProcessor) with joblib."""
    import joblib
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    joblib.dump(obj, path)
    return path


def _plain(v):
    """Make values yaml-safe."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def write_processing_record(path: str, *, decisions: dict | None = None, cleaning_log: pd.DataFrame | None = None,
                            target=None, split: dict | None = None, feature_processors: dict | None = None,
                            dropped_features: list | None = None, outputs: dict | None = None,
                            notes: list[str] | None = None) -> str:
    """Write processing_record.yml: what the EDA recommended, what the user overrode, and what was done.
    ``feature_processors`` maps an output name (e.g. "catboost", "linear") to a fitted FeatureProcessor."""
    import numpy as _np
    import sklearn as _sk

    from . import __version__ as _v
    rec: dict = {"created": _dt.date.today().isoformat(),
                 "versions": {"dsprep": _v, "pandas": pd.__version__, "numpy": _np.__version__,
                              "scikit-learn": _sk.__version__}}
    if decisions is not None:
        rec["eda_source"] = (decisions.get("meta") or {}).get("eda_notebook")
        rec["user_overrides"] = decisions.get("_overrides", [])
    if cleaning_log is not None and len(cleaning_log):
        rec["cleaning"] = cleaning_log.to_dict(orient="records")
    if target is not None:
        rec["target"] = target.describe() if hasattr(target, "describe") else target
        if hasattr(target, "log_"):
            rec["target"]["log"] = target.log_
    if split is not None:
        rec["split"] = split
    if feature_processors:
        rec["feature_sets"] = {}
        for name, fp in feature_processors.items():
            rec["feature_sets"][name] = {
                "n_features": len(fp.feature_names_),
                "cat_features": fp.cat_features(),
                "text_features": fp.text_features(),
                "unused_input_columns": fp.unused_columns_,
                "log": fp.log.to_dict(orient="records"),
            }
    if dropped_features:
        rec["dropped_features"] = dropped_features
    if outputs:
        rec["outputs"] = outputs
    if notes:
        rec["notes"] = notes
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(_plain(rec), f, sort_keys=False, allow_unicode=True)
    with open(path, encoding="utf-8") as f:
        yaml.safe_load(f)  # confirm it parses
    return path
