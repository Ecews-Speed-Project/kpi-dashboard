"""Weekly reports replaced by a monthly report are kept apart (period T<week>) for the weekly trend charts only."""
import pandas as pd

from etl.period_merge import merge_weekly_monthly, replaced_weeks

BASE = dict(state="Delta", lga="L", dataname="HTS_TST_POS", disagg="Male, 25-29")


def _row(table, period, fac, value):
    return dict(table=table, period=period, facility_key=fac, facility=fac.upper(), value=value, **BASE)


def test_replaced_weeks_keeps_only_the_weeks_a_monthly_report_replaced():
    w = pd.DataFrame([_row("HTS_TST", "2026W41", "a", 3), _row("HTS_TST", "2026W42", "a", 2), _row("HTS_TST", "2026W42", "b", 4)])
    m = pd.DataFrame([_row("HTS_TST", "202610", "a", 6)])
    merged = merge_weekly_monthly(w, m, keep_weeks=True)
    t = replaced_weeks(w, m, merged, since="2026-10")
    # facility a: both weeks replaced by its October report; facility b's week is still in the totals
    assert sorted(t["period"]) == ["T2026W41", "T2026W42"]
    assert set(t["facility_key"]) == {"a"} and t["value"].sum() == 5
    assert (t["source"] == "weekly").all() and t["period"].str.startswith("T").all()
    # the totals are unchanged: monthly for a, weekly for b
    assert merged["value"].sum() == 10


def test_replaced_weeks_respects_since():
    w = pd.DataFrame([_row("HTS_TST", "2026W36", "a", 1)])
    m = pd.DataFrame([_row("HTS_TST", "202609", "a", 1)])
    merged = merge_weekly_monthly(w, m, keep_weeks=True)
    assert replaced_weeks(w, m, merged, since="2026-10").empty
