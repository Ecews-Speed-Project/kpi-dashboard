import datetime as dt

import pandas as pd

from run_pipeline import merge_cached


def test_incremental_merge_keeps_every_cached_row():
    # two org units of one facility (a facility and its sub-facility) share a key: both rows must survive
    old = pd.DataFrame({"table": "HTS_TST", "period": ["2026W20", "2026W20", "2026W40"], "facility_key": "f1",
                        "dataname": "HTS_TST_VCT", "disagg": "Female, 20-24 years", "value": [5, 7, 99]})
    new = pd.DataFrame({"table": "HTS_TST", "period": ["2026W40"], "facility_key": "f1",
                        "dataname": "HTS_TST_VCT", "disagg": "Female, 20-24 years", "value": [11]})
    out = merge_cached(old, new, dt.date(2026, 7, 26))
    assert sorted(out["value"]) == [5, 7, 11]        # old W20 rows kept, stale W40 replaced by the fresh pull
