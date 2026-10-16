import datetime as dt

import pandas as pd

from etl.period_merge import merge_weekly_monthly, month_status, week_to_month


def row(table, period, fac, name, value):
    return dict(table=table, period=period, facility_key=fac, state="Ekiti", lga="Ido-Osi",
                facility=fac, dataname=name, disagg="", value=value)


def test_unpadded_weeks_sort_by_date():
    from etl.period_merge import _wk_key
    assert sorted(["2026W41", "2026W9", "2026W10"], key=_wk_key) == ["2026W9", "2026W10", "2026W41"]


def test_period_start():
    from etl.period_merge import period_start
    assert period_start("2026W41") == dt.date(2026, 10, 5)
    assert period_start("202609") == dt.date(2026, 9, 1)
    assert period_start("2026Q3") == dt.date.min


def test_week_to_month_rules():
    # 2026W40 = Mon 28 Sep – Sun 4 Oct; Thursday is 1 Oct
    assert week_to_month("2026W40", "thursday") == "2026-10"
    assert week_to_month("2026W40", "week_start") == "2026-09"
    assert week_to_month("2026W40", "week_end") == "2026-10"
    # 2026W53 = 28 Dec 2026 – 3 Jan 2027; Thursday 31 Dec -> December
    assert week_to_month("2026W53", "thursday") == "2026-12"
    assert week_to_month("2026W01", "thursday") == "2026-01"


def test_monthly_replaces_weeks_per_facility():
    weekly = pd.DataFrame([
        row("HTS_TST", "2026W36", "FTH", "HTS_TST", 100),   # Sep
        row("HTS_TST", "2026W37", "FTH", "HTS_TST", 110),   # Sep
        row("HTS_TST", "2026W36", "CSCC", "HTS_TST", 50),   # Sep, no monthly yet
        row("HTS_TST", "2026W37", "CSCC", "HTS_TST", 55),
        row("HTS_TST", "2026W41", "FTH", "HTS_TST", 120),   # Oct, open month
    ])
    monthly = pd.DataFrame([row("HTS_TST", "202609", "FTH", "HTS_TST", 470)])
    out = merge_weekly_monthly(weekly, monthly)

    fth_sep = out[(out.facility_key == "FTH") & (out.month == "2026-09")]
    assert len(fth_sep) == 1 and fth_sep.value.iloc[0] == 470 and fth_sep.source.iloc[0] == "monthly"

    cscc_sep = out[(out.facility_key == "CSCC") & (out.month == "2026-09")]
    assert cscc_sep.value.iloc[0] == 105 and cscc_sep.source.iloc[0] == "weekly"
    assert cscc_sep.weeks.iloc[0] == "2026W36,2026W37"

    fth_oct = out[(out.facility_key == "FTH") & (out.month == "2026-10")]
    assert fth_oct.value.iloc[0] == 120 and fth_oct.source.iloc[0] == "weekly"
    # Totals: 470 + 105 + 120, weekly FTH Sep (210) dropped
    assert out.value.sum() == 695


def test_takeover_is_per_table():
    weekly = pd.DataFrame([row("TB", "2026W36", "FTH", "TB_PREV_NUM", 9)])
    monthly = pd.DataFrame([row("HTS_TST", "202609", "FTH", "HTS_TST", 470)])
    out = merge_weekly_monthly(weekly, monthly)
    tb = out[out.table == "TB"]
    assert tb.value.iloc[0] == 9 and tb.source.iloc[0] == "weekly"


def test_month_status():
    weekly = pd.DataFrame([row("HTS_TST", "2026W41", "FTH", "HTS_TST", 1)])
    monthly = pd.DataFrame([row("HTS_TST", "202609", "FTH", "HTS_TST", 1),
                            row("HTS_TST", "202609", "CSCC", "HTS_TST", 1)])
    st = month_status(merge_weekly_monthly(weekly, monthly), n_facilities=2, as_of=dt.date(2026, 10, 10))
    sep = st[st.month == "2026-09"].iloc[0]
    octo = st[st.month == "2026-10"].iloc[0]
    assert sep.closed and not octo.closed and octo.weeks == "2026W41"


def test_keep_weeks_matches_aggregate():
    weekly = pd.DataFrame([row("HTS_TST", "2026W40", "FTH", "HTS_TST", 5),
                           row("HTS_TST", "2026W41", "FTH", "HTS_TST", 7),
                           row("HTS_TST", "2026W36", "FTH", "HTS_TST", 9)])
    monthly = pd.DataFrame([row("HTS_TST", "202609", "FTH", "HTS_TST", 40)])
    a = merge_weekly_monthly(weekly, monthly)
    k = merge_weekly_monthly(weekly, monthly, keep_weeks=True)
    assert a.value.sum() == k.value.sum() == 52
    assert sorted(k[k.source == "weekly"].period) == ["2026W40", "2026W41"]
