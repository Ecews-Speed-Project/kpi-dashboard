"""Weekly -> monthly rolling merge for the two DHIS2 instances.

The rule, in one line:
    for every facility x month x table, use the MONTHLY report if it exists,
    otherwise the sum of the WEEKLY reports that fall in that month.

So while a month is open, the weeks keep adding up ("real time"). As soon as
a facility submits its monthly report, that facility's weeks for the month are
dropped and the monthly value replaces them. Nothing is ever double counted,
and a late facility keeps showing its weekly numbers until it reports.

Input frames (long format, one row per value) need these columns:
    table, period, facility_key, state, lga, facility, dataname, disagg, value
"""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

WEEK_RE = re.compile(r"^(\d{4})W(\d{1,2})$")
MONTH_RE = re.compile(r"^(\d{4})(\d{2})$")

KEYS = ["table", "facility_key", "month"]
DIMS = ["table", "month", "facility_key", "state", "lga", "facility", "dataname", "disagg"]


def week_bounds(period: str) -> tuple[dt.date, dt.date]:
    """DHIS2 ISO weekly period '2026W41' -> (Monday, Sunday)."""
    m = WEEK_RE.match(period)
    if not m:
        raise ValueError(f"Not a weekly period: {period}")
    monday = dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return monday, monday + dt.timedelta(days=6)


def week_to_month(period: str, rule: str = "thursday") -> str:
    """Assign an ISO week to a month (YYYY-MM) using the configured rule."""
    mon, sun = week_bounds(period)
    day = {"thursday": mon + dt.timedelta(days=3), "week_end": sun, "week_start": mon}[rule]
    return f"{day.year}-{day.month:02d}"


def _wk_key(period: str):
    """Sort DHIS2 weeks by date: '2026W9' comes before '2026W41'."""
    m = WEEK_RE.match(str(period))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def period_start(period: str) -> dt.date:
    """First day of a DHIS2 weekly ('2026W41') or monthly ('202609') period;
    date.min for any other period type."""
    p = str(period)
    if WEEK_RE.match(p):
        return week_bounds(p)[0]
    m = MONTH_RE.match(p)
    return dt.date(int(m.group(1)), int(m.group(2)), 1) if m else dt.date.min


def month_of(period: str) -> str:
    m = MONTH_RE.match(period)
    if not m:
        raise ValueError(f"Not a monthly period: {period}")
    return f"{m.group(1)}-{m.group(2)}"


def merge_weekly_monthly(weekly: pd.DataFrame, monthly: pd.DataFrame,
                         rule: str = "thursday", takeover: str = "facility_table",
                         closed_months: set[str] | None = None, keep_weeks: bool = False) -> pd.DataFrame:
    """Return the merged fact table with provenance columns:
        source          'monthly' | 'weekly'
        weeks           comma list of weeks used (weekly rows only)
    """
    w = weekly.copy()
    m = monthly.copy()
    w["month"] = w["period"].map(lambda p: week_to_month(p, rule))
    m["month"] = m["period"].map(month_of)

    if takeover == "facility_table":
        submitted = m[KEYS].drop_duplicates()
        w = w.merge(submitted.assign(_has_monthly=True), on=KEYS, how="left")
        w = w[w["_has_monthly"].isna()].drop(columns="_has_monthly")
    elif takeover == "all":
        closed = closed_months or set()
        w = w[~w["month"].isin(closed)]
        m = m[m["month"].isin(closed)]
    else:
        raise ValueError(f"Unknown monthly_takeover: {takeover}")

    if keep_weeks:   # one row per week, so a date range can pick individual weeks
        w_agg = (w.groupby(DIMS + ["period"], dropna=False)["value"].sum().reset_index()
                   .assign(source="weekly"))
        w_agg["weeks"] = w_agg["period"]
        m_agg = (m.groupby(DIMS + ["period"], dropna=False)["value"].sum().reset_index()
                   .assign(source="monthly", weeks=""))
        return pd.concat([m_agg, w_agg], ignore_index=True).sort_values(DIMS).reset_index(drop=True)

    w_agg = (w.groupby(DIMS, dropna=False)
               .agg(value=("value", "sum"), weeks=("period", lambda s: ",".join(sorted(set(s), key=_wk_key))))
               .reset_index()
               .assign(source="weekly"))
    m_agg = (m.groupby(DIMS, dropna=False)["value"].sum().reset_index()
               .assign(source="monthly", weeks=""))
    out = pd.concat([m_agg, w_agg], ignore_index=True)
    return out.sort_values(DIMS).reset_index(drop=True)


def month_status(merged: pd.DataFrame, n_facilities: int, as_of: dt.date,
                 completeness: float = 0.90, close_day: int = 15) -> pd.DataFrame:
    """Per month: how many facilities are on monthly vs weekly data, and
    whether the month counts as closed (for display and for 'all' takeover)."""
    f = (merged.groupby(["month", "source"])["facility_key"].nunique()
               .unstack(fill_value=0)
               .reindex(columns=["monthly", "weekly"], fill_value=0)
               .reset_index())
    f["weeks"] = f["month"].map(
        merged[merged.source == "weekly"].groupby("month")["weeks"]
        .agg(lambda s: ",".join(sorted({w for x in s for w in x.split(",") if w}, key=_wk_key))))
    f["weeks"] = f["weeks"].fillna("")
    f["monthly_share"] = f["monthly"] / max(n_facilities, 1)

    def closed(row):
        y, mo = map(int, row["month"].split("-"))
        nxt = dt.date(y + (mo == 12), mo % 12 + 1, 1)
        past_cutoff = as_of >= nxt.replace(day=close_day)
        return bool(row["monthly_share"] >= completeness or (past_cutoff and row["monthly"] > 0))

    f["closed"] = f.apply(closed, axis=1)
    return f.rename(columns={"monthly": "facilities_monthly", "weekly": "facilities_weekly"})
