"""Read the EDA handoff (eda_decisions.yml), apply user overrides, re-run the cleaning on raw data,
and suggest a starting feature spec.

Typical use:
    dec = load_decisions("eda_decisions.yml")
    print(decisions_summary(dec))                       # what the EDA recommends, by status
    dec = apply_overrides(dec, {"recommendations.target.transform": None,
                                "validation.split": "random"})
    df, clean_log = apply_cleaning(df_raw, dec)         # applied + recommended steps
    specs = suggest_specs(dec)                          # editable starting point
"""
from __future__ import annotations

import copy
import re
from typing import Any

import numpy as np
import pandas as pd
import yaml

DEFAULT_NULL_TOKENS = ["NA", "N/A", "n/a", "null", "NULL", "None", "", " ", "?", "-"]


# ---------------------------------------------------------------- load / override / summary
def load_decisions(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    for key in ("meta", "problem", "columns", "cleaning", "recommendations", "validation"):
        d.setdefault(key, {})
    d.setdefault("open_questions", [])
    d["_overrides"] = []
    return d


def _get(d: dict, dotted: str):
    cur = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def apply_overrides(dec: dict, overrides: dict[str, Any]) -> dict:
    """Override any key by dotted path, e.g. {"problem.positive_class": "yes",
    "recommendations.capping": [], "validation.split": "none"}. Every override is recorded
    in dec["_overrides"] (EDA value vs. user value) for the processing log."""
    dec = copy.deepcopy(dec)
    for path, value in overrides.items():
        keys = path.split(".")
        cur = dec
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        old = cur.get(keys[-1])
        cur[keys[-1]] = value
        dec["_overrides"].append({"key": path, "eda_value": old, "user_value": value})
    return dec


def decisions_summary(dec: dict) -> pd.DataFrame:
    """Flat table of every recommendation / cleaning step with its status, for review with the user."""
    rows = []
    for section in ("cleaning", "recommendations"):
        for key, val in (dec.get(section) or {}).items():
            items = val if isinstance(val, list) else ([val] if isinstance(val, dict) else [])
            if key == "duplicates" and isinstance(val, dict):
                items = [{"column": k, **v} for k, v in val.items() if isinstance(v, dict)]
            if key == "target" and isinstance(val, dict):
                items = [{"column": "<target>", **val}]
            for it in items:
                if not isinstance(it, dict):
                    continue
                col = it.get("column", it.get("columns"))
                status = it.get("status", it.get("verdict", "recommended"))
                detail = {k: v for k, v in it.items() if k not in ("column", "columns", "status", "verdict", "reason")}
                rows.append({"section": section, "step": key, "column": col, "status": status,
                             "detail": detail, "reason": it.get("reason", "")})
    return pd.DataFrame(rows)


def open_questions(dec: dict) -> list[str]:
    qs = list(dec.get("open_questions") or [])
    s = decisions_summary(dec)
    if len(s):
        for _, r in s[s["status"] == "check_with_user"].iterrows():
            qs.append(f"{r['step']} for {r['column']}: {r['detail']} ({r['reason']})")
    return qs


# ---------------------------------------------------------------- cleaning
# Fixed vocabulary for actions in eda_decisions.yml. Anything else is reported, never guessed.
ACTIONS = {
    "parsing_fixes": ("to_numeric", "to_numeric_eu", "percent_to_fraction", "to_datetime", "manual"),
    "duplicates.full_row": ("drop", "keep_all"),
    "duplicates.conflicting_key": ("keep_first", "keep_last", "drop_all", "keep_all"),
    "invalid_values": ("set_nan", "drop_row"),
}


def _quote_columns(rule: str, columns) -> str:
    """Wrap column names that are not valid identifiers (spaces, dashes, Hebrew is fine) in backticks,
    so the rule works with DataFrame.query. Already-quoted names are left alone."""
    for c in sorted((c for c in map(str, columns) if not c.isidentifier()), key=len, reverse=True):
        rule = re.sub(rf"(?<!`){re.escape(c)}(?!`)", f"`{c}`", rule)
    return rule


def _to_numeric(s: pd.Series, style: str) -> pd.Series:
    """us: 1,234.5 / $1 234 -> 1234.5;  eu: 1.234,5 -> 1234.5;  percent: 12% -> 0.12 (plain 0.12 stays)."""
    vals, pct = [], []
    for v in s.to_numpy(dtype=object):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            vals.append(None); pct.append(False)
            continue
        if isinstance(v, (int, float, np.integer, np.floating)):
            vals.append(str(v)); pct.append(False)
            continue
        t = str(v).strip().replace(" ", "").replace("\u00a0", "")
        t = t.replace(".", "").replace(",", ".") if style == "eu" else t.replace(",", "")
        pct.append(t.endswith("%"))
        vals.append(re.sub(r"[^0-9.\-eE]", "", t))
    out = pd.to_numeric(pd.Series(vals, index=s.index, dtype=object), errors="coerce").astype(float)
    if style == "percent":
        out = out.where(~np.array(pct), out / 100)
    return out


def apply_cleaning(df_raw: pd.DataFrame, dec: dict, include_status=("applied", "recommended"),
                   skip_steps=(), drop_leakage=True, drop_columns=True,
                   drop_rows=True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Re-apply the EDA cleaning to the raw data, in a fixed order. Returns (df, log).

    Steps: parsing fixes -> null tokens -> sentinels -> spelling -> invalid values ->
           duplicates -> outlier removals -> drop leakage / recommended drops.
    Only entries whose status is in ``include_status`` are applied; ``check_with_user`` entries are
    skipped and logged so they can be raised. ``skip_steps`` takes step names to skip entirely.
    Actions must use the fixed vocabulary in ``ACTIONS``; an unknown action is logged as
    "unknown_action" and skipped.

    drop_rows=False keeps every row: value fixes are applied, invalid values are set to NaN instead of
    dropping the row, and duplicate removal is only reported ("would drop n"). Use it for a test set
    that was delivered already split, so the evaluation data keeps the rows production will see.
    """
    df = df_raw.copy()
    log: list[dict] = []
    cl = dec.get("cleaning") or {}

    def ok(item):
        return item.get("status", "recommended") in include_status

    def add(step, column, action, n=None, note=""):
        log.append({"step": step, "column": column, "action": action, "n_affected": n, "note": note})

    def known(step, action, column):
        if action in ACTIONS[step]:
            return True
        add(step.split(".")[0], column, "unknown_action",
            note=f"'{action}' is not one of {ACTIONS[step]} - fix eda_decisions.yml or handle it manually")
        return False

    # 1. parsing fixes
    if "parsing_fixes" not in skip_steps:
        for it in cl.get("parsing_fixes") or []:
            col, fix = it.get("column"), it.get("fix")
            if col not in df.columns:
                continue
            if not ok(it):
                add("parsing_fixes", col, "skipped", note=f"status={it.get('status')}: {fix}")
                continue
            if not known("parsing_fixes", fix, col):
                continue
            before = df[col].copy()
            if fix == "to_datetime":
                df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=bool(it.get("dayfirst", False)),
                                         format=it.get("format"))
            elif fix == "to_numeric":
                df[col] = _to_numeric(df[col], "us")
            elif fix == "to_numeric_eu":
                df[col] = _to_numeric(df[col], "eu")
            elif fix == "percent_to_fraction":
                df[col] = _to_numeric(df[col], "percent")
            else:
                add("parsing_fixes", col, "manual", note=it.get("note", "write it in the project module"))
                continue
            n_lost = int((df[col].isna() & before.notna()).sum())
            add("parsing_fixes", col, fix, int(before.notna().sum()),
                f"{n_lost} values could not be parsed -> NaN" if n_lost else "")

    # 2. null tokens
    if "null_tokens" not in skip_steps:
        tokens = [t.strip() for t in (cl.get("null_tokens") or DEFAULT_NULL_TOKENS)]
        for col in df.columns:
            if df[col].dtype != object and not pd.api.types.is_string_dtype(df[col]):
                continue
            mask = df[col].map(lambda v: isinstance(v, str) and v.strip() in tokens)
            n = int(mask.sum())
            if n:
                df.loc[mask, col] = np.nan
                add("null_tokens", col, "to NaN", n)

    # 3. sentinels
    if "sentinels_to_nan" not in skip_steps:
        for it in cl.get("sentinels_to_nan") or []:
            col = it.get("column")
            if col not in df.columns:
                continue
            if not ok(it):
                add("sentinels_to_nan", col, "skipped", note=f"status={it.get('status')} values={it.get('values')}")
                continue
            vals = it.get("values") or []
            mask = df[col].isin(vals) | df[col].astype(str).isin([str(v) for v in vals])
            n = int(mask.sum())
            df.loc[mask, col] = np.nan
            add("sentinels_to_nan", col, f"{vals} -> NaN", n, it.get("reason", ""))

    # 4. spelling mappings
    if "spelling_mappings" not in skip_steps:
        for it in cl.get("spelling_mappings") or []:
            col = it.get("column")
            if col not in df.columns:
                continue
            if not ok(it):
                add("spelling_mappings", col, "skipped", note=f"status={it.get('status')}")
                continue
            mp = it.get("mapping") or {}
            mask = df[col].isin(list(mp.keys()))
            df[col] = df[col].replace(mp)
            add("spelling_mappings", col, f"{len(mp)} variants merged", int(mask.sum()))

    # 5. invalid values (rule is a pandas query that selects the INVALID rows, e.g. "age < 0")
    if "invalid_values" not in skip_steps:
        for it in cl.get("invalid_values") or []:
            col, action = it.get("column"), it.get("action", "set_nan")
            if col not in df.columns:
                continue
            if not ok(it):
                add("invalid_values", col, "skipped", note=f"status={it.get('status')}: {it.get('rule')}")
                continue
            if not known("invalid_values", action, col):
                continue
            rule = _quote_columns(str(it.get("rule")), df.columns)
            try:
                idx = df.query(rule).index
            except Exception as e:
                add("invalid_values", col, "manual", note=f"rule '{rule}' is not a valid query ({e})")
                continue
            if action == "drop_row" and drop_rows:
                df = df.drop(index=idx)
                add("invalid_values", col, "rows dropped", len(idx), rule)
            else:
                df.loc[idx, col] = np.nan
                note = rule if action == "set_nan" else f"{rule} (drop_row -> NaN because drop_rows=False)"
                add("invalid_values", col, "set NaN", len(idx), note)

    # 6. duplicates
    if "duplicates" not in skip_steps:
        dup = cl.get("duplicates") or {}
        fr = dup.get("full_row") or {}
        if fr and fr.get("count") != 0:
            action = fr.get("action", "drop")
            if not ok(fr):
                add("duplicates", "<all>", "skipped", note=f"full rows: status={fr.get('status')}")
            elif known("duplicates.full_row", action, "<all>") and action == "drop":
                n_dup = int(df.duplicated().sum())
                if drop_rows:
                    df = df.drop_duplicates(keep="first")
                    add("duplicates", "<all>", "full-row duplicates dropped (first kept)", n_dup)
                else:
                    add("duplicates", "<all>", "kept (drop_rows=False)", n_dup, f"would drop {n_dup}")
            elif action == "keep_all":
                add("duplicates", "<all>", "kept - legitimate repeated rows", int(df.duplicated().sum()))
        ck = dup.get("conflicting_key") or {}
        key = (dec.get("problem") or {}).get("key") or []
        if ck and ck.get("count") != 0 and key and all(k in df.columns for k in key):
            action = ck.get("action", "keep_all")
            n_dup = int(df.duplicated(subset=key, keep=False).sum())
            if not ok(ck):
                add("duplicates", ",".join(key), "skipped", note=f"conflicting keys: status={ck.get('status')}")
            elif known("duplicates.conflicting_key", action, ",".join(key)) and action != "keep_all":
                keep = {"keep_first": "first", "keep_last": "last", "drop_all": False}[action]
                if drop_rows:
                    n0 = len(df)
                    df = df.drop_duplicates(subset=key, keep=keep)
                    add("duplicates", ",".join(key), f"conflicting keys: {action}", n0 - len(df))
                else:
                    add("duplicates", ",".join(key), "kept (drop_rows=False)", n_dup,
                        f"{n_dup} rows share a key; {action} not applied")
            elif action == "keep_all":
                add("duplicates", ",".join(key), "kept", n_dup, "conflicting keys kept as they are")

    # 7. outlier removals (threshold: [low, high] values; values outside become NaN)
    if "outlier_removals" not in skip_steps:
        for it in cl.get("outlier_removals") or []:
            col = it.get("column")
            if col not in df.columns:
                continue
            if not ok(it):
                add("outlier_removals", col, "skipped", note=f"status={it.get('status')}")
                continue
            thr = it.get("threshold")
            if isinstance(thr, (list, tuple)) and len(thr) == 2:
                lo, hi = thr
                x = pd.to_numeric(df[col], errors="coerce")
                mask = (x < (lo if lo is not None else -np.inf)) | (x > (hi if hi is not None else np.inf))
                df.loc[mask, col] = np.nan
                add("outlier_removals", col, f"outside {thr} -> NaN", int(mask.sum()), it.get("reason", ""))
            else:
                add("outlier_removals", col, "manual", note=f"threshold '{thr}' is not [low, high]")

    # 8. drops
    rec = dec.get("recommendations") or {}
    if drop_leakage and "leakage" not in skip_steps:
        for it in rec.get("leakage") or []:
            col = it.get("column")
            if col in df.columns and it.get("verdict") == "drop":
                df = df.drop(columns=col)
                add("leakage", col, "dropped", note=it.get("evidence", ""))
            elif col in df.columns and it.get("verdict") == "check_with_user":
                add("leakage", col, "kept - check with user", note=it.get("evidence", ""))
    if drop_columns and "drop_columns" not in skip_steps:
        for it in rec.get("drop_columns") or []:
            col = it.get("column")
            if col in df.columns and ok(it):
                df = df.drop(columns=col)
                add("drop_columns", col, "dropped", note=it.get("reason", ""))
    return df, pd.DataFrame(log, columns=["step", "column", "action", "n_affected", "note"])


# ---------------------------------------------------------------- feature spec suggestion
def suggest_specs(dec: dict, model_family: str = "generic") -> dict:
    """Starting-point specs for each processor, derived from the column types and recommendations.

    model_family:
      "catboost" - categoricals and text kept native, no scaling, no imputation
      "gbm"      - LightGBM/XGBoost/HistGB: ordinal/count encodings, NaN left in place
      "linear"   - onehot, imputation + missing indicators, scaling, transforms
      "generic"  - a middle ground (onehot low-card, frequency high-card, median impute, no scaling)
    The output is a plain dict meant to be printed, edited by the user, and passed to the processors.
    """
    cols = dec.get("columns") or {}
    rec = dec.get("recommendations") or {}
    target = (dec.get("problem") or {}).get("target")
    drop = {it.get("column") for it in rec.get("drop_columns") or []}
    drop |= {it.get("column") for it in rec.get("leakage") or [] if it.get("verdict") == "drop"}
    by_type: dict[str, list[str]] = {}
    for c, info in cols.items():
        if c == target or c in drop:
            continue
        by_type.setdefault((info or {}).get("type", "unknown"), []).append(c)

    mi = {it["column"] for it in rec.get("missing_indicators") or [] if "column" in it}
    caps = {it["column"]: (it.get("lower_pct", 0.01), it.get("upper_pct", 0.99)) for it in rec.get("capping") or [] if "column" in it}
    trans = {it["column"]: it.get("transform") for it in rec.get("transforms") or [] if "column" in it}
    manual = {it["column"]: it.get("kind") for it in rec.get("manual_treatment") or [] if "column" in it}
    fam = model_family

    numeric = {}
    for c in by_type.get("continuous", []) + by_type.get("low_card_numeric", []):
        p: dict = {}
        if c in mi:
            p["missing_indicator"] = True
        if c in caps:
            p["cap"] = list(caps[c])
        if c in trans and fam in ("linear", "generic"):
            p["transform"] = trans[c]
        if fam == "linear":
            p.update({"impute": "median", "scale": "standard"})
            p.setdefault("missing_indicator", (cols[c] or {}).get("missing_pct", 0) > 0)
        elif fam == "generic":
            p["impute"] = "median"
        numeric[c] = p or None

    cat_method = {"catboost": "native", "gbm": "ordinal", "linear": "onehot", "generic": "onehot"}[fam]
    hc_method = {"catboost": "native", "gbm": "target", "linear": "target", "generic": "frequency"}[fam]
    categorical = {c: cat_method for c in by_type.get("categorical", [])}
    categorical.update({c: hc_method for c in by_type.get("high_card_categorical", [])})
    # EDA-recommended rare grouping wins over the method's automatic choice
    for it in rec.get("rare_level_grouping") or []:
        c = it.get("column")
        if c in categorical and categorical[c] != "native":
            categorical[c] = {"method": categorical[c], "group_rare": True}

    text_method = {"catboost": ["native", "stats"], "gbm": ["stats", "tfidf"], "linear": ["stats", "tfidf"],
                   "generic": ["stats", "tfidf"]}[fam]
    text = {c: ({"method": text_method, "svd_components": 50} if "tfidf" in text_method else text_method)
            for c in by_type.get("free_text", [])}
    lists = {c: ["stats", "multihot"] for c in by_type.get("list", [])}
    for c, kind in manual.items():
        if kind == "list" and c not in lists and c not in drop:
            lists[c] = ["stats", "multihot"]
        if kind == "free_text" and c not in text and c not in drop:
            text[c] = {"method": text_method}

    parts = {it["column"]: it.get("parts") for it in rec.get("datetime_parts") or [] if "column" in it}
    datetime = {c: ({"parts": parts[c]} if parts.get(c) else None) for c in by_type.get("datetime", [])}

    val = dec.get("validation") or {}
    prob = dec.get("problem") or {}
    split = val.get("split")
    fold_columns = {
        "group_col": (val.get("group_column") or prob.get("entity_column")) if split in ("group", "group_stratified") else None,
        "time_col": prob.get("time_column") if split == "time" else None,
    }
    return {
        "model_family": fam,
        "fold_columns": fold_columns,   # pass to FeatureProcessor(group_col=..., time_col=...)
        "numeric": numeric,
        "categorical": categorical,
        "text": text,
        "lists": lists,
        "datetime": datetime,
        "identifiers_dropped": by_type.get("identifier", []),
        "unknown_type": by_type.get("unknown", []),
    }


def to_python_literal(obj, indent=0) -> str:
    """Pretty-print a spec dict as Python code the user can paste and edit."""
    import pprint
    return pprint.pformat(obj, indent=1, width=100, sort_dicts=False)
