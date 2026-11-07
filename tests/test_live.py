"""Live data entry monitor: figures, who has reported, and unusual entries (no connection to DHIS2)."""
import datetime as dt

import pandas as pd

from kpi import live


def _rows(*rows):
    return pd.DataFrame(rows, columns=["facility", "lga", "measure", "period", "value"])


def test_previous_periods_cross_year_and_stay_unpadded():
    assert live.previous("2026W2", 3) == ["2026W1", "2025W52", "2025W51"]
    assert live.previous("2026W11", 1) == ["2026W10"]
    assert live.previous("202601", 2) == ["202512", "202511"]


def test_options_offer_weeks_then_months():
    o = live.options(dt.date(2026, 11, 6))
    keys = [p["key"] for p in o["periods"]]
    assert keys[0] == "2026W45" and "2026W9" not in keys and keys[live.WEEKS] == "202611" and keys[-1] == "202512"
    assert o["periods"][0]["label"] == "Week 45 - 2026-11-02 - 2026-11-08" and o["periods"][live.WEEKS + 1]["label"] == "October 2026"
    assert live.valid_period("202610") and not live.valid_period("1' or '1")


def test_summary_counts_this_period_and_who_is_missing():
    cube = _rows(("A", "ek Ado", "hts_tst", "2026-10", 100), ("A", "ek Ado", "hts_tst_pos", "2026-10", 5),
                 ("A", "ek Ado", "hts_tst", "2026-09", 90), ("B", "ek Ikole", "hts_tst", "2026-10", 40))
    s = live.summarise(cube, {"A", "B"}, {"A": "Ado", "B": "Ikole", "C": "Ikole"}, "hts", "202610")
    assert s["totals"]["hts_tst"] == 140 and s["totals"]["hts_tst_pos"] == 5 and s["previous"]["hts_tst"] == 90
    assert (s["expected"], s["reported"]) == (3, 2) and s["missing"] == [["Ikole", "C"]]
    assert {x["lga"]: x["reported"] for x in s["by_lga"]} == {"Ado": 1, "Ikole": 1}
    assert s["flags"] == []


def test_entry_far_above_the_sites_usual_is_flagged():
    usual = [("Ido", "Ido", "hts_tst_pos", p, 1) for p in ("2026-07", "2026-08", "2026-09")]
    cube = _rows(*usual, ("Ido", "Ido", "hts_tst_pos", "2026-10", 133), ("Other", "Ido", "hts_tst_pos", "2026-10", 19))
    s = live.summarise(cube, {"Ido", "Other"}, {"Ido": "Ido", "Other": "Ido"}, "hts", "202610")
    assert [(f["facility"], f["value"], f["usual"]) for f in s["flags"]] == [("Ido", 133, 1)]


def test_steady_large_site_is_not_flagged():
    cube = _rows(*[("Big", "L", "hts_tst", p, 400) for p in ("2026-07", "2026-08", "2026-09", "2026-10")])
    assert live.summarise(cube, {"Big"}, {"Big": "L"}, "hts", "202610")["flags"] == []


def test_failed_pull_keeps_last_good_figures(monkeypatch):
    live._cache.clear()
    good = {"totals": {}, "as_of": "2026-11-06T10:00:00"}
    calls = iter([good])

    def pull(*a):
        v = next(calls, None)
        if v is None:
            raise RuntimeError("DHIS2 down")
        return dict(v)

    monkeypatch.setattr(live, "_pull", pull)
    monkeypatch.setattr(live, "valid_period", lambda p: True)
    assert live.get("hts", "Ekiti", "202610")["as_of"] == good["as_of"]
    assert "warning" not in live.get("hts", "Ekiti", "202610")          # answered from the cache
    live._cache[("hts", "Ekiti", "202610")]["at"] -= live.CACHE_SECONDS + 1
    assert "did not answer" in live.get("hts", "Ekiti", "202610")["warning"]
    live._cache.clear()


def test_flags_list_covers_sections_and_names_them():
    usual = [("Ido", "Ido", "hts_tst_pos", p, 1) for p in ("2026-07", "2026-08", "2026-09")]
    prep = [("Ido", "Ido", "prep_new", p, 2) for p in ("2026-07", "2026-08", "2026-09")]
    cube = _rows(*usual, *prep, ("Ido", "Ido", "hts_tst_pos", "2026-10", 133), ("Ido", "Ido", "prep_new", "2026-10", 40)).assign(state="Ekiti")
    out = live.collect_flags(cube, "202610")
    assert [(f["section"], f["state"], f["value"]) for f in out["flags"]] == [("HIV testing", "Ekiti", 133), ("PrEP", "Ekiti", 40)]
    assert {c["key"]: c["n"] for c in out["counts"]} == {"hts": 1, "pmtct": 0, "tb": 0, "ahd": 0, "prep": 1}
    assert live.options()["sections"][-1]["key"] == live.FLAGS
