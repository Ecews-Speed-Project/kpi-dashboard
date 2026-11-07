"""Live data entry monitor: one state (or all), one period, one section of the DHIS2 forms, read straight
from DHIS2 when someone opens it, without the full update. Same formulas as the dashboard (dhis_cube), so
a figure here is what the next update will show if nothing else is entered. Aggregates only.

Answers are kept for CACHE_SECONDS, so a room full of people pressing Refresh makes one request to DHIS2,
and only one pull runs at a time. If DHIS2 fails, the last good answer is returned with a warning.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import statistics
import threading
import time

CACHE_SECONDS = 300
TIMEOUT = 90
META_SECONDS = 24 * 3600
ENV_KEYS = ("DHIS2_WEEKLY_USER", "DHIS2_WEEKLY_PASS", "DHIS2_MONTHLY_USER", "DHIS2_MONTHLY_PASS")
STATES = ["Delta", "Ekiti", "Osun"]
WEEKS, MONTHS, PRIOR = 12, 12, 3          # periods offered; earlier periods read to spot unusual entries
FLAG_MIN, FLAG_TIMES = 20, 3            # flagged: at least 20, and at least 3 times the site's highest recent value

# primary: the forms that decide whether a facility has reported in this section.
# HTS reads more forms because HTS_TST as defined by the DHIS2 indicator also counts PMTCT, TB and index testing.
SECTIONS = {
    "hts": dict(label="HIV testing", primary=["HTS_TST"], tables=["HTS_TST", "INDEX", "HIVST", "PMTCT", "PMTCT_COMM", "TB"],
                measures=[("hts_tst", "Tested"), ("hts_tst_pos", "Positive"), ("index_tst", "Index contacts tested"), ("index_pos", "Index new positive")],
                rate=("Yield", "hts_tst_pos", "hts_tst")),
    "pmtct": dict(label="PMTCT", primary=["PMTCT"], tables=["PMTCT", "PMTCT_COMM", "PMTCT_EID"],
                  measures=[("pmtct_stat_num", "Status known"), ("pmtct_stat_pos", "Positive"), ("pmtct_art", "On ART"), ("pmtct_eid", "EID samples")],
                  rate=("Positivity", "pmtct_stat_pos", "pmtct_stat_num")),
    "tb": dict(label="TB", primary=["TB"], tables=["TB", "TX_TB"],
               measures=[("tb_stat_num", "TB status known"), ("tb_stat_pos", "TB/HIV positive"), ("tb_art", "TB_ART"), ("tpt_started", "TPT started")],
               rate=("Positivity", "tb_stat_pos", "tb_stat_num")),
    "ahd": dict(label="AHD (new clients)", primary=["AHD"], tables=["AHD"],
                measures=[("ahd_enrolled_n", "Newly enrolled"), ("ahd_cd4_n", "CD4 tested"), ("ahd_confirmed_n", "AHD confirmed"), ("ahd_crag_test_n", "CrAg tested")],
                rate=("CD4 uptake", "ahd_cd4_n", "ahd_enrolled_n")),
    "prep": dict(label="PrEP", primary=["PREP"], tables=["PREP"],
                 measures=[("prep_offered", "Offered"), ("prep_new", "PrEP_NEW"), ("prep_ct", "PrEP_CT")],
                 rate=("Uptake", "prep_new", "prep_offered")),
}

FLAGS = "flags"                         # every section's unusual entries in one list, from one read of all the forms

_lock = threading.Lock()
_cache: dict[tuple, dict] = {}
_meta: dict[str, dict] = {}


# ---------------------------------------------------------------- periods
def week_key(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}W{w}"       # DHIS2 writes weeks unpadded (2026W9)


def month_key(d: dt.date) -> str:
    return f"{d.year}{d.month:02d}"


def _period_date(p: str) -> dt.date:
    if "W" in p:
        y, w = p.split("W")
        return dt.date.fromisocalendar(int(y), int(w), 1)
    return dt.date(int(p[:4]), int(p[4:6]), 1)


def previous(p: str, n: int = PRIOR) -> list[str]:
    """The n periods before p, nearest first."""
    d, out = _period_date(p), []
    for _ in range(n):
        if "W" in p:
            d -= dt.timedelta(days=7)
            out.append(week_key(d))
        else:
            d = (d - dt.timedelta(days=1)).replace(day=1)
            out.append(month_key(d))
    return out


def period_label(p: str) -> str:
    """As the DHIS2 data entry screen writes it: Week 45 - 2026-11-02 - 2026-11-08, or October 2026."""
    d = _period_date(p)
    if "W" in p:
        return f"Week {d.isocalendar()[1]} - {d} - {d + dt.timedelta(days=6)}"
    return f"{d:%B %Y}"


def options(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    weeks = [week_key(monday - dt.timedelta(days=7 * i)) for i in range(WEEKS)]
    months = [month_key(today)] + previous(month_key(today), MONTHS - 1)
    return {"sections": [{"key": k, "label": s["label"]} for k, s in SECTIONS.items()] + [{"key": FLAGS, "label": "Flags: all sections"}], "states": STATES,
            "periods": [{"key": p, "label": period_label(p), "kind": "Weekly reports"} for p in weeks]
            + [{"key": p, "label": period_label(p), "kind": "Monthly reports"} for p in months]}


def valid_period(p: str) -> bool:
    return p in {x["key"] for x in options()["periods"]}


# ---------------------------------------------------------------- figures
def _num(v) -> float:
    return float(v) if v == v else 0.0


def clean_lga(s) -> str:
    return re.sub(r"^(de|ek|os)\s+", "", str(s or "Unknown"), flags=re.I)


def summarise(cube, reported: set, expected: dict, section: str, period: str) -> dict:
    """cube: dhis_cube rows for the selected period and the earlier ones. reported: facilities with any entry
    in the section's main forms this period. expected: facility -> LGA of those that reported on these forms
    in this period or the last 3 (DHIS2 assigns many sites to the forms that never report on them)."""
    sec = SECTIONS[section]
    names = [m for m, _ in sec["measures"]]
    key = lambda p: (p if "W" in p else f"{p[:4]}-{p[4:6]}")
    now, prior = key(period), [key(p) for p in previous(period)]
    c = cube[cube["measure"].isin(names)].copy()
    c["lga"] = c["lga"].map(clean_lga)
    cur = c[c["period"] == now]
    totals = {m: _num(cur[cur["measure"] == m]["value"].sum()) for m in names}
    last = c[c["period"] == prior[0]]
    prev = {m: _num(last[last["measure"] == m]["value"].sum()) for m in names}

    lga_of = dict(expected)
    for f, l in zip(cur["facility"], cur["lga"]):
        lga_of.setdefault(f, l)
    done = {f for f in reported if f in lga_of}
    lgas = sorted(set(lga_of.values()))
    by_lga = []
    for l in lgas:
        x, sites = cur[cur["lga"] == l], [f for f, g in lga_of.items() if g == l and f in expected]
        by_lga.append({"lga": l, "expected": len(sites), "reported": sum(f in done for f in sites),
                       **{m: _num(x[x["measure"] == m]["value"].sum()) for m in names}})
    missing = sorted(([lga_of[f], f] for f in expected if f not in done))

    # a site far above its own recent entries: for the data officer to check, not a verdict
    flags = []
    label = dict(sec["measures"])
    g = c.groupby(["facility", "measure", "period"])["value"].sum()
    site_periods = c.groupby("facility")["period"].agg(set).to_dict()
    for (f, m), v in cur.groupby(["facility", "measure"])["value"].sum().items():
        seen = [p for p in prior if p in site_periods.get(f, ())]       # periods the site reported at all
        if not seen:
            continue
        recent = [_num(g.get((f, m, p), 0)) for p in seen]
        usual = statistics.median(recent)
        if v >= FLAG_MIN and v >= FLAG_TIMES * max(max(recent), 1):
            flags.append({"facility": f, "lga": lga_of.get(f, ""), "measure": label[m], "value": _num(v), "usual": usual})
    flags.sort(key=lambda x: -x["value"])
    return {"section": section, "label": sec["label"], "period": period, "period_label": period_label(period),
            "previous_label": period_label(previous(period)[0]), "measures": [{"key": k, "label": l} for k, l in sec["measures"]],
            "rate": {"label": sec["rate"][0], "num": sec["rate"][1], "den": sec["rate"][2]},
            "totals": totals, "previous": prev, "expected": len(expected), "reported": len({f for f in done if f in expected}),
            "by_lga": by_lga, "missing": missing, "flags": flags}


def collect_flags(cube, period: str) -> dict:
    """The unusual entries of every section, with the same rule as each section's own list."""
    state_of = dict(zip(cube["facility"], cube["state"])) if "state" in cube else {}
    flags, counts = [], []
    for k, s in SECTIONS.items():
        found = summarise(cube, set(), {}, k, period)["flags"]
        flags += [{**f, "section": s["label"], "state": state_of.get(f["facility"], "")} for f in found]
        counts.append({"key": k, "label": s["label"], "n": len(found)})
    flags.sort(key=lambda x: -x["value"])
    return {"section": FLAGS, "period": period, "period_label": period_label(period), "flags": flags, "counts": counts}


