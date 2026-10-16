"""Synthetic data shaped like the real sources, for testing the pipeline end to
end without server access. Facility names and all values are invented;
volumes are scaled to FY26 Q4 programme levels. Never mix with real data."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(26)
OUT = Path(__file__).resolve().parents[1] / "data" / "demo"

LGAS = {
    "Delta": ["Warri South", "Oshimili South", "Sapele", "Ughelli North", "Uvwie", "Ika South", "Ethiope East", "Okpe"],
    "Ekiti": ["Ado-Ekiti", "Ido-Osi", "Ikere", "Ijero", "Oye", "Ikole"],
    "Osun": ["Osogbo", "Olorunda", "Ife Central", "Ilesa East", "Ede North", "Iwo", "Ilesa West"],
}
N_FAC = {"Delta": 52, "Ekiti": 20, "Osun": 33}
TXC = {"Delta": 47100, "Ekiti": 8100, "Osun": 21500}
KINDS = ["General Hospital", "Comprehensive Health Centre", "PHC", "Cottage Hospital", "Mission Hospital"]


def facilities():
    rows = []
    for st, lgas in LGAS.items():
        for i in range(N_FAC[st]):
            lga = lgas[i % len(lgas)]
            kind = KINDS[(i // len(lgas)) % len(KINDS)]
            n = i // (len(lgas) * len(KINDS)) + 1
            rows.append(dict(state=st, lga=lga, facility=f"{lga} {kind}" + (f" {n}" if n > 1 else "")))
    f = pd.DataFrame(rows).drop_duplicates("facility")
    w = rng.pareto(1.6, len(f)) + 0.3          # a few big sites carry most clients
    f["weight"] = w
    f["weight"] = f.groupby("state")["weight"].transform(lambda s: s / s.sum())
    return f


def linelist(fac, as_of):
    rows = []
    for st, g in fac.groupby("state"):
        n_total = int(TXC[st] / 0.86)
        idx = rng.choice(len(g), size=n_total, p=g["weight"].values)
        sub = g.iloc[idx][["state", "lga", "facility"]].reset_index(drop=True)
        rows.append(sub)
    d = pd.concat(rows, ignore_index=True)
    n = len(d)
    d["uniqueid"] = [f"DEMO{i:06d}" for i in range(n)]
    status = rng.choice(["Active", "LTFU", "Transferred out", "Death", "Stopped"], size=n,
                        p=[0.862, 0.098, 0.022, 0.012, 0.006])
    prev = status.copy()
    flip = rng.random(n)
    prev[(status == "Active") & (flip < 0.010)] = "LTFU"          # returned to treatment
    prev[(status != "Active") & (flip < 0.22)] = "Active"           # lost this quarter
    start_days = rng.integers(10, 365 * 15, size=n)
    new = rng.random(n) < 0.0019
    start_days[new] = rng.integers(0, (as_of - dt.date(2026, 10, 1)).days + 1, size=new.sum())
    prev[new] = ""
    d["artstartdate"] = [as_of - dt.timedelta(days=int(x)) for x in start_days]
    d["daysonart"] = start_days
    d["ti"] = np.where(rng.random(n) < 0.03, "Yes", "No")
    d["currentartstatus"] = status
    d["artstatus_previousquarter"] = prev
    d["pqs_status"] = prev
    d["outcomesdate"] = [as_of - dt.timedelta(days=int(rng.integers(1, 400))) if s in ("Death", "Transferred out", "Stopped")
                         else None for s in status]
    refill = rng.choice([30, 90, 180], size=n, p=[0.15, 0.6, 0.25])
    d["daysofarvrefill"] = refill
    pick = []
    for s, r in zip(status, refill):
        if s == "LTFU":
            pick.append(as_of - dt.timedelta(days=int(r + 28 + rng.integers(0, 120))))
        else:
            pick.append(as_of - dt.timedelta(days=int(rng.integers(0, r))))
    d["pharmacylastpickupdate"] = pick
    has_vl = rng.random(n) < 0.96
    d["dateofcurrentviralload"] = [as_of - dt.timedelta(days=int(rng.integers(5, 340 if h else 600)))
                                   for h in has_vl]
    sup = rng.random(n) < 0.976
    und = rng.random(n) < 0.93
    d["currentviralload"] = np.where(sup, np.where(und, rng.integers(0, 49, n), rng.integers(50, 999, n)),
                                     rng.integers(1000, 250000, n))
    d["currentage"] = np.where(rng.random(n) < 0.025, rng.integers(1, 15, n),
                               np.clip(rng.normal(41, 11, n), 15, 84).astype(int))
    d["sex"] = rng.choice(["F", "M"], size=n, p=[0.66, 0.34])
    d["pbs"] = np.where(rng.random(n) < 0.97, "Yes", "No")
    d["recapture"] = np.where(rng.random(n) < 0.95, "Yes", "")
    return d.rename(columns={"facility": "facilityname"})


def pbs(ll):
    """Same columns and codes as the NDR PBS line list (lower-cased on load)."""
    a = ll[["state", "facilityname", "currentartstatus", "artstartdate", "pharmacylastpickupdate", "sex", "currentage",
            "uniqueid"]].copy()
    n = len(a)
    a = a.rename(columns={"state": "statename", "currentartstatus": "currentstatus28",
                          "pharmacylastpickupdate": "lastdrugpickupdate", "uniqueid": "unique_id"})
    a["patientartnumber"] = [f"ART-{i:05d}" for i in range(n)]
    a["hospitalnumber"] = [str(1000 + i) for i in range(n)]
    cap = rng.random(n) < 0.904
    a["fingerprint enrolled"] = np.where(cap, "YES", "NO")
    a["date_captured"] = [dt.date(2026, 10, 10) - dt.timedelta(days=int(rng.integers(1, 1500))) if c else None for c in cap]
    st = rng.choice(["ENROLLED", "ADJUDICATIONWAITING", "DUPLICATE", "NO_VALID_DATA"], size=n, p=[0.955, 0.014, 0.028, 0.003])
    a["fingerprintstatus"] = np.where(cap, st, "")
    a["fingerprint valid"] = np.where(cap & (st != "NO_VALID_DATA"), "YES", "NO")
    a["duplicate_type"] = np.where(cap & (rng.random(n) < 0.0055), rng.choice(["Duplicate", "Mis-Match"], size=n, p=[.28, .72]), "")
    a["recapture fingerprint enrolled"] = np.where(cap & (rng.random(n) < 0.84), "YES", "NO")
    a["match_outcome"] = np.where(a["recapture fingerprint enrolled"] == "YES",
                                  rng.choice(["Match", "No match", "No base"], size=n, p=[0.968, 0.031, 0.001]), "")
    return a


WEEKLY_PER_FAC = {  # programme weekly volume -> (profile) spread across facilities
    # HTS: one stream per modality, routed to the real DHIS2 element by _hts_element()
    ("HTS_TST", "HTS_TST"): (6700, "hts"), ("HTS_TST", "HTS_TST_POS"): (118, "hts_pos"),
    ("PREP", "PrEP_NEW"): (190, "adult"),
    ("HIVST", "HTS_SELF_ASSISTED"): (500, "adult"), ("HIVST", "HTS_SELF_UNASSISTED"): (400, "adult"),
    ("PMTCT", "PMTCT_STAT_DEN"): (1880, "preg"), ("PMTCT", "PMTCT_STAT_NUM_Prev known"): (26, "preg"),
    ("PMTCT", "PMTCT_ART"): (29, "preg"),
    ("PMTCT_COMM", "Community_PMTCT_STAT_DEN"): (730, "preg"),
    ("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New neg"): (726, "preg"),
    ("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New pos"): (4, "preg"),
    ("PMTCT_COMM", "Community_PMTCT_ART_New_on_ART"): (4, "preg"),
    ("PMTCT_EID", "PMTCT_EID"): (40, "infant"), ("PMTCT_EID", "PMTCT_HEI_POS"): (0.9, "infant"),
    ("TB", "TB_PREV_NUM_Newly enrolled on ART"): (95, "adult"),
    ("TB", "TB_PREV_DEN_Newly enrolled on ART"): (120, "adult"), ("TB", "TB_ART_New on ART"): (0.5, "adult"),
    ("TX_TB", "TX_TB_DEN"): (2000, "adult"), ("TX_TB", "TX_TB_NUM_Newly started on ART"): (18, "adult"),
    ("TX_TB", "TX_TB_POS_New on ART"): (56, "adult"), ("TX_TB", "TX_TB_Specimen sent"): (50, "adult"),
    ("TX_TB", "TX_TB_Pos result"): (20, "adult"),
    ("AHD", "AHD_MSF-Confirmed  AHD _New"): (37, "adult"),
}
_HTS_SUFFIX = {"Other PITC": "OTHER PITC", "VCT": "VCT", "Emergency": "EMERGENCY", "Inpatient": "INPATIENT",
               "Mobile": "MOBILE", "Paediatrics": "PAEDIATRICS", "Post ANC1": "PMTCT (POST ANC1: Pregnancy)"}
_INDEX_RESULT = "HTS_INDEX_CONTACTS TEST RESULT( Includes Previously Known)"


def _hts_element(mod: str, pos: bool, sex_age: str) -> tuple[str, str, str]:
    """Demo HTS modality -> (table, data element, category option combo) as in DHIS2."""
    if mod in _HTS_SUFFIX:
        return "HTS_TST", f"HTS_TST_{'POS_' if pos else ''}{_HTS_SUFFIX[mod]}", sex_age
    if mod == "ANC1":
        age = sex_age.split(", ")[1]
        return "PMTCT", "PMTCT_STAT_NUM (Newly Tested)", f"{age} years, {'Newly Tested Positives' if pos else 'New Negative'}"
    if mod == "Index":
        return "INDEX", _INDEX_RESULT, f"{'New positives' if pos else 'New Negative'}, {sex_age}"
    return "TB", "TB_STAT_POS_New positive" if pos else "TB_STAT_NUM", sex_age  # TB Clinic
BANDS = ["<1", "1-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39", "40-44", "45-49",
         "50-54", "55-59", "60-64", "65+"]
ADULT_AGE = np.array([.003, .01, .01, .012, .08, .15, .17, .15, .12, .1, .07, .05, .035, .02, .02])
PREG_AGE = np.array([0, 0, 0, 0, .1, .22, .26, .2, .14, .06, .02, 0, 0, 0, 0])
MODS = {"ANC1": (115010, 349), "Other PITC": (94222, 2539), "Index": (11094, 970), "VCT": (7602, 105),
        "TB Clinic": (4248, 364), "Post ANC1": (3203, 11), "Emergency": (2828, 65), "Inpatient": (2687, 33),
        "Mobile": (1242, 9), "Paediatrics": (67, 1)}


def _profile(name):
    """-> list of disagg labels and probabilities"""
    labs, ps = [], []
    if name in ("hts", "hts_pos"):
        mw = np.array([v[0 if name == "hts" else 1] for v in MODS.values()], float); mw /= mw.sum()
        for m, pm in zip(MODS, mw):
            fem = 1.0 if m in ("ANC1", "Post ANC1") else 0.58
            age = PREG_AGE if m in ("ANC1", "Post ANC1") else ADULT_AGE
            if m == "Paediatrics":
                age = np.array([.2, .4, .25, .15] + [0] * 11)
            age = age / age.sum()
            for sx, psx in (("Female", fem), ("Male", 1 - fem)):
                for b, pa in zip(BANDS, age):
                    if pm * psx * pa > 0:
                        labs.append(f"{sx}, {b}, {m}"); ps.append(pm * psx * pa)
    else:
        age = {"adult": ADULT_AGE, "preg": PREG_AGE, "infant": np.array([1.0] + [0] * 14)}[name]
        age = age / age.sum()
        fem = {"adult": 0.6, "preg": 1.0, "infant": 0.5}[name]
        for sx, psx in (("Female", fem), ("Male", 1 - fem)):
            for b, pa in zip(BANDS, age):
                if psx * pa > 0:
                    labs.append(f"{sx}, {b}"); ps.append(psx * pa)
    ps = np.array(ps); return labs, ps / ps.sum()


def dhis(fac, as_of):
    weeks = [f"2026W{w:02d}" for w in range(27, 42)]
    profiles = {k: _profile(k) for k in ("hts", "hts_pos", "adult", "preg", "infant")}
    fw = fac.set_index("facility")["weight"] * fac.set_index("facility")["state"].map(
        {"Delta": 0.55, "Ekiti": 0.2, "Osun": 0.25})
    meta = fac.set_index("facility")
    wrows = []
    for wk in weeks:
        last = wk == weeks[-1]
        for f, w in fw.items():
            if last and rng.random() < 0.35:   # current week: not everyone has entered yet
                continue
            st, lga = meta.loc[f, "state"], meta.loc[f, "lga"]
            for (tbl, dn), (vol, prof) in WEEKLY_PER_FAC.items():
                tot = rng.poisson(vol * w * (0.55 if last else 1.0))
                if not tot:
                    continue
                labs, ps = profiles[prof]
                cnt = rng.multinomial(tot, ps)
                for i in np.nonzero(cnt)[0]:
                    t, d, lab = tbl, dn, labs[i]
                    if prof in ("hts", "hts_pos"):
                        sx, b, mod = lab.split(", ")
                        t, d, lab = _hts_element(mod, prof == "hts_pos", f"{sx}, {b}")
                    wrows.append((t, wk, f.lower(), st, lga, f, d, lab, float(cnt[i])))
    wdf = pd.DataFrame(wrows, columns=["table", "period", "facility_key", "state", "lga", "facility",
                                       "dataname", "disagg", "value"])
    from etl.period_merge import week_to_month
    wdf["month"] = wdf["period"].map(week_to_month)
    # Monthly reports: Jul & Aug complete, Sep trickling in (60%), Oct not due
    fac_list = sorted(wdf["facility"].unique())
    late = set(fac_list[i] for i in range(len(fac_list)) if (i * 7) % 10 >= 6)
    m = wdf[(wdf["month"] != "2026-10") & ~((wdf["month"] == "2026-09") & wdf["facility"].isin(late))]
    m = (m.groupby(["table", "month", "facility_key", "state", "lga", "facility", "dataname", "disagg"])["value"]
          .sum().reset_index())
    m["value"] = np.round(m["value"] * rng.uniform(0.98, 1.06, len(m)))
    m["period"] = m["month"].str.replace("-", "")
    return wdf.drop(columns="month"), m.drop(columns="month")


def main(as_of=dt.date(2026, 10, 10)):
    OUT.mkdir(parents=True, exist_ok=True)
    fac = facilities()
    ll = linelist(fac, as_of)
    w, m = dhis(fac, as_of)
    fac[["state", "lga", "facility"]].to_csv(OUT / "facilities.csv", index=False)
    ll.to_parquet(OUT / "linelist.parquet", index=False)
    pbs(ll).to_parquet(OUT / "pbs_all.parquet", index=False)
    w.to_parquet(OUT / "dhis_weekly.parquet", index=False)
    m.to_parquet(OUT / "dhis_monthly.parquet", index=False)
    pd.DataFrame(columns=["state", "facility", "visit_date", "gon_entities"]).to_csv(OUT / "joint_ssv_visits.csv", index=False)
    dq = fac.sample(4, random_state=1)[["state", "facility"]].assign(dqa_date="2026-10-07")
    dq.to_csv(OUT / "dqa_sites.csv", index=False)
    pd.DataFrame({"state": list(LGAS), "month": "2026-10-01", "post_resp": [26, 15, 9]}).to_csv(OUT / "ccmp.csv", index=False)
    print(f"demo data written to {OUT}  (linelist {len(ll):,} rows, weekly {len(w):,}, monthly {len(m):,})")


if __name__ == "__main__":
    main()
