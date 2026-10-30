"""Datetime features.

Method "auto" (default) builds the parts listed in ``parts``; "passthrough" keeps the parsed datetime.

Per-column params:
  parts        any of: year, quarter, month, week, day, dayofweek, dayofyear, hour, minute,
               is_weekend, is_month_start, is_month_end
               default ["year", "month", "dayofweek", "hour", "is_weekend"]; parts with a single value
               in train (e.g. hour when there is no time of day) are dropped automatically
  cyclical     list of parts to also encode as sin/cos (default ["month", "dayofweek", "hour"])
  elapsed_from  None | "min" (days since the earliest train date) | a fixed date string
               -> <col>__days_since
  reference_col  name of another datetime column: <col>__days_from_<ref> = col - ref, in days
               (e.g. delivery_date - order_date). The reference column must be in the frame.
  holidays     None | country code like "IL" or "US" (needs `pip install holidays`) -> <col>__is_holiday
  weekend_days  days counted as weekend (default (5, 6) = Sat, Sun; for Israel use (4, 5) = Fri, Sat)
  dayfirst     passed to pd.to_datetime when parsing strings
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseColumnProcessor

_PERIOD = {"month": 12, "dayofweek": 7, "hour": 24, "minute": 60, "dayofyear": 366, "week": 53, "quarter": 4, "day": 31}


def _parse(s, dayfirst=False):
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    return pd.to_datetime(s, errors="coerce", dayfirst=dayfirst)


class DatetimeProcessor(BaseColumnProcessor):
    default_method = "auto"
    valid_methods = ("auto", "passthrough")

    def __init__(self, spec=None, keep_original=False, parts=("year", "month", "dayofweek", "hour", "is_weekend"),
                 cyclical=("month", "dayofweek", "hour"), elapsed_from=None, reference_col=None,
                 holidays=None, weekend_days=(5, 6), dayfirst=False):
        super().__init__(spec=spec, keep_original=keep_original)
        self.parts = parts
        self.cyclical = cyclical
        self.elapsed_from = elapsed_from
        self.reference_col = reference_col
        self.holidays = holidays
        self.weekend_days = weekend_days
        self.dayfirst = dayfirst

    # the reference column lives outside the processed column, so keep the full frame around
    def _transform_impl(self, X, use_oof=False, collect_info=False):
        self._frame_ = X
        try:
            return super()._transform_impl(X, use_oof, collect_info)
        finally:
            self._frame_ = None

    def _fit_impl(self, X, y, task, group_col=None, time_col=None):
        self._frame_ = X
        return super()._fit_impl(X, y, task, group_col, time_col)

    @staticmethod
    def _part(d, part, weekend_days):
        if part == "week":
            return d.dt.isocalendar().week.astype(float)
        if part == "is_weekend":
            return d.dt.dayofweek.isin(weekend_days).astype(float).where(d.notna())
        if part in ("is_month_start", "is_month_end"):
            return getattr(d.dt, part).astype(float).where(d.notna())
        return getattr(d.dt, part).astype(float)

    def _fit_column(self, s, y, p):
        col = s.name
        d = _parse(s, p.get("dayfirst", False))
        n_bad = int(d.isna().sum() - s.isna().sum())
        if n_bad:
            self._log(col, "parse", f"{n_bad} unparseable values -> NaT")
        st: dict = {}
        if p["method"] == "passthrough":
            return st
        keep = []
        for part in p.get("parts") or []:
            v = self._part(d, part, p.get("weekend_days", (5, 6)))
            if v.nunique(dropna=True) > 1:
                keep.append(part)
        dropped = [x for x in (p.get("parts") or []) if x not in keep]
        if dropped:
            self._log(col, "parts", f"dropped constant parts: {dropped}")
        st["parts"] = keep
        st["cyclical"] = [c for c in (p.get("cyclical") or []) if c in keep]
        ef = p.get("elapsed_from")
        if ef is not None:
            st["origin"] = d.min() if ef == "min" else pd.Timestamp(ef)
            self._log(col, "days_since", f"origin {st['origin']}")
        ref = p.get("reference_col")
        if ref:
            if ref not in self._frame_.columns:
                raise KeyError(f"reference_col '{ref}' for '{col}' not in data")
            st["ref"] = ref
        hol = p.get("holidays")
        if hol:
            try:
                import holidays as hl
            except ImportError as e:
                raise ImportError("holidays=... needs `pip install holidays`") from e
            years = sorted(set(d.dropna().dt.year.astype(int)))
            years = list(range(min(years) - 1, max(years) + 3)) if years else []
            st["holiday_set"] = set(pd.Timestamp(x) for x in hl.country_holidays(hol, years=years).keys())
            self._log(col, "holidays", f"{hol}, years {years[0] if years else ''}-{years[-1] if years else ''}")
        self._log(col, "parts", f"{keep}; cyclical {st['cyclical']}")
        return st

    def _transform_column(self, s, st):
        p = st["params"]
        col = s.name
        d = _parse(s, p.get("dayfirst", False))
        if p["method"] == "passthrough":
            return pd.DataFrame({col: d}, index=s.index)
        out = {}
        wk = p.get("weekend_days", (5, 6))
        for part in st["parts"]:
            v = self._part(d, part, wk)
            out[self._name(col, part)] = v
            if part in st["cyclical"]:
                per = _PERIOD.get(part, 1)
                out[self._name(col, f"{part}_sin")] = np.sin(2 * np.pi * v / per)
                out[self._name(col, f"{part}_cos")] = np.cos(2 * np.pi * v / per)
        if "origin" in st:
            out[self._name(col, "days_since")] = (d - st["origin"]).dt.total_seconds() / 86400
        if "ref" in st:
            r = _parse(self._frame_.loc[s.index, st["ref"]], p.get("dayfirst", False))
            out[self._name(col, f"days_from_{st['ref']}")] = (d - r).dt.total_seconds() / 86400
        if "holiday_set" in st:
            out[self._name(col, "is_holiday")] = d.dt.normalize().isin(st["holiday_set"]).astype(float).where(d.notna())
        return pd.DataFrame(out, index=s.index)
