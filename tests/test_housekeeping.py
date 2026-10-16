import datetime as dt
from pathlib import Path

from dashboard.build import thin_archive
from kpi.measures import week_ends


def _f(stamp):
    return Path(f"SPEED_dashboard_{stamp}.html")


def test_thin_archive_keeps_today_last_per_day_then_last_per_month():
    today = dt.date(2026, 10, 12)
    files = [_f("2026-10-12_0745"), _f("2026-10-12_1345"),          # today: both kept
             _f("2026-10-11_0745"), _f("2026-10-11_1345"),          # within 30 days: last of the day
             _f("2026-08-03_0745"), _f("2026-08-28_1345"),          # older: last of the month
             _f("2024-01-31_1345"),                                  # beyond 24 months: dropped
             _f("2026-10-11_0900_DEMO")]
    gone = {f.name for f in thin_archive(files, today)}
    assert gone == {"SPEED_dashboard_2026-10-11_0745.html", "SPEED_dashboard_2026-10-11_0900_DEMO.html",
                    "SPEED_dashboard_2026-08-03_0745.html", "SPEED_dashboard_2024-01-31_1345.html"}


def test_thin_archive_ignores_unrecognised_names():
    assert thin_archive([Path("SPEED_dashboard_notadate.html")], dt.date(2026, 10, 12)) == []


def test_week_ends_are_sundays_in_range():
    w = week_ends(dt.date(2026, 9, 30), dt.date(2026, 10, 12))
    assert w == [dt.date(2026, 10, 4), dt.date(2026, 10, 11)]
    assert all(d.weekday() == 6 for d in w)
