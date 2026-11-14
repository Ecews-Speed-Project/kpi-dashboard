"""Programme week numbers: the week (Monday to Sunday, as DHIS2) that holds 1 October is Week 1 of the fiscal year,
the next is Week 2, and so on. DHIS2 keeps its own ISO week keys (2026W45); only the number shown changes."""
from __future__ import annotations

import datetime as dt


def fiscal_week(d: dt.date | str) -> int:
    """Fiscal week of any date in the week: 2026-09-28 .. 2026-10-04 -> 1, 2026-11-02 -> 6."""
    d = dt.date.fromisoformat(str(d)[:10]) if not isinstance(d, dt.date) else d
    monday = d - dt.timedelta(days=d.weekday())
    for y in (monday.year + 1, monday.year, monday.year - 1):
        oct1 = dt.date(y, 10, 1)
        start = oct1 - dt.timedelta(days=oct1.weekday())
        if monday >= start:
            return (monday - start).days // 7 + 1
    raise ValueError(d)


def iso_week_monday(key: str) -> dt.date:
    """2026W45 -> Monday 2026-11-02."""
    y, w = key.split("W")
    return dt.date.fromisocalendar(int(y), int(w), 1)
