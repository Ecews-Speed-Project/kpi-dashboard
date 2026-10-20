"""Roll base measures up the hierarchy and evaluate every registry indicator
against its target. Produces the single JSON the dashboard renders."""
from __future__ import annotations

import datetime as dt
import math
import re

import pandas as pd
import yaml

from .measures import (AGE_BANDS, APPT_COLS, VL_COLS, vl_clients, EAC_COLS, eac_clients, _datetimes, CONC_CATS, CONC_COLS, DIMS, PBS_FOLLOWUP, UNKNOWN_AGE, Period,
                       appointment_clients, concurrence, dhis_cube, facility_key, linelist_backfill, ndr_waterfall,
                       linelist_cube, week_ends, pbs_backfill, pbs_clients, pbs_cube, pbs_from_linelist)
from .naomi import AGE5

SPEED = "ECEWS SPEED"


def _key(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return float(v)


def status_of(score, rag, neutral=False):
    if score is None or neutral:
        return "none"
    if score >= rag["green_from"]:
        return "good"
    if score >= rag["red_below"]:
        return "warning"
    return "critical"


def rate_status(value, target, rag):
    """Rates are judged against the target itself, not a % of it: 90% VLS
    against a 95% target is a miss even though it is '95% of target'."""
    if value is None or target is None:
        return "none"
    if value >= target:
        return "good"
    if value >= target - rag.get("rate_warning_gap", 0.05):
        return "warning"
    return "critical"


def evaluate(reg: dict, values: dict, targets: pd.DataFrame, p: Period,
             dhis_through: dt.date, rag: dict) -> list[dict]:
    q = f"q{p.q_index}"
    out = []
    groups = [("IPI", reg["ipi"]), ("Non-IPI", reg["non_ipi"]), ("Conventional", reg["conventional"])]
    for group, items in groups:
        for ind in items:
            rec = {k: ind.get(k) for k in ("code", "name", "pillar", "measures", "kind", "flow", "source", "num", "den")}
            rec["group"] = group
            rec["by_scope"] = {}
            for scope, vals in values.items():
                num = _clean(vals.get(ind["num"]))
                den = _clean(vals.get(ind["den"])) if ind.get("den") else None
                value = (num / den if num is not None and den else None) if ind["kind"] == "rate" else num
                t = targets[(targets.target_key == ind["target"]) & (targets.scope == scope) & (targets.age_band == "All")]
                annual = _clean(t["annual"].iloc[0]) if len(t) else None
                milestone = _clean(t[q].iloc[0]) if len(t) and q in t else None
                r = dict(value=value, num=num, den=den, annual=annual)
                has_ms = len(t) > 0 and t[["q1", "q2", "q3", "q4"]].notna().any(axis=1).iloc[0]
                if ind["kind"] == "rate" and ind.get("paced"):
                    # cumulative coverage (e.g. DQA): milestone grows each quarter
                    prev_ms = _clean(t[f"q{p.q_index - 1}"].iloc[0]) if p.q_index > 1 and len(t) else 0.0
                    tgt = milestone if milestone is not None else annual
                    expected = (prev_ms or 0) + ((tgt or 0) - (prev_ms or 0)) * p.elapsed()
                    r.update(target=tgt, expected_to_date=expected,
                             score=(value / expected if value is not None and expected else None))
                elif ind["kind"] == "rate":
                    tgt = milestone if milestone is not None else annual
                    r.update(target=tgt, gap=(value - tgt if value is not None and tgt is not None else None),
                             score=(value / tgt if value is not None and tgt else None))
                elif ind.get("flow") == "snapshot":
                    r.update(target=annual, score=(value / annual if value is not None and annual else None))
                else:  # cumulative count: quarter target and pace
                    # Quarterly counts from milestones when given (e.g. joint SSV), else annual/4
                    if has_ms:
                        q_target = milestone or 0.0   # milestone-driven: no milestone = nothing due
                    else:
                        q_target = annual / 4 if annual else None
                    through = dhis_through if "DHIS" in str(ind.get("source", "DHIS2")) or group == "Conventional" else p.as_of
                    expected = q_target * p.elapsed(through) if q_target else None
                    r.update(target=q_target, expected_to_date=expected,
                             pct_of_quarter=(value / q_target if value is not None and q_target else None),
                             score=(value / expected if value is not None and expected else None),
                             pct_of_annual=(value / annual if value is not None and annual else None))
                    if q_target == 0:
                        r.update(score=None, note="No milestone due this quarter")
                    if ind.get("milestone_scope") == "programme" and scope != SPEED:
                        r.update(target=None, score=None, expected_to_date=None,
                                 note="Milestones are set programme-wide")
                if ind["kind"] == "rate" and not ind.get("paced"):
                    r["status"] = rate_status(value, r.get("target"), rag)
                else:
                    r["status"] = status_of(r.get("score"), rag, ind.get("neutral", False))
                rec["by_scope"][scope] = r
            out.append(rec)
    return out



ALIASES: dict[str, str] = {}  # normalised source facility name -> normalised DHIS2 name (settings.facility_aliases)


def set_aliases(aliases: dict | None) -> None:
    ALIASES.clear()
    for src, dst in (aliases or {}).items():
        ALIASES[facility_key(src)] = facility_key(dst)




def _fkey(s: pd.Series) -> pd.Series:
    return s.map(facility_key).map(lambda x: ALIASES.get(x, x))


def attach_master(cube: pd.DataFrame, master: pd.DataFrame, source: str, dq: dict) -> pd.DataFrame:
    """Re-attach state/LGA from the master facility list (DHIS2 hierarchy),
    matching on normalised facility name (after settings.facility_aliases).
    Unmatched facilities are reported."""
    m = master.assign(fkey=master["facility"].map(facility_key)).drop_duplicates("fkey").set_index("fkey")
    c = cube.copy()
    c["fkey"] = _fkey(c["facility"])
    miss = sorted(set(c["fkey"]) - set(m.index))
    if miss:
        dq.setdefault("unmatched_facilities", {})[source] = miss
    c = c[c["fkey"].isin(m.index)]
    c["state"] = c["fkey"].map(m["state"])
    c["lga"] = c["fkey"].map(m["lga"])
    c["facility"] = c["fkey"].map(m["facility"])
    return c.drop(columns="fkey")


def period_meta(key: str, rule: str = "thursday") -> dict:
    """Start, end, anchor date and month of a cube period key. The dashboard
    includes a period in a date range when its anchor falls inside the range
    (months: when the range covers at least half the month)."""
    import calendar
    from etl.period_merge import WEEK_RE, week_bounds, week_to_month
    if key == "":
        return dict(key="", kind="", start=None, end=None, anchor=None, month="")
    if key[:2] in ("SL", "SN"):      # stock figures from a saved linelist (SL) / NDR file (SN) copy of that date
        d = key[2:]
        return dict(key=key, kind="S", start=d, end=d, anchor=d, month=d[:7])
    if WEEK_RE.match(key):
        mon, sun = week_bounds(key)
        anchor = {"thursday": mon + dt.timedelta(days=3), "week_end": sun, "week_start": mon}[rule]
        return dict(key=key, kind="W", start=mon.isoformat(), end=sun.isoformat(), anchor=anchor.isoformat(),
                    month=week_to_month(key, rule))
    if len(key) == 7:
        y, m = map(int, key.split("-"))
        last = dt.date(y, m, calendar.monthrange(y, m)[1])
        return dict(key=key, kind="M", start=f"{key}-01", end=last.isoformat(), anchor=f"{key}-15", month=key)
    return dict(key=key, kind="D", start=key, end=key, anchor=key, month=key[:7])


def quarters(history_start: dt.date, p: Period) -> list[dict]:
    """Fiscal quarters from the start of the history window to the current one."""
    out, d = [], dt.date(history_start.year, history_start.month, 1)
    fy_m = p.fy_start.month
    while d <= p.q_end:
        m0 = (d.month - fy_m) % 12
        qs = dt.date(d.year, d.month, 1) - pd.DateOffset(months=m0 % 3)
        qs = qs.date() if hasattr(qs, "date") else qs
        fy = qs.year + 1 if qs.month >= fy_m else qs.year
        qn = ((qs.month - fy_m) % 12) // 3 + 1
        qe = (pd.Timestamp(qs) + pd.DateOffset(months=3) - pd.Timedelta(days=1)).date()
        lab = f"FY{str(fy)[-2:]}Q{qn}"
        if not out or out[-1]["label"] != lab:
            out.append(dict(label=lab, fy=f"FY{str(fy)[-2:]}", q=qn, start=qs.isoformat(), end=qe.isoformat()))
        d = (pd.Timestamp(qe) + pd.Timedelta(days=1)).date()
    return out


def in_window(meta: dict, start: dt.date, end: dt.date) -> bool:
    if meta["kind"] == "":
        return True
    s, e = dt.date.fromisoformat(meta["start"]), dt.date.fromisoformat(meta["end"])
    if meta["kind"] == "M":
        overlap = (min(e, end) - max(s, start)).days + 1
        return overlap * 2 >= (e - s).days + 1
    return start <= dt.date.fromisoformat(meta["anchor"]) <= end


def scope_values(cube: pd.DataFrame, p: Period, state_measures: dict, rule: str = "thursday") -> dict:
    keys = cube["period"].fillna("").unique()
    keep = {k for k in keys if in_window(period_meta(k, rule), p.q_start, min(p.q_end, p.as_of))}
    window = cube[cube["period"].fillna("").isin(keep)]
    out = {SPEED: window.groupby("measure")["value"].sum().to_dict()}
    for st, g in window.groupby("state"):
        out[st] = g.groupby("measure")["value"].sum().to_dict()
    for scope, extra in state_measures.items():
        out.setdefault(scope, {}).update({k: v for k, v in extra.items() if v is not None})
    return out


def linelist_date(ll: pd.DataFrame, c: dict, as_of: dt.date) -> dt.date:
    """The linelist's data date: latest generation date in the extract (capped at as_of)."""
    from .measures import _datetimes
    col = c.get("generated")
    if ll.empty or col not in ll:
        return as_of
    d = _datetimes(ll[col]).dropna()
    d = d[d.dt.date <= as_of]
    return d.max().date() if len(d) else as_of


def facility_order(master: pd.DataFrame) -> pd.DataFrame:
    return master[["state", "lga", "facility"]].drop_duplicates("facility").sort_values(["state", "lga", "facility"])


def encode_clients(cl: pd.DataFrame, master: pd.DataFrame, dq: dict) -> dict:
    """Biometric follow-up list for the browser: [facility, sex, age, codes, ids...]."""
    cols = ["unique_id", "art_no", "hosp_no", "fp_status", "dup_type", "match", "captured", "last_pickup"]
    if cl.empty:
        return {"cats": PBS_FOLLOWUP, "cols": cols, "rows": []}
    cl = attach_master(cl, master, "pbs_clients", dq)
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    sex_i, age_i = {"F": 0, "M": 1, "U": 2}, {a: i for i, a in enumerate(AGE_BANDS + [UNKNOWN_AGE])}
    rows = [[int(fac_idx[r.facility]), sex_i.get(r.sex, 2), age_i.get(r.age, len(AGE_BANDS)), r.cats]
            + [str(getattr(r, k) or "") for k in cols] for r in cl.itertuples(index=False)]
    return {"cats": PBS_FOLLOWUP, "cols": cols, "rows": rows}


def encode_conc_clients(cl: pd.DataFrame, master: pd.DataFrame) -> dict:
    """EMR vs NDR differences, one row per client: [facility, sex, age, category, CONC_COLS...]."""
    cats = list(CONC_CATS)
    if cl is None or cl.empty:
        return {"cats": CONC_CATS, "cols": CONC_COLS, "rows": []}
    cl = attach_master(cl, master, "conc_clients", {})
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    sex_i, age_i = {"F": 0, "M": 1, "U": 2}, {a: i for i, a in enumerate(AGE_BANDS + [UNKNOWN_AGE])}
    rows = [[int(fac_idx[r["facility"]]), sex_i.get(r["sex"], 2), age_i.get(r["age"], len(AGE_BANDS)), cats.index(r["measure"])]
            + [str(r[k]) for k in CONC_COLS] for r in cl.to_dict("records")]
    return {"cats": CONC_CATS, "cols": CONC_COLS, "rows": rows}


def encode_appt_clients(cl: pd.DataFrame, master: pd.DataFrame) -> dict:
    """Appointments in the window, one row per client: [facility, sex, age, nopick(0/1), APPT_COLS...].
    Repetitive text columns are stored once in 'dict' and referenced by index."""
    if cl is None or cl.empty:
        return {"cols": APPT_COLS, "rows": [], "dict": {}}
    cl = attach_master(cl, master, "appt_clients", {})
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    sex_i, age_i = {"F": 0, "M": 1, "U": 2}, {a: i for i, a in enumerate(AGE_BANDS + [UNKNOWN_AGE])}
    dcols = ("datim", "status", "refill_days", "regimen_line", "flags", "uniq")
    lookup = {k: sorted(cl[k].astype(str).unique().tolist()) for k in dcols}
    idx = {k: {v: i for i, v in enumerate(lookup[k])} for k in dcols}
    rows = [[int(fac_idx[r["facility"]]), sex_i.get(r["sex"], 2), age_i.get(r["age"], len(AGE_BANDS)), int(r["nopick"])]
            + [idx[k][str(r[k])] if k in idx else str(r[k]) for k in APPT_COLS] for r in cl.to_dict("records")]
    return {"cols": APPT_COLS, "rows": rows, "dict": lookup}


def _eac_date(df, c) -> str:
    """Data date of the EAC line list (the script's @endDate, column Date_Generated)."""
    if df is None or df.empty or c.get("generated") not in df:
        return ""
    d = _datetimes(df[c["generated"]]).dropna()
    return d.max().date().isoformat() if len(d) else ""


def encode_eac_clients(cl: pd.DataFrame, master: pd.DataFrame) -> dict:
    """EAC clients: [facility, sex, age, EAC_COLS...]; repetitive text columns via 'dict'."""
    if cl is None or cl.empty:
        return {"cols": EAC_COLS, "rows": [], "dict": {}}
    cl = attach_master(cl, master, "eac_clients", {})
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    sex_i, age_i = {"F": 0, "M": 1, "U": 2}, {a: i for i, a in enumerate(AGE_BANDS + [UNKNOWN_AGE])}
    dcols = ("datim", "status", "transferred_in", "cycle", "sessions", "fu_status", "switched")
    lookup = {k: sorted(cl[k].astype(str).unique().tolist()) for k in dcols}
    idx = {k: {v: i for i, v in enumerate(lookup[k])} for k in dcols}
    rows = [[int(fac_idx[r["facility"]]), sex_i.get(r["sex"], 2), age_i.get(r["age"], len(AGE_BANDS))]
            + [idx[k][str(r[k])] if k in idx else str(r[k]) for k in EAC_COLS] for r in cl.to_dict("records")]
    return {"cols": EAC_COLS, "rows": rows, "dict": lookup}


def encode_vl_clients(cl: pd.DataFrame, master: pd.DataFrame) -> dict:
    """Unsuppressed clients: [facility, sex, age, VL_COLS...]; repetitive text columns via 'dict'."""
    if cl is None or cl.empty:
        return {"cols": VL_COLS, "rows": [], "dict": {}}
    cl = attach_master(cl, master, "vl_clients", {})
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    sex_i, age_i = {"F": 0, "M": 1, "U": 2}, {a: i for i, a in enumerate(AGE_BANDS + [UNKNOWN_AGE])}
    dcols = ("datim", "vl_band", "repeat", "regimen_line", "regimen")
    lookup = {k: sorted(cl[k].astype(str).unique().tolist()) for k in dcols}
    idx = {k: {v: i for i, v in enumerate(lookup[k])} for k in dcols}
    rows = [[int(fac_idx[r["facility"]]), sex_i.get(r["sex"], 2), age_i.get(r["age"], len(AGE_BANDS))]
            + [idx[k][str(r[k])] if k in idx else str(r[k]) for k in VL_COLS] for r in cl.to_dict("records")]
    return {"cols": VL_COLS, "rows": rows, "dict": lookup}


TREND_MEASURES = ["tx_curr", "tx_curr_emr", "iit_now", "vl_result", "vl_suppressed", "pbs_active", "pbs_baseline", "pbs_invalid",
                  "pbs_valid", "pbs_duplicate", "pbs_adjudication", "pbs_recapture",
                  "bio_aw", "bio_noprint", "bio_invalid", "bio_dup_supp", "bio_unusable", "tx_curr_cdr"]


def snapshot_rows(cube: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """Today's stock figures per facility, saved after every live run so exact
    trends build up over time."""
    s = cube[(cube["period"].fillna("") == "") & cube["measure"].isin(TREND_MEASURES)]
    return (s.groupby(["state", "facility", "measure"])["value"].sum().reset_index()
             .assign(date=as_of.isoformat())[["date", "state", "facility", "measure", "value"]])


def encode_trend(est: pd.DataFrame, snaps: pd.DataFrame, master: pd.DataFrame, dq: dict) -> dict:
    """{dates, measures, rows:[facility, date, measure, value]}; *_est measures are
    reconstructed from one snapshot, the rest are saved run snapshots."""
    parts = [x for x in (est, snaps) if x is not None and len(x)]
    if not parts:
        return {"dates": [], "measures": [], "rows": []}
    t = attach_master(pd.concat(parts, ignore_index=True), master, "trend", {})
    t = t.groupby(["facility", "date", "measure"])["value"].sum().reset_index()
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    dates, meas = sorted(t["date"].unique()), sorted(t["measure"].unique())
    di, mi = {d: i for i, d in enumerate(dates)}, {m: i for i, m in enumerate(meas)}
    rows = [[int(fac_idx[f]), di[d], mi[m], int(v)] for f, d, m, v in t.itertuples(index=False)]
    return {"dates": dates, "measures": meas, "rows": rows}


def encode_methods(merged: pd.DataFrame, master: pd.DataFrame, qs: list[dict], tables: dict, rule: str) -> dict:
    """'How it's calculated': every DHIS2 data element behind each measure, with its
    contribution per facility x fiscal quarter (same selection code as the cube)."""
    from pathlib import Path
    from .measures import DHIS_MEASURES, dhis_terms
    raw = Path(__file__).resolve().parents[1] / "data" / "raw"
    names = {}
    for inst in ("weekly", "monthly"):
        f = raw / f"dhis2_catalog_{inst}_datasets.csv"
        if f.exists():
            names.update(dict(zip(*pd.read_csv(f)[["id", "name"]].astype(str).T.values)))

    def ds(table, inst):
        ids = (tables.get(table) or {}).get(inst) or []
        return [[i, names.get(i, i).strip()] for i in ([ids] if isinstance(ids, str) else ids)]

    catalog = {m: [dict(table=t.table, dataname=t.dataname, coc=t.coc, sign=t.sign, mod=t.mod,
                        weekly=ds(t.table, "weekly"), monthly=ds(t.table, "monthly")) for t in terms]
               for m, terms in DHIS_MEASURES.items()}
    tr = dhis_terms(merged)
    if tr.empty:
        return {"catalog": catalog, "quarters": [], "measures": [], "rows": []}
    qlab = {}
    for k in tr["period"].unique():
        meta = period_meta(k, rule)
        qlab[k] = next((q["label"] for q in qs if meta["kind"] and
                        in_window(meta, dt.date.fromisoformat(q["start"]), dt.date.fromisoformat(q["end"]))), None)
    tr = tr.assign(q=tr["period"].map(qlab)).dropna(subset=["q"])
    tr = attach_master(tr, master, "methods", {})
    tr = tr.groupby(["facility", "q", "measure", "term"])["value"].sum().reset_index()
    fac_idx = {f: i for i, f in enumerate(facility_order(master)["facility"])}
    ql = [q["label"] for q in qs]
    ms = sorted(tr["measure"].unique())
    qi, mi = {q: i for i, q in enumerate(ql)}, {m: i for i, m in enumerate(ms)}
    rows = [[int(fac_idx[f]), qi[q], mi[m], int(t), (float(v) if v % 1 else int(v))]
            for f, q, m, t, v in tr.itertuples(index=False) if v]
    return {"catalog": catalog, "quarters": ql, "measures": ms, "rows": rows}


def encode_cube(cube: pd.DataFrame, master: pd.DataFrame) -> dict:
    """Compact form for the browser: dimension dictionaries + integer rows
    [facility, sex, age, mod, period, measure, value]."""
    fac = facility_order(master)
    fac_idx = {f: i for i, f in enumerate(fac["facility"])}
    dims = {
        "facility": fac.values.tolist(),
        "sex": ["F", "M", "U"],
        "age": AGE_BANDS + [UNKNOWN_AGE],
        "mod": sorted(cube["mod"].fillna("").unique().tolist()),
        "period": sorted(cube["period"].fillna("").unique().tolist()),
        "measure": sorted(cube["measure"].unique().tolist()),
    }
    idx = {k: {v: i for i, v in enumerate(vals)} for k, vals in dims.items() if k != "facility"}
    c = cube[cube["value"] != 0]
    rows = list(zip(c["facility"].map(fac_idx), c["sex"].map(idx["sex"]), c["age"].map(idx["age"]),
                    c["mod"].fillna("").map(idx["mod"]), c["period"].fillna("").map(idx["period"]),
                    c["measure"].map(idx["measure"]), c["value"].round(3)))
    rows = [[int(a), int(b), int(c_), int(d), int(e), int(f), (float(v) if v % 1 else int(v))]
            for a, b, c_, d, e, f, v in rows]
    return {"dims": dims, "rows": rows}


def run_engine(settings: dict, registry_path: str, merged: pd.DataFrame, month_stat: pd.DataFrame,
               linelist: pd.DataFrame, pbs: pd.DataFrame, manual: dict, targets: pd.DataFrame,
               master: pd.DataFrame, as_of: dt.date, weekly_raw: pd.DataFrame, naomi: pd.DataFrame,
               demo: bool = False, history: pd.DataFrame | None = None,
               prev_quarter: pd.DataFrame | None = None, past: dict | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    reg = yaml.safe_load(open(registry_path, encoding="utf-8"))
    p = Period(as_of=as_of, fy_start=pd.to_datetime(str(settings["fy_start"])).date())
    cols = settings["columns"]
    set_aliases(settings.get("facility_aliases"))
    # Linelist stock figures (TX_CURR, VL 12-month window, IIT) are measured as of the
    # date the linelist was generated, not today: a stale extract must not lose a
    # fortnight of VL results from its window.
    ll_asof = linelist_date(linelist, cols["linelist"], as_of)
    p_ll = Period(as_of=ll_asof, fy_start=p.fy_start)
    # one entry per site: weekly and monthly instances sometimes spell a site differently
    master = master.assign(_k=master["facility"].map(facility_key)).drop_duplicates("_k").drop(columns="_k")
    # Biometrics: NDR PBS line list when supplied, else the linelist's pbs/recapture flags
    if len(pbs):
        pbs_src, pc, pbs_source = pbs, cols["pbs_all"], "ndr"
    else:
        pbs_src, pc = pbs_from_linelist(linelist, cols["linelist"])
        pbs_source = "linelist" if len(pbs_src) else "none"

    from etl.period_merge import WEEK_RE, week_bounds
    # by date, not text: DHIS2 writes 2026W9 (unpadded), which sorts after 2026W41 as text
    wk = sorted({p for p in weekly_raw["period"] if WEEK_RE.match(str(p))}, key=lambda p: week_bounds(p)[0]) \
        if len(weekly_raw) else []
    dhis_through = min(week_bounds(wk[-1])[1], as_of) if wk else as_of

    history_start = (pd.Timestamp(p.fy_start) - pd.DateOffset(months=settings["reporting"].get("history_months", 12))).date()
    dq: dict = {}
    dcube = attach_master(dhis_cube(merged), master, "dhis2", dq)
    # PMTCT facility vs community follows the dataset (PMTCT vs Community PMTCT), as the deck does
    parts = [dcube,
             attach_master(linelist_cube(linelist, cols["linelist"], p_ll, history_start, today=as_of), master, "linelist", dq),
             attach_master(pbs_cube(pbs_src, pc), master, "pbs", dq)]
    # treatment from the NDR: the CDR line list still gives the EMR's own TX_CURR (same unique rule), so
    # EMR and NDR can be compared - tx_curr_cdr / emr_active_cdr, as of the CDR line list date
    cdr = manual.get("cdr_linelist")
    if cdr is not None and len(cdr):
        cd = linelist_date(cdr, cols["linelist"], as_of)
        x = linelist_cube(cdr, cols["linelist"], Period(as_of=cd, fy_start=p.fy_start), None, today=cd)
        x = x[(x["period"].fillna("") == "") & (x["mod"].fillna("") == "") & x["measure"].isin(["tx_curr_emr", "emr_active"])]
        parts.append(attach_master(x.assign(measure=x["measure"].map({"tx_curr_emr": "tx_curr_cdr", "emr_active": "emr_active_cdr"})),
                                   master, "cdr_linelist", dq))
    # Past periods: stock figures from the saved dated copies (one per month), period "SL<date>" /
    # "SN<date>". Quarter-relative flows (waterfall, appointments) come from today's files only.
    ndr_date = None
    for d, ll_old in (past or {}).get("linelist", []):
        if d >= ll_asof or ll_old.empty:
            continue
        from .measures import quarter_start
        qfile = "quarter_file" in ll_old
        x = linelist_cube(ll_old, cols["linelist"], Period(as_of=d, fy_start=p.fy_start),
                          quarter_start(d, p.fy_start.month) if qfile else None, today=d)
        stock = x[(x["period"].fillna("") == "") & (x["mod"].fillna("") == "")]
        if qfile:
            # a quarter-end line list (inbox q<N> file) is that quarter's treatment source: its waterfall,
            # and its TX_NEW / IIT by date, replacing the current linelist's dates inside the quarter
            qs = quarter_start(d, p.fy_start.month).isoformat()
            stock = pd.concat([stock[~stock["measure"].str.startswith("appt_")],
                               stock[stock["measure"] == "prev_tx_curr"].assign(measure="wf_prev")], ignore_index=True)
            flows = ("tx_new_q", "iit_q")
            inq = lambda y: y["measure"].isin(flows) & (y["period"].fillna("") >= qs) & (y["period"].fillna("") <= d.isoformat())
            parts[1] = parts[1][~inq(parts[1])]
            parts.append(attach_master(x[inq(x)], master, "quarter_linelist", {}))
        else:
            stock = stock[~stock["measure"].str.match(r"^(?:wf_|appt_)")]
        parts.append(attach_master(stock.assign(period=f"SL{d.isoformat()}"), master, "past_linelist", {}))
    if pbs_source == "ndr":
        nd = [d for d, _ in (past or {}).get("ndr", [])]
        ndr_date = max(nd) if nd else None          # the newest saved copy is today's file
    for d, pb_old in (past or {}).get("ndr", []):
        if d == ndr_date or d >= ll_asof or pb_old.empty:   # on/after the linelist date: today's files cover it
            continue
        x = pbs_cube(pb_old, pc)
        x = x[x["period"].fillna("") == ""] if "period" in x else x
        parts.append(attach_master(x.assign(period=f"SN{d.isoformat()}"), master, "past_ndr", {}))
    # EMR waterfall start: the previous_quarter_status table's own Active count (deck method)
    if prev_quarter is not None and len(prev_quarter):
        pq = prev_quarter[_key(prev_quarter["status"]).isin({"active", "actives"})]
        parts.append(attach_master(pq.groupby(["state", "facility"]).size().rename("value").reset_index()
                                   .assign(lga="", sex="U", age=UNKNOWN_AGE, mod="", period="", measure="wf_prev"),
                                   master, "prev_quarter", {}))
    else:   # no previous_quarter_status table (linelist_flat): start from the linelist's own previous-quarter status
        ll_part = parts[1]
        parts.append(ll_part[ll_part["measure"] == "prev_tx_curr"].assign(measure="wf_prev"))
    # appointments from 120 days before the linelist date (a visit counts as missed 28 days after
    # the expected date) to the end of the month two months ahead; the dashboard picks a range inside
    appt_cl = appointment_clients(linelist, cols["linelist"], pbs_src if pbs_source == "ndr" else pd.DataFrame(),
                                  pc, ll_asof, as_of, lo=min(ll_asof - dt.timedelta(days=120), p.fy_start),
                                  hi=(pd.Timestamp(as_of) + pd.offsets.MonthEnd(3)).date())
    if pbs_source == "ndr":
        cdr = manual.get("cdr_linelist")
        conc_rows, conc_cl = concurrence(cdr if cdr is not None else linelist, cols["linelist"], pbs_src, pc)
        parts.append(attach_master(conc_rows, master, "concurrence", {}))
        from .measures import quarter_start
        parts.append(attach_master(ndr_waterfall(pbs_src, pc, p.fy_start, as_of),   # IIT is counted from 1 October
                                   master, "ndr_waterfall", {}))
    else:
        conc_cl = pd.DataFrame()

    # Facility-level manual inputs go into the cube too, so they drill
    ssv, dqa, ccmp = manual.get("joint_ssv"), manual.get("dqa"), manual.get("ccmp")
    base = dict(sex="U", age=UNKNOWN_AGE, mod="", period="")
    if ssv is not None and len(ssv):
        vd = pd.to_datetime(ssv["visit_date"]).dt.date
        s = ssv[(vd >= p.q_start) & (vd <= p.as_of)]
        if len(s):
            parts.append(attach_master(s.groupby(["state", "facility"]).size().rename("value").reset_index()
                                       .assign(lga="", measure="joint_ssv", **base), master, "joint_ssv", dq))
    if dqa is not None and len(dqa):
        d = dqa[pd.to_datetime(dqa["dqa_date"]).dt.date >= p.fy_start].drop_duplicates("facility")
        parts.append(attach_master(d.assign(lga="", value=1, measure="dqa_sites", **base)[DIMS + ["measure", "value"]],
                                   master, "dqa", dq))
    cube = pd.concat([x for x in parts if len(x)], ignore_index=True)[DIMS + ["measure", "value"]]
    # Headline TX_CURR: the NDR count (valid recognised prints + all <15), as CDC measures it,
    # when the NDR line list is loaded; otherwise the EMR linelist count. tx_curr_emr stays alongside.
    tx_src = "ndr_tx_curr" if pbs_source == "ndr" and (cube["measure"] == "ndr_tx_curr").any() else "tx_curr_emr"
    head = cube[cube["measure"] == tx_src].assign(measure="tx_curr")
    cube = pd.concat([cube[cube["measure"] != "ndr_tx_curr"], head], ignore_index=True)
    tx_curr_source = "NDR" if tx_src == "ndr_tx_curr" else "EMR"

    # State-level inputs that cannot be placed at a facility
    state_measures: dict = {}
    if ccmp is not None and len(ccmp):
        c = ccmp[pd.to_datetime(ccmp["month"]).dt.strftime("%Y-%m").isin(p.q_months)]
        for st, g in c.groupby("state"):
            state_measures.setdefault(st, {})["post_resp"] = float(g["post_resp"].sum())
        state_measures.setdefault(SPEED, {})["post_resp"] = float(c["post_resp"].sum())
    nao = naomi if naomi is not None else pd.DataFrame()
    for scope in [SPEED] + sorted(master["state"].dropna().unique()):
        g = nao if scope == SPEED else nao[nao["state"] == scope] if len(nao) else nao
        state_measures.setdefault(scope, {})["plhiv_est"] = float(g["plhiv"].sum()) if len(g) else None
        n_sites = master["facility"].nunique() if scope == SPEED else master[master["state"] == scope]["facility"].nunique()
        state_measures[scope]["sites_total"] = float(n_sites)

    rule = settings["reporting"]["week_to_month_rule"]
    values = scope_values(cube, p, state_measures, rule)
    for vals in values.values():            # a measure with no rows in a scope is a true zero
        for m in cube["measure"].unique():
            vals.setdefault(m, 0.0)
    cur_t = targets[targets["fy"] == settings["fiscal_year"]] if "fy" in targets else targets
    scorecard = evaluate(reg, values, cur_t, p, dhis_through, settings["rag"])

    months = sorted(merged["month"].unique())[-4:]
    tr = merged[merged["month"].isin(months) & merged["table"].eq("HTS_TST")]
    trend = []
    for (mo, st, src), g in tr.groupby(["month", "state", "source"]):
        dn = g["dataname"].str.lower()
        trend.append(dict(month=mo, state=st, source=src,
                          hts_tst=float(g.loc[dn == "hts_tst", "value"].sum()),
                          hts_tst_pos=float(g.loc[dn == "hts_tst_pos", "value"].sum())))

    def recs(df):
        return [{k: (bool(v) if type(v).__name__ in ("bool", "bool_") else
                     _clean(v) if isinstance(v, (int, float)) else v) for k, v in r.items()}
                for r in df.to_dict("records")]

    # Trends: estimated week-ends (Sundays) over the history window + exact saved snapshots
    ends = week_ends(history_start, as_of)
    lc = cols["linelist"]
    est = pd.concat([linelist_backfill(linelist, lc, ends), pbs_backfill(pbs_src, pc, ends)], ignore_index=True)
    snap_today = snapshot_rows(cube, as_of)
    snaps = pd.concat([history, snap_today], ignore_index=True) if history is not None and len(history) else snap_today
    snaps = snaps.drop_duplicates(["date", "state", "facility", "measure"], keep="last")

    registry = [dict(ind, group=g) for g, key in (("IPI", "ipi"), ("Non-IPI", "non_ipi"), ("Conventional", "conventional"))
                for ind in reg[key]]
    t = targets.copy()
    t = t.astype(object).where(t.notna(), None)
    data = {
        "meta": dict(programme=settings["programme"], fiscal_year=settings["fiscal_year"], quarter=p.label,
                     q_index=p.q_index, q_start=p.q_start.isoformat(), q_end=p.q_end.isoformat(),
                     q_months=p.q_months, as_of=as_of.isoformat(), fy_start=p.fy_start.isoformat(),
                     dhis_through=dhis_through.isoformat(), elapsed=p.elapsed(dhis_through),
                     elapsed_asof=p.elapsed(), generated_at=dt.datetime.now().isoformat(timespec="minutes"),
                     demo=demo, rag=settings["rag"], naomi_loaded=bool(len(nao)), age5=AGE5,
                     pbs_source=pbs_source, linelist_as_of=ll_asof.isoformat(), tx_curr_source=tx_curr_source,
                     treatment_source=linelist.attrs.get("source", "emr"),
                     eac_as_of=_eac_date(manual.get("eac"), cols.get("eac", {})), eac_source=manual.get("eac_source", ""),
                     linelist_note=manual.get("linelist_note", "")),
        "cube": encode_cube(cube, master),
        "pbs_clients": encode_clients(pbs_clients(pbs_src, pc), master, dq),
        "conc_clients": encode_conc_clients(conc_cl, master),
        "appt_clients": encode_appt_clients(appt_cl, master),
        "vl_clients": encode_vl_clients(vl_clients(linelist, cols["linelist"], ll_asof), master),
        "geo": manual.get("geo") or [],
        "eac_clients": encode_eac_clients(eac_clients(manual.get("eac"), cols.get("eac", {}), history_start), master),
        "trend_series": encode_trend(est, snaps, master, dq),
        "methods": encode_methods(merged, master, quarters(history_start, p), settings["dhis2"]["tables"], rule),
        "columns": settings["columns"],
        "periods": [period_meta(k, rule) for k in sorted(cube["period"].fillna("").unique().tolist())],
        "quarters": quarters(history_start, p),
        "registry": registry,
        "targets": t.to_dict("records"),
        "state_measures": state_measures,
        "naomi": recs(nao) if len(nao) else [],
        "dataflow": recs(month_stat),
        "trend": trend,
        "dq": dq,
    }
    sc = pd.DataFrame([dict(code=i["code"], group=i["group"], scope=s, **{k: v for k, v in r.items()})
                       for i in scorecard for s, r in i["by_scope"].items()])
    data["_snapshot"] = snap_today   # popped by run_pipeline, which saves it (live runs only)
    return data, sc, cube