# ---------------------------------------------------------------- DHIS2
def enabled() -> bool:
    """On only where the DHIS2 logins are set (read from .env, never shown)."""
    import run_pipeline as R
    R.load_env()
    return all(os.environ.get(k) for k in ENV_KEYS)


def _instance(which: str) -> dict:
    """The client and its lookups, re-read once a day."""
    m = _meta.get(which)
    if m and time.time() - m["at"] < META_SECONDS:
        return m
    import run_pipeline as R
    from etl.dhis2_client import DHIS2, clean_state, facility_lookup, site_types

    class Client(DHIS2):
        """One short request per form for the wanted state and periods; a failure is reported, not waited out."""
        ou, periods = [], []

        def get(self, path, **params):
            r = self.s.get(f"{self.base}/api/{path.lstrip('/')}", params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()

        def data_values(self, dataset, start, end):
            import pandas as pd
            frames = [pd.DataFrame(self.get("dataValueSets", dataSet=dataset, orgUnit=ou, children="true", period=self.periods).get("dataValues", []))
                      for ou in self.ou]
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            return df.drop_duplicates(["dataElement", "period", "orgUnit", "categoryOptionCombo", "attributeOptionCombo"]) if len(df) else df

    R.load_env()
    cfg = R.settings()
    h = cfg["dhis2"]
    c = Client(h[f"{which}_url"], f"DHIS2_{which.upper()}_USER", f"DHIS2_{which.upper()}_PASS", timeout=TIMEOUT)
    lv = (h["level_state"], h["level_lga"], h["level_facility"])
    fac = facility_lookup(c, *lv, match=h["orgunit_match"])
    states = {clean_state(r["name"]): r["id"] for r in c.org_units(h["level_state"]).to_dict("records")}
    community = {ou for ou, t in site_types(c, h["level_facility"]).items() if t == "Community"} if which == "monthly" else None
    from kpi.measures import DHIS_MEASURES
    DHIS_MEASURES.update(R.indicator_terms(cfg))
    m = _meta[which] = dict(at=time.time(), client=c, fac=fac, states=states, community=community, tables=h["tables"],
                            field=h["dataname_field"])
    return m


def _pull(section: str, state: str, period: str) -> dict:
    import pandas as pd
    wanted = [t for s in SECTIONS.values() for t in s["tables"]] if section == FLAGS else SECTIONS[section]["tables"]
    from etl.dhis2_client import extract_instance
    from kpi.measures import dhis_cube, prefer_monthly_variants
    which = "weekly" if "W" in period else "monthly"
    m = _instance(which)
    names = [state] if state in STATES else STATES
    c = m["client"]
    c.ou, c.periods = [m["states"][s] for s in names if s in m["states"]], [period] + previous(period)
    tables = {t: m["tables"][t] for t in dict.fromkeys(wanted)}
    facts = extract_instance(c, tables, which, None, None, m["fac"], m["field"], m["community"])
    facts = facts.assign(source=which)
    if which == "monthly":
        facts = prefer_monthly_variants(facts)
    cube = dhis_cube(facts) if len(facts) else pd.DataFrame(columns=["facility", "lga", "measure", "period", "value"])
    if section == FLAGS:
        return {**collect_flags(cube, period), "state": state if state in STATES else "", "as_of": dt.datetime.now().isoformat(timespec="seconds")}
    main = facts[facts["table"].isin(SECTIONS[section]["primary"])]
    expected = {f: clean_lga(l) for f, l in zip(main["facility"], main["lga"])}
    out = summarise(cube, set(main[main["period"].astype(str) == period]["facility"]), expected, section, period)
    return {**out, "state": state if state in STATES else "", "as_of": dt.datetime.now().isoformat(timespec="seconds")}


def get(section: str, state: str, period: str) -> dict:
    """The answer for a selection: from the cache when fresh, else from DHIS2, else the last good one with a warning."""
    if (section not in SECTIONS and section != FLAGS) or not valid_period(period) or state not in STATES + [""]:
        raise ValueError("unknown selection")
    key = (section, state, period)
    with _lock:              # one pull at a time; those waiting then find the fresh answer
        hit = _cache.get(key)
        if hit and time.time() - hit["at"] < CACHE_SECONDS:
            return hit["data"]
        started = time.time()
        try:
            data = _pull(section, state, period)
        except Exception as e:
            print(f"  live data entry: {section} {state or 'all'} {period}: {type(e).__name__}")
            if hit:
                return {**hit["data"], "warning": "DHIS2 did not answer, so these are the last figures read."}
            raise
        data["seconds"] = round(time.time() - started, 1)
        _cache[key] = dict(at=time.time(), data=data)
        for k in [k for k, v in _cache.items() if time.time() - v["at"] > 6 * CACHE_SECONDS]:
            del _cache[k]
        return data
