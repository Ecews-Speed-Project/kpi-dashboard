"""ECEWS SPEED KPI pipeline.

    python run_pipeline.py discover   # list datasets / org-unit levels on both DHIS2 instances
    python run_pipeline.py run        # live: DHIS2 + central repository + Excel -> dashboard
    python run_pipeline.py demo       # synthetic data, no server access needed

Output: live/index.html (always the latest; refresh it) plus a dated copy in
archive/. Add --export for QA CSVs in exports/. New source files dropped in
inbox/ are picked up automatically (newest wins).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from etl.period_merge import merge_weekly_monthly, month_status  # noqa: E402
from kpi.engine import run_engine  # noqa: E402
from kpi.targets import load_targets  # noqa: E402

OUT = ROOT / "data" / "out"
HISTORY = ROOT / "data" / "history" / "snapshots.csv"   # facility stock figures, one set per live-run day
SOURCES = ROOT / "data" / "history" / "sources"         # dated copies of the linelist and the NDR PBS file


def merge_cached(old: pd.DataFrame, new: pd.DataFrame, pull_from: dt.date) -> pd.DataFrame:
    """Incremental DHIS2 refresh: cached rows for periods starting before pull_from,
    plus the fresh pull (which only holds periods starting on/after pull_from)."""
    from etl.period_merge import period_start
    starts = new["period"].map(period_start) if len(new) else pd.Series(dtype=object)
    fresh = new[starts >= pull_from] if len(new) else new
    return pd.concat([old[old["period"].map(period_start) < pull_from], fresh], ignore_index=True)


def cached_table(path: Path, reader) -> pd.DataFrame:
    """Read a large Excel source once; later runs use a parquet copy until the file changes."""
    path = Path(path)
    if not path.exists() or path.suffix.lower() == ".csv":
        return reader(path)
    st = path.stat()
    cdir = ROOT / "data" / "raw" / "cache" / "files"
    f = cdir / f"{path.stem}_{int(st.st_mtime)}_{st.st_size}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    df = reader(path)
    cdir.mkdir(parents=True, exist_ok=True)
    for old in cdir.glob(f"{path.stem}_*.parquet"):
        old.unlink(missing_ok=True)
    out = df.copy()
    for c in out.columns:            # mixed-type text columns: store as text, keep blanks blank
        if out[c].dtype == object:
            out[c] = out[c].map(lambda x: None if pd.isna(x) else str(x))
    out.to_parquet(f, index=False)
    return df


def load_history() -> pd.DataFrame:
    return pd.read_csv(HISTORY, dtype={"date": str}) if HISTORY.exists() else pd.DataFrame()


def _as_text(df: pd.DataFrame) -> pd.DataFrame:
    """Mixed-type text columns as text (parquet needs one type per column); blanks stay blank."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].map(lambda x: None if pd.isna(x) else str(x))
    return out


def archive_sources(cfg: dict, linelist: pd.DataFrame, pbs: pd.DataFrame, pbs_path, as_of: dt.date) -> None:
    """Keep a dated copy of each new linelist (named by its generation date) and NDR PBS file
    (by the date in its file name, else its modified date), so past periods can show the stock
    figures of their time. Only the columns the pipeline reads are kept (no names). Copies older
    than reporting.source_keep_days are thinned to the last one of each month."""
    import re
    from kpi.engine import linelist_date
    SOURCES.mkdir(parents=True, exist_ok=True)
    cols = cfg["columns"]
    saved = []
    if len(linelist):
        d = linelist_date(linelist, cols["linelist"], as_of)
        f = SOURCES / f"linelist_{d.isoformat()}.parquet"
        # (re)save when new, or when the saved copy lacks columns the pipeline now reads
        stale = f.exists() and not set(linelist.columns) <= set(pd.read_parquet(f).columns)
        if (not f.exists() or stale) and not getattr(linelist, "attrs", {}).get("from_copy"):
            _as_text(linelist).to_parquet(f, index=False)
            saved.append(f.name + (" (refreshed)" if stale else ""))
    if len(pbs) and pbs_path is not None and Path(pbs_path).exists():
        m = re.search(r"(\d{4}-\d{2}-\d{2})", Path(pbs_path).name)
        d = m.group(1) if m else dt.date.fromtimestamp(Path(pbs_path).stat().st_mtime).isoformat()
        f = SOURCES / f"ndr_{d}.parquet"
        if not f.exists():
            keep = [c for c in dict.fromkeys(str(v) for v in cols["pbs_all"].values()) if c in pbs.columns]
            _as_text(pbs[keep] if keep else pbs).to_parquet(f, index=False)
            saved.append(f.name)
    if saved:
        print(f"  saved dated copies: {', '.join(saved)}")
    # thin old copies: past keep_days, only the last copy of each month stays
    cut = as_of - dt.timedelta(days=int(cfg["reporting"].get("source_keep_days", 120)))
    for kind in ("linelist", "ndr"):
        old = sorted(SOURCES.glob(f"{kind}_*.parquet"))
        by_month = {}
        for f in old:
            by_month.setdefault(f.stem.split("_")[1][:7], []).append(f)
        for fs in by_month.values():
            for f in fs[:-1]:
                if dt.date.fromisoformat(f.stem.split("_")[1]) < cut:
                    f.unlink(missing_ok=True)


def lga_geometry(cfg: dict, client, refresh: bool = False) -> list:
    """LGA polygons for the programme states from the DHIS2 map layer (geoFeatures, level 3),
    simplified to keep the dashboard small. Cached in data/raw/cache/lga_geo.json."""
    import json
    f = ROOT / "data" / "raw" / "cache" / "lga_geo.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        feats = client.get("geoFeatures", ou="ou:LEVEL-3", displayProperty="NAME")
    except Exception as e:
        print(f"  LGA map: DHIS2 geoFeatures not available ({type(e).__name__})")
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    states = [s.lower() for s in cfg["states"]]

    def simplify(pts, tol=0.0012):
        if len(pts) < 4:
            return pts
        import math
        a, b = pts[0], pts[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1e-12
        dmax, idx = 0, 0
        for i in range(1, len(pts) - 1):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + b[0] * a[1] - b[1] * a[0]) / L
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol:
            return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)
        return [a, b]

    out = []
    for g in feats:
        pn = str(g.get("pn", "")).lower()
        if g.get("ty") != 2 or not any(s in pn for s in states):
            continue
        rings = []
        for poly in json.loads(g["co"]):
            for ring in poly[:1]:                      # outer ring only
                pts = [[round(x, 3), round(y, 3)] for x, y in ring]
                h = len(pts) // 2                     # a closed ring: simplify its two halves
                r = simplify(pts[:h + 1])[:-1] + simplify(pts[h:])
                if len(r) >= 4:
                    rings.append(r)
        out.append({"lga": g["na"], "state": next(s for s in cfg["states"] if s.lower() in pn), "rings": rings})
    f.write_text(json.dumps(out), encoding="utf-8")
    print(f"  LGA map: {len(out)} LGA boundaries from DHIS2")
    return out


# Saved copies of the old CDR "linelist" table use other column names; read them as linelist_flat
LEGACY_LINELIST = {"uniqueid": "unique_id", "datetransferedin": "date_transfered_in", "pharmacylastpickupdate": "pharmacy_lastpickupdate",
                   "currentage": "current_age", "outcomesdate": "outcomes_date", "datimcode": "datim_code", "pqs_status": "artstatus_previousquarter"}


def legacy_linelist(df: pd.DataFrame) -> pd.DataFrame:
    if "uniqueid" not in df.columns:
        return df
    df = df.rename(columns=LEGACY_LINELIST)
    if "period" not in df.columns and "date_generated" in df.columns:
        from kpi.measures import _datetimes
        d = _datetimes(df["date_generated"]).dropna()
        df["period"] = d.max().date().isoformat() if len(d) else ""
    return df


def quarter_linelist(cfg: dict) -> list:
    """Quarter-end treatment line list in the inbox (NDR / PHIS3 layout, columns per quarter _Q1.._Q4)
    -> dated copies in data/history/sources (linelist_<quarter end> and ndr_<quarter end>), marked
    'quarter_file', so that quarter's treatment numbers (TX_CURR, TX_NEW, waterfall, VL, MMD, DSD,
    biometrics) come from it. Status, pickup, refill, VL and age are the quarter-end values; TX_CURR
    unique follows the file's q<N>_algo (1 under 15, 2 enrolled, 3 duplicate of an inactive parent,
    4 justification). Converted once per file (re-done when the file changes)."""
    import re
    inbox = folder(cfg, "inbox", "inbox")
    files = sorted({f for pat in cfg.get("inbox_patterns", {}).get("quarter_linelist", []) for f in inbox.glob(pat)})
    done = []
    for f in files:
        q = int(re.match(r"[qQ]([1-4])", f.name).group(1))
        cache = SOURCES / f".quarter_{f.stem.replace(' ', '_')}.txt"
        stamp = f"{f.stat().st_size}:{int(f.stat().st_mtime)}"
        if cache.exists() and cache.read_text().strip().split("|")[0] == stamp:
            done.append(cache.read_text().strip().split("|")[1])
            continue
        d = pd.read_csv(f, dtype=str, low_memory=False)
        dmy = lambda s: pd.to_datetime(d[s], format="%d/%m/%Y", errors="coerce")
        # quarter end = the end of the calendar quarter holding the latest quarter-end pickup
        last = dmy(f"last_drug_pickup_date_Q{q}").max()
        end = (pd.Period(last, freq="Q").end_time.normalize()).date()
        prev_end = (pd.Timestamp(end) - pd.offsets.QuarterEnd(1)).date()

        def status(col, at):
            """Active / LTFU / Death / Transferred out / Discontinued Care at a quarter end."""
            s = pd.Series("LTFU", index=d.index)
            on = lambda flag, date: d[flag].fillna("").isin(["1", "YES", "Yes", "yes"]) & ~(dmy(date) > pd.Timestamp(at))
            s[on("patient_stopped_treatment", "patient_stopped_treatment_date")] = "Discontinued Care"
            s[on("patient_transferred_out", "transferred_out_date")] = "Transferred out"
            s[on("patient_has_died", "patient_deceased_date")] = "Death"
            s[d[col].fillna("").str.lower().eq("active")] = "Active"
            return s

        cur, prev = status(f"currentStatus_28_Q{q}", end), status(f"currentStatus_28_Q{q - 1}" if q > 1 else "currentStatus_28", prev_end)
        algo = d.get(f"q{q}_algo", pd.Series("", index=d.index)).fillna("")
        active = cur.eq("Active")
        # valid print: the unique classification for clients active at the quarter end, the print itself otherwise
        valid = (active & algo.isin(["2", "3", "4"])) | (~active & d["Fingerprint.Valid"].fillna("").str.upper().eq("YES"))
        start = dmy("art_start_date")
        iso = lambda s: s.dt.strftime("%Y-%m-%d").fillna("")
        dq = lambda k: d[f"{k}_Q{q}"] if f"{k}_Q{q}" in d else d[k]
        outcome = pd.concat([dmy("patient_deceased_date"), dmy("transferred_out_date"), dmy("patient_stopped_treatment_date")], axis=1).max(axis=1)
        pick, refill = pd.to_datetime(dq("last_drug_pickup_date"), format="%d/%m/%Y", errors="coerce"), pd.to_numeric(dq("days_of_arv_refill"), errors="coerce")
        ll = pd.DataFrame({
            "unique_id": d["pid"], "period": end.isoformat(), "state": d["state_name"], "lga": d["lga_name"],
            "facilityname": d["facility_name"], "currentartstatus": cur, "artstatus_previousquarter": prev,
            "lastdateofsamplecollection": dq("date_of_current_sample_collection"),
            "date_transfered_in": d["transferred_in_date"],
            "estimatednextappointmentpharmacy": iso(pick + pd.to_timedelta(refill, unit="D")),
            "currentregimenline": d["regimen_line_code"], "currentartregimen": dq("last_drug_regimen"),
            "artstartdate": iso(start), "pharmacy_lastpickupdate": iso(pick), "daysofarvrefill": dq("days_of_arv_refill"),
            "daysonart": (pd.Timestamp(end) - start).dt.days.astype("Int64").astype(str).replace("<NA>", ""),
            "dateofcurrentviralload": dq("date_of_current_viral_load"), "currentviralload": dq("current_viral_load"),
            "ti": d["patient_transferred_in"].map({"1": "Yes", "0": "No"}).fillna(""),
            "sex": d["sex"], "current_age": dq("current_age"), "outcomes_date": iso(outcome),
            "datim_code": d["datim_code"], "pepid": d["patient_identifier"], "patienthospitalno": d["hospital_number"],
            "pbs": d["Fingerprint.Enrolled"].str.title(), "recapture": d["Recapture.Fingerprint.Enrolled"].str.title(),
            "pbsdatecreated": d["Date_Captured"], "validbiometric": valid.map({True: "Yes", False: "No"}),
            "indication_ahd": d["Indications"].where(d["Indications"].str.upper().eq("YES"), ""),
            "cd4_lfa_result": d["cd4_lfa_result"], "firstcd4": d["current_cd4_count"], "firstcd4date": d["date_of_current_cd4_count"],
            "other_test_(tb-lam_lf-lam_etc)": d["tb_lf_lam_result"], "serology_for_crag_result": d["serum_crag_result"],
            "csf_for_crag_result": d["csf_crag_result"], "whostage": d["clinical_stage"],
            "dsd_model": d["differentiated_service_delivery"].str.replace("Dispensing", " Dispensing"),
            "dsd_model_type": d["Dispensing"],
            "ndr_unique": algo.isin(["1", "2", "3", "4"]).map({True: "Yes", False: "No"}),   # TX_NEW unique rule
            "quarter_file": f.name,
        })
        prev_status = d[f"currentStatus_28_Q{q - 1}"] if q > 1 else d["currentStatus_28"]
        ndr = pd.DataFrame({
            "statename": d["state_name"], "datimcode": d["datim_code"], "unique_id": d["pid"],
            "patientartnumber": d["patient_identifier"], "hospitalnumber": d["hospital_number"],
            "fingerprint valid": d["Fingerprint.Valid"], "date_captured": d["Date_Captured"], "facilityname": d["facility_name"],
            "currentstatus28": d[f"currentStatus_28_Q{q}"], "fingerprint enrolled": d["Fingerprint.Enrolled"],
            "recapture fingerprint enrolled": d["Recapture.Fingerprint.Enrolled"], "match_outcome": "",
            "fingerprintstatus": d["FingerPrintStatus"], "duplicate_type": d["duplicate_type"].where(d["duplicate_type"].isin(["Duplicate", "Mis-Match"]), ""),
            "currentstatus28q4": prev_status, "patienttransferredin": d["patient_transferred_in"],
            "transferredindate": d["transferred_in_date"], "patienttransferredout": d["patient_transferred_out"],
            "transferredoutdate": d["transferred_out_date"], "patienthasdied": d["patient_has_died"],
            "patientdeceaseddate": d["patient_deceased_date"], "artstartdate": iso(start), "lastdrugpickupdate": iso(pick),
            "sex": d["sex"], "currentage": dq("current_age"),
            "ndr_unique": (active & algo.isin(["1", "2", "3", "4"])).map({True: "Yes", False: "No"}),
            "quarter_file": f.name,
        })
        SOURCES.mkdir(parents=True, exist_ok=True)
        ll.to_parquet(SOURCES / f"linelist_{end.isoformat()}.parquet", index=False)
        ndr.to_parquet(SOURCES / f"ndr_{end.isoformat()}.parquet", index=False)
        cache.write_text(f"{stamp}|{end.isoformat()}")
        print(f"  quarter line list {f.name}: Q{q} to {end} - TX_CURR {int(active.sum()):,}, "
              f"unique {int((active & algo.isin(['1', '2', '3', '4'])).sum()):,}")
        done.append(end.isoformat())
    return done


# CDR line-list columns the NDR file does not carry: joined onto the NDR clients
EMR_ONLY = ["regimen_line", "regimen", "ahd_indication", "cd4_lfa", "cd4", "cd4_date", "lflam", "crag", "csf_crag",
            "who_stage", "dsd_model", "dsd_type", "tb_status", "tb_status_date", "tb_cough", "tb_fever", "tb_weight",
            "tb_sweats", "tb_gxp", "tb_gxp_result", "tb_afb", "tb_afb_result", "tb_cxr", "tb_cxr_result", "tb_culture",
            "tb_culture_result", "tb_tx_start", "otz_enrolled", "otz_outcome", "otz_transitioned", "pbs", "recapture_flag", "pbs_date"]


def ndr_linelist(pbs: pd.DataFrame, cdr: pd.DataFrame, cfg: dict, file_date: dt.date) -> pd.DataFrame:
    """The NDR PBS line list in the CDR line-list layout, so every treatment measure reads the NDR:
    status (Active / LTFU / Death / Transferred out), previous fiscal quarter's status, ART start,
    pickup, refill, VL, transfer-in, age, sex, and the NDR unique rule (valid print not flagged
    'Duplicate', or under 15). EMR-only fields (DSD, AHD, TB screening, OTZ, regimen) are joined
    from the CDR line list by DATIM code + ART number."""
    pc, lc = cfg["columns"]["pbs_all"], cfg["columns"]["linelist"]
    col = lambda k, default="": pbs[k] if k in pbs else pd.Series(default, index=pbs.index)
    def day(s):
        """Excel day numbers, real dates or text dates (older NDR files store ART start as a date)."""
        if pd.api.types.is_datetime64_any_dtype(s):
            return pd.to_datetime(s, errors="coerce")
        s = pd.to_numeric(s, errors="coerce") if s.dtype != object else s
        d = pd.to_datetime(s, unit="D", origin="1899-12-30", errors="coerce") if pd.api.types.is_numeric_dtype(s) \
            else pd.to_datetime(s, errors="coerce", dayfirst=True)
        return d
    iso = lambda d: d.dt.strftime("%Y-%m-%d").fillna("")
    flag = lambda k: pd.to_numeric(col(k, 0), errors="coerce").fillna(0).eq(1) | col(k).astype(str).str.upper().eq("YES")
    fq = ((file_date.month - int(str(cfg["fy_start"])[5:7])) % 12) // 3 + 1          # fiscal quarter of the file
    prev_col = f"currentstatus28q{fq - 1 if fq > 1 else 4}"

    start, pick = day(col("artstartdate")), day(col("lastdrugpickupdate"))

    def status(s):
        """Inactive clients: transferred out only with a transfer dated on or after the last pickup (older or
        undated flags belong to an earlier episode, so the client counts as IIT); dead when flagged, unless
        the death is dated before the last pickup."""
        out = pd.Series("LTFU", index=pbs.index)
        out[flag("patienttransferredout") & (day(col("transferredoutdate")) >= pick)] = "Transferred out"
        out[flag("patienthasdied") & ~(day(col("patientdeceaseddate")) < pick)] = "Death"
        out[s.astype(str).str.strip().str.lower().eq("active")] = "Active"
        return out

    refill = pd.to_numeric(col("daysofarvrefill"), errors="coerce")
    # unique print, as TX_CURR_Unique: valid, not suppressed as a duplicate, not waiting for adjudication
    valid = (col("fingerprint valid").astype(str).str.upper().eq("YES") & ~col("duplicate_type").astype(str).str.lower().eq("duplicate")
             & ~col("fingerprintstatus").astype(str).str.upper().eq("ADJUDICATIONWAITING"))
    out = pd.DataFrame({
        "unique_id": col("unique_id").astype(str), "period": file_date.isoformat(),
        "state": col("statename"), "lga": col("lganame"), "facilityname": col("facilityname"),
        "currentartstatus": status(col("currentstatus28")), "artstatus_previousquarter": status(col(prev_col)),
        "lastdateofsamplecollection": iso(day(col("dateofcurrentsamplecollection"))),
        "date_transfered_in": iso(day(col("transferredindate"))),
        "estimatednextappointmentpharmacy": iso(pick + pd.to_timedelta(refill, unit="D")),
        "artstartdate": iso(start), "pharmacy_lastpickupdate": iso(pick), "daysofarvrefill": refill,
        "daysonart": (pd.Timestamp(file_date) - start).dt.days,
        "dateofcurrentviralload": iso(day(col("currentvldate"))), "currentviralload": pd.to_numeric(col("currentvl"), errors="coerce"),
        "ti": flag("patienttransferredin").map({True: "Yes", False: "No"}), "sex": col("sex"), "current_age": col("currentage"),
        "outcomes_date": iso(pd.concat([day(col("transferredoutdate")), day(col("patientdeceaseddate"))], axis=1).max(axis=1)),
        "datim_code": col("datimcode"), "pepid": col("patientartnumber"), "patienthospitalno": col("hospitalnumber"),
        "validbiometric": valid.map({True: "Yes", False: "No"}),
    })
    # EMR-only fields from the CDR line list (DATIM + ART number, punctuation and leading zeros ignored)
    emr_cols = [lc[k] for k in EMR_ONLY if lc.get(k) in cdr]
    if len(cdr) and emr_cols:
        def key(datim, art):
            a = art.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True).str.replace(r"\d+", lambda m: str(int(m.group())), regex=True)
            return datim.astype(str).str.strip().str.upper() + "|" + a
        e = cdr[emr_cols].assign(_k=key(cdr[lc["datim"]], cdr[lc["art_no"]]), _emr=True).drop_duplicates("_k")
        out = out.assign(_k=key(out["datim_code"], out["pepid"])).merge(e, on="_k", how="left").drop(columns="_k")
        if lc.get("hosp_no") in cdr:           # no ART-number match: try DATIM + hospital number
            miss = out["_emr"].isna()
            e2 = cdr[emr_cols].assign(_k=key(cdr[lc["datim"]], cdr[lc["hosp_no"]]), _emr=True).drop_duplicates("_k")
            m2 = out.loc[miss, ["patienthospitalno", "datim_code"]].assign(_k=lambda x: key(x["datim_code"], x["patienthospitalno"]))
            m2 = m2[["_k"]].reset_index().merge(e2, on="_k", how="inner").set_index("index")
            out.loc[m2.index, emr_cols + ["_emr"]] = m2[emr_cols + ["_emr"]]
        found = out["_emr"].fillna(False).astype(bool)
        # every TX_CURR client has a regimen: a blank one means the client is not in the EMR line list
        for k in ("regimen_line", "regimen"):
            if lc.get(k) in out:
                out.loc[~found, lc[k]] = "Not in EMR line list"
        out = out.drop(columns="_emr")
        print(f"  treatment from the NDR file ({len(out):,} clients); EMR fields joined for {found.mean():.0%} "
              f"(ART number, then hospital number)")
    # the previous quarter's TX_CURR is the one reported for it: the unique flag of each client in that
    # quarter's official NDR file (data/history/sources/ndr_<quarter end>.parquet), fixed until the next quarter
    from kpi.measures import quarter_start
    q_end = quarter_start(file_date, int(str(cfg["fy_start"])[5:7])) - dt.timedelta(days=1)
    qf = SOURCES / f"ndr_{q_end.isoformat()}.parquet"
    if qf.exists():
        q = pd.read_parquet(qf)
        if "ndr_unique" in q and "patientartnumber" in q:
            # quarter files and weekly downloads number clients differently: match on DATIM + ART number
            qkey = lambda datim, art: datim.astype(str).str.strip().str.upper() + "|" + art.astype(str).str.upper() \
                .str.replace(r"[^A-Z0-9]", "", regex=True).str.replace(r"\d+", lambda m: str(int(m.group())), regex=True)
            flag = dict(zip(qkey(q["datimcode"], q["patientartnumber"]), q["ndr_unique"].astype(str)))
            out["prev_unique"] = qkey(out["datim_code"], out["pepid"]).map(flag).fillna("No")
            print(f"  previous quarter TX_CURR from the official NDR file of {q_end}: "
                  f"{int((q['ndr_unique'].astype(str).str.lower() == 'yes').sum()):,} clients")
    out.attrs["source"] = "ndr"
    return out


def read_flat(eng, pg: dict, lc: dict) -> pd.DataFrame:
    """Read linelist_flat: the latest period, plus any earlier period not yet saved as a dated copy
    (each week is read once, then kept in data/history/sources)."""
    import re
    from sqlalchemy import text
    from etl.sources import read_pg_table
    cols = [str(c) for c in lc.values() if c != "pqs_status"]
    pc = lc.get("period")
    if not pc:
        return read_pg_table(eng, pg["schema"], pg["tables"]["linelist"], columns=cols, page=int(pg.get("page_rows", 5000)))
    with eng.connect() as con:
        periods = sorted({str(r[0])[:10] for r in con.execute(text(f'SELECT DISTINCT "{pc}" FROM "{pg["schema"]}"."{pg["tables"]["linelist"]}"')) if r[0]})
    periods = [p for p in periods if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)]
    need = [p for p in periods[:-1] if not (SOURCES / f"linelist_{p}.parquet").exists()] + periods[-1:]
    # the CDR is refreshed about once a week, DHIS2 daily: when the newest period is already saved here with
    # every column the pipeline reads, use that copy instead of re-reading it (about 5 minutes) from the CDR
    latest = SOURCES / f"linelist_{periods[-1]}.parquet" if periods else None
    if latest is not None and latest.exists() and need == periods[-1:] and "--refresh-cdr" not in sys.argv:   # --refresh-cdr: read it again
        import pyarrow.parquet as pq_
        have = {c.lower() for c in pq_.read_schema(latest).names}
        if {c.lower() for c in cols} <= have | {"ndr_unique"}:
            print(f"  linelist_flat periods: {', '.join(periods)}; newest ({periods[-1]}) unchanged since it was saved: using the saved copy")
            return legacy_linelist(pd.read_parquet(latest))
    print(f"  linelist_flat periods: {', '.join(periods)}; reading {', '.join(need)}")
    return pd.concat([read_pg_table(eng, pg["schema"], pg["tables"]["linelist"], columns=cols, where=f"\"{pc}\" = '{p}'",
                                    page=int(pg.get("page_rows", 5000)))
                      for p in need], ignore_index=True)


def split_periods(flat: pd.DataFrame, lc: dict) -> pd.DataFrame:
    """linelist_flat stacks weekly snapshots ("period"). Returns the latest snapshot (one row per
    client) and saves each earlier one as a dated copy in data/history/sources, so past periods get
    their own stock figures. State names are tidied (OSUN / Osun -> Osun)."""
    pc, idc = lc.get("period"), lc["id"]
    if lc.get("state") in flat:
        flat[lc["state"]] = flat[lc["state"]].astype(str).str.strip().str.title()
    if not pc or pc not in flat:
        return flat
    per = pd.to_datetime(flat[pc], errors="coerce").dt.date
    flat = flat.assign(**{pc: per.astype(str)})
    snaps = {d: g.drop_duplicates(idc, keep="last") for d, g in flat.groupby(per) if pd.notna(d)}
    latest = max(snaps)
    SOURCES.mkdir(parents=True, exist_ok=True)
    for d, g in snaps.items():
        f = SOURCES / f"linelist_{d.isoformat()}.parquet"
        if d != latest and not f.exists():
            _as_text(g).to_parquet(f, index=False)
    print(f"  linelist_flat: {len(snaps)} period(s) {', '.join(str(d) for d in sorted(snaps))}; using {latest} ({len(snaps[latest]):,} clients)")
    return snaps[latest].reset_index(drop=True)


def indicator_terms(cfg: dict, refresh: bool = False) -> dict:
    """{measure: [Term]} from the DHIS2 indicators in settings (dhis2.indicators): each instance's
    numerator formula, applied to that instance's reports. Cached in data/raw/cache/indicators.json."""
    import json
    from kpi.measures import Term, parse_indicator, indicator_mod
    spec = cfg["dhis2"].get("indicators") or {}
    if not spec:
        return {}
    f = ROOT / "data" / "raw" / "cache" / "indicators.json"
    cache = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    want = {(inst, uid) for m in spec.values() for inst, uid in m.items()}
    if refresh or not want <= {tuple(k.split("|")) for k in cache}:
        wk, mo = clients(cfg)
        for inst, c in (("weekly", wk), ("monthly", mo)):
            uids = [uid for (i, uid) in want if i == inst]
            if not uids:
                continue
            js = c.get("indicators", fields="id,name,numerator", filter=f"id:in:[{','.join(uids)}]", paging="false")
            de, coc = c.data_elements[cfg["dhis2"].get("dataname_field", "name")], c.category_option_combos
            for ind in js.get("indicators", []):
                terms = [[s, str(de.get(d, d)), (str(coc.get(o, o)) if o else None)] for s, d, o in parse_indicator(ind["numerator"])]
                cache[f"{inst}|{ind['id']}"] = {"name": ind["name"], "terms": terms}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    out = {}
    for meas, m in spec.items():
        out[meas] = [Term("*", d, indicator_mod(d), o, sign=s, source=inst, exact=bool(o))
                     for inst, uid in m.items() for s, d, o in cache.get(f"{inst}|{uid}", {}).get("terms", [])]
    print("  DHIS2 indicators: " + ", ".join(f"{k} ({len(v)} terms)" for k, v in out.items()))
    return out


def read_eac(cfg: dict, eng, table) -> tuple[pd.DataFrame, str]:
    """EAC line list (EAC_Script_Version11 output): the CDR table when it has rows, else an
    eac_linelist* file in the inbox. Only the mapped columns are read (never names or DOB)."""
    from sqlalchemy import text
    from etl.sources import read_pg_table
    pg, c = cfg["postgres"], cfg["columns"].get("eac", {})
    name = pg["tables"].get("eac")
    if name:
        try:
            with eng.connect() as con:
                n = con.execute(text(f'SELECT count(*) FROM "{pg["schema"]}"."{name}"')).scalar()
            if n:
                df = read_pg_table(eng, pg["schema"], name, columns=[str(v) for v in c.values()])
                print(f"  using EAC: CDR {name} ({len(df):,} clients)")
                return df, f"CDR {name}"
            print(f"  EAC: CDR {name} is empty")
        except Exception as e:                     # table not created yet
            print(f"  EAC: CDR {name} not available ({type(e).__name__})")
    hits = [f for p in cfg.get("inbox_patterns", {}).get("eac", []) for f in folder(cfg, "inbox", "inbox").glob(p)]
    if hits:
        f = max(hits, key=lambda x: x.stat().st_mtime)
        df = table(f)
        keep = [v for v in dict.fromkeys(str(v) for v in c.values()) if v in df.columns]
        print(f"  using EAC: inbox/{f.name}")
        return df[keep], f"inbox/{f.name}"
    return pd.DataFrame(), ""


def housekeeping(cfg: dict, pbs_path, as_of: dt.date) -> None:
    """After a successful live run: superseded NDR PBS exports in the inbox (client-level data)
    are kept as a dated copy in data/history/sources and removed; logs older than 90 days go."""
    import re
    inbox = folder(cfg, "inbox", "inbox")
    pats = cfg.get("inbox_patterns", {}).get("pbs_all", [])
    current = Path(pbs_path).resolve() if pbs_path else None
    for f in {x.resolve() for p in pats for x in inbox.glob(p)}:
        if f == current or (current and f.stat().st_mtime >= current.stat().st_mtime):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        d = m.group(1) if m else dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
        copy = SOURCES / f"ndr_{d}.parquet"
        if not copy.exists():
            from etl.sources import read_excel, read_manual
            df = read_manual(f) if f.suffix.lower() == ".csv" else read_excel(f)
            keep = [c for c in dict.fromkeys(str(v) for v in cfg["columns"]["pbs_all"].values()) if c in df.columns]
            SOURCES.mkdir(parents=True, exist_ok=True)
            _as_text(df[keep] if keep else df).to_parquet(copy, index=False)
        f.unlink()
        print(f"  inbox: {f.name} superseded -> kept as {copy.name}")
    logs = folder(cfg, "logs", "logs")
    cut = as_of - dt.timedelta(days=90)
    for f in logs.glob("run_*.log"):
        if dt.date.fromtimestamp(f.stat().st_mtime) < cut:
            f.unlink(missing_ok=True)


def load_sources(history_start: dt.date) -> dict:
    """Saved dated copies inside the history window: {'linelist': [(date, df)], 'ndr': [(date, df)]},
    the last copy of each month."""
    out = {}
    for kind in ("linelist", "ndr"):
        last = {}
        for f in sorted(SOURCES.glob(f"{kind}_*.parquet")):
            d = dt.date.fromisoformat(f.stem.split("_")[1])
            if d >= history_start:
                last[d.strftime("%Y-%m")] = (d, f)
        out[kind] = [(d, legacy_linelist(pd.read_parquet(f)) if kind == "linelist" else pd.read_parquet(f)) for d, f in sorted(last.values())]
    return out


def save_history(snap: pd.DataFrame) -> None:
    """Append today's snapshot (replacing an earlier one from the same day)."""
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    old = load_history()
    if len(old):
        old = old[old["date"] != snap["date"].iloc[0]]
    pd.concat([old, snap], ignore_index=True).to_csv(HISTORY, index=False)


def folder(cfg, key, default):
    p = ROOT / cfg.get("paths", {}).get(key, default)
    p.mkdir(parents=True, exist_ok=True)
    return p


def source(cfg, key, fallback):
    """Newest file in the inbox matching the key's patterns, else the configured path."""
    inbox = folder(cfg, "inbox", "inbox")
    hits = [f for pat in cfg.get("inbox_patterns", {}).get(key, []) for f in inbox.glob(pat)]
    if hits:
        f = max(hits, key=lambda x: x.stat().st_mtime)
        print(f"  using {key}: inbox/{f.name}")
        return f
    return ROOT / fallback


def load_env():
    env = ROOT / ".env"
    if env.exists():
        import os
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def settings():
    return yaml.safe_load(open(ROOT / "config" / "settings.yaml", encoding="utf-8"))


def as_of_date(cfg):
    v = cfg["reporting"]["as_of"]
    return dt.date.today() if str(v) == "auto" else pd.to_datetime(str(v)).date()


def fy_window(cfg, as_of):
    # Pull history_months before FY start so earlier quarters can be selected
    fy = pd.to_datetime(str(cfg["fy_start"])).date()
    start = (pd.Timestamp(fy) - pd.DateOffset(months=cfg["reporting"].get("history_months", 12))).date()
    return start, as_of


def finish(cfg, weekly, monthly, linelist, pbs, manual, master, as_of, demo, prev_quarter=None, past=None):
    """Merge DHIS2 periods, apply the formulas, render the dashboard. Nothing
    is stored except the dashboard itself (add --export for QA CSVs)."""
    if not demo:   # HTS_TST / HTS_TST_POS exactly as the DHIS2 indicators
        from kpi.measures import DHIS_MEASURES
        try:
            DHIS_MEASURES.update(indicator_terms(cfg, refresh="--full" in sys.argv))
        except Exception as e:
            print(f"  WARNING: DHIS2 indicator formulas unavailable ({type(e).__name__}); using the built-in definitions")
    from kpi.naomi import load_naomi
    from kpi.measures import prefer_monthly_variants
    r = cfg["reporting"]
    monthly = prefer_monthly_variants(monthly)        # TX_TB: monthly values, not the half-year totals
    merged = merge_weekly_monthly(weekly, monthly, rule=r["week_to_month_rule"], takeover=r["monthly_takeover"],
                                  keep_weeks=True)
    n_fac = master["facility"].nunique()
    ms = month_status(merged, n_fac, as_of, r["month_close_completeness"], r["month_close_day"])
    targets = load_targets(source(cfg, "targets", cfg["excel"]["targets"])).assign(fy=cfg["fiscal_year"])
    from kpi.targets import load_prior_targets
    for fy, path in (cfg.get("prior_targets") or {}).items():     # earlier years: annual targets + reported achievement
        prior = load_prior_targets(ROOT / path, fy)
        if len(prior):
            print(f"  using {fy} targets: {path}")
            targets = pd.concat([targets, prior], ignore_index=True)
    lga_lookup = dict(zip(master["lga"].astype(str), master["state"]))
    naomi = load_naomi(source(cfg, "naomi", cfg["manual"]["naomi"]), lga_lookup, cfg["manual"].get("naomi_quarter") or None)
    data, scorecard, cube = run_engine(cfg, ROOT / "config" / "indicators.yaml", merged, ms, linelist, pbs,
                                       manual, targets, master, as_of, weekly, naomi, demo=demo,
                                       history=None if demo else load_history(), prev_quarter=prev_quarter, past=past)
    snap = data.pop("_snapshot")
    if not demo and len(snap):
        save_history(snap)
    if "--export" in sys.argv:
        exp = folder(cfg, "exports", "exports")
        merged.to_csv(exp / "fact_dhis_merged.csv", index=False)
        ms.to_csv(exp / "month_status.csv", index=False)
        targets.to_csv(exp / "targets_fy27.csv", index=False)
        cube.to_csv(exp / "cube.csv", index=False)
        scorecard.to_csv(exp / "scorecard.csv", index=False)
    from dashboard.build import build
    # demo builds never touch live\ (the served dashboard) or archive\
    if not demo:          # HTS team's monthly case finding report (PowerPoint), one per month of the year
        try:
            from kpi.hts_report import find_template, write_reports
            data["reports"] = write_reports(data, folder(cfg, "live", "live") / "reports", find_template(cfg, ROOT))
            print(f"  monthly case finding reports: {len(data['reports'])}")
        except Exception as e:                      # a report problem must not stop the update
            print(f"  WARNING monthly case finding reports not made: {type(e).__name__}: {e}")
    path = (build(data, ROOT / "data" / "demo" / "out") if demo else
            build(data, folder(cfg, "live", "live"), folder(cfg, "archive", "archive"), split=True))
    if not demo and "--publish" in sys.argv:      # only the scheduled update (update_dashboard.bat) publishes; other runs stay local
        publish_git(cfg, data)
    print(f"{data['meta']['quarter']}  as of {as_of}  cube rows {len(data['cube']['rows']):,}  ->  {path}")
    return data, scorecard


def cmd_demo():
    cfg = settings()
    d = ROOT / "data" / "demo"
    if not (d / "linelist.parquet").exists():
        from scripts.make_demo_data import main as make
        make()
    as_of = dt.date(2026, 10, 10)
    master = pd.read_csv(d / "facilities.csv")
    manual = {
        "joint_ssv": pd.read_csv(d / "joint_ssv_visits.csv"),
        "dqa": pd.read_csv(d / "dqa_sites.csv"),
        "ccmp": pd.read_csv(d / "ccmp.csv"),
    }
    return finish(cfg, pd.read_parquet(d / "dhis_weekly.parquet"), pd.read_parquet(d / "dhis_monthly.parquet"),
                  pd.read_parquet(d / "linelist.parquet"), pd.read_parquet(d / "pbs_all.parquet"),
                  manual, master, as_of, demo=True)


def clients(cfg):
    from etl.dhis2_client import DHIS2
    h = cfg["dhis2"]
    return (DHIS2(h["weekly_url"], "DHIS2_WEEKLY_USER", "DHIS2_WEEKLY_PASS"),
            DHIS2(h["monthly_url"], "DHIS2_MONTHLY_USER", "DHIS2_MONTHLY_PASS"))


def cmd_discover():
    load_env()
    cfg = settings()
    raw = ROOT / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, c in zip(("weekly", "monthly"), clients(cfg)):
        ds = c.datasets()
        ds.to_csv(raw / f"dhis2_catalog_{name}_datasets.csv", index=False)
        c.data_elements.reset_index().to_csv(raw / f"dhis2_catalog_{name}_dataelements.csv", index=False)
        levels = c.get("organisationUnitLevels", fields="level,name", paging="false")
        print(f"\n[{name}] {len(ds)} datasets; org unit levels: "
              + ", ".join(f"{l['level']}={l['name']}" for l in levels["organisationUnitLevels"]))
        print(ds[["id", "name", "periodType"]].to_string(index=False))
    print(f"\nCatalogs written to {raw}. Map each table to its dataset UIDs in config/settings.yaml.")


def cmd_run():
    load_env()
    cfg = settings()
    as_of = as_of_date(cfg)
    start, end = fy_window(cfg, as_of)
    from etl.dhis2_client import extract_instance, facility_lookup
    from etl.sources import pg_engine, read_excel, read_manual, read_pg_table
    h = cfg["dhis2"]
    # Every run caches its DHIS2 pull (aggregate figures only); --cached reuses it,
    # which saves the ~40-minute download while testing changes to formulas or pages.
    cache = ROOT / "data" / "raw" / "cache"
    names = ("weekly", "monthly", "fac_w", "fac_m")
    have_cache = all((cache / f"{n}.parquet").exists() for n in names)
    cache_age = (dt.datetime.now() - dt.datetime.fromtimestamp((cache / "weekly.parquet").stat().st_mtime)) if have_cache else None
    if "--cached" in sys.argv and have_cache:
        weekly, monthly, fac_w, fac_m = (pd.read_parquet(cache / f"{n}.parquet") for n in names)
        print(f"  using cached DHIS2 pull from {dt.datetime.now() - cache_age:%Y-%m-%d %H:%M}")
    else:
        # Incremental: re-pull the last refresh_months (late reports and edits land there) and
        # keep older months from the cache. Full pull with --full, or when the cache is missing
        # or older than full_refresh_days.
        r = cfg["reporting"]
        full = "--full" in sys.argv or not have_cache or cache_age.days >= r.get("full_refresh_days", 7)
        m0 = (as_of.replace(day=1) - pd.DateOffset(months=r.get("refresh_months", 3) - 1)).date()
        cut = max(start, m0 - dt.timedelta(days=6))
        pull_from = start if full else cut
        print(f"  DHIS2 pull {'FULL' if full else 'incremental'} from {pull_from}", flush=True)
        wk, mo = clients(cfg)
        wk.checkpoint_dir = mo.checkpoint_dir = cache / "partial"   # a failed pull resumes from here
        lv = (h["level_state"], h["level_lga"], h["level_facility"])
        fac_w = facility_lookup(wk, *lv, match=h["orgunit_match"])
        fac_m = facility_lookup(mo, *lv, match=h["orgunit_match"])
        weekly = extract_instance(wk, h["tables"], "weekly", pull_from, end, fac_w, h["dataname_field"])
        from etl.dhis2_client import site_types
        community = {ou for ou, t in site_types(mo, h["level_facility"]).items() if t == "Community"}
        monthly = extract_instance(mo, h["tables"], "monthly", pull_from, end, fac_m, h["dataname_field"], community)
        if not full:
            old_w, old_m = pd.read_parquet(cache / "weekly.parquet"), pd.read_parquet(cache / "monthly.parquet")
            # Cached periods starting before pull_from + freshly pulled periods (DHIS2 returns
            # only periods lying wholly inside [pull_from, end]): disjoint, so no de-duplication.
            # (Rows are not unique on facility x dataname x disagg: sub-facilities share
            # their facility's key, so de-duplicating here would drop real data.)
            weekly = merge_cached(old_w, weekly, pull_from)
            monthly = merge_cached(old_m, monthly, pull_from)
        cache.mkdir(parents=True, exist_ok=True)
        for n, df in zip(names, (weekly, monthly, fac_w, fac_m)):
            df.to_parquet(cache / f"{n}.parquet", index=False)
        import shutil
        shutil.rmtree(cache / "partial", ignore_errors=True)   # pull complete: drop the checkpoints

    pg = cfg["postgres"]
    eng = pg_engine(pg)
    lcols = cfg["columns"]["linelist"]
    # When the CDR linelist table is unavailable (dropped / being reloaded), fall back to the
    # latest saved dated copy so scheduled runs still build; the dashboard shows a warning.
    ll_note = ""
    try:
        flat = read_flat(eng, pg, lcols)
        linelist = split_periods(flat, lcols)
    except Exception as e:
        # newest saved copy of the CDR line list itself: copies built from NDR files (history import, quarter
        # files) lack the EMR-only fields (DSD, AHD, TB screening, OTZ)
        import pyarrow.parquet as pq_
        cdr_only = lcols.get("tb_status", "current_tb_status")
        copies = [f for f in sorted(SOURCES.glob("linelist_*.parquet")) if cdr_only in pq_.read_schema(f).names]
        if not copies:
            raise
        linelist = legacy_linelist(pd.read_parquet(copies[-1]))
        linelist.attrs["from_copy"] = True
        ll_note = f"CDR linelist table unavailable ({type(e).__name__}); using the saved copy of {copies[-1].stem.split('_')[1]}"
        print(f"  WARNING: {ll_note}")
        print(f"  CDR error: {str(e).splitlines()[0][:300] if str(e) else type(e).__name__}")
    # previous-quarter status as the deck uses it: the previous_quarter_status table, by unique ID
    # (linelist_flat carries its own ARTStatus_PreviousQuarter, so the table is only read for the old layout)
    pq = None
    try:
        if lcols.get("prev_status") != "pqs_status":
            raise LookupError("not needed")
        pq = read_pg_table(eng, pg["schema"], pg["tables"]["previous_quarter_status"],
                           columns=["unique_id", "Previous ART_Status", "state", "facilityname"])
        pq = pq.rename(columns={"previous art_status": "status", "facilityname": "facility"})
        pq = pq.drop_duplicates("unique_id")
        if lcols.get("prev_status") == "pqs_status":
            linelist["pqs_status"] = linelist[lcols["id"]].map(dict(zip(pq["unique_id"], pq["status"])))
    except LookupError:
        pass
    except Exception as e:
        if "pqs_status" not in linelist:
            raise
        print(f"  WARNING: previous_quarter_status unavailable ({type(e).__name__}); using the saved copy's values")
    def table(path):
        return read_manual(path) if str(path).lower().endswith(".csv") else read_excel(path)
    pbs_path = source(cfg, "pbs_all", cfg["excel"]["pbs_all"])
    pbs = cached_table(pbs_path, table)
    # dated copies of today's sources, then the saved ones for past periods
    archive_sources(cfg, linelist, pbs, pbs_path, as_of)
    cdr_linelist = None
    if cfg["reporting"].get("treatment_source", "emr") == "ndr" and len(pbs):
        import re
        m_ = re.search(r"(\d{4}-\d{2}-\d{2})", Path(pbs_path).name)
        fdate = dt.date.fromisoformat(m_.group(1)) if m_ else dt.date.fromtimestamp(Path(pbs_path).stat().st_mtime)
        cdr_linelist, linelist = linelist, ndr_linelist(pbs, linelist, cfg, fdate)
    hist_start = (pd.Timestamp(str(cfg["fy_start"])) - pd.DateOffset(months=cfg["reporting"].get("history_months", 12))).date()
    quarter_linelist(cfg)                       # quarter-end line lists in the inbox -> dated copies
    past = load_sources(hist_start)
    m = cfg["manual"]
    eac, eac_src = read_eac(cfg, eng, table)
    wk_geo, _ = clients(cfg)
    geo = lga_geometry(cfg, wk_geo, refresh="--full" in sys.argv)
    manual = {"eac": eac, "eac_source": eac_src, "geo": geo, "linelist_note": ll_note, "cdr_linelist": cdr_linelist,
              "joint_ssv": read_manual(source(cfg, "joint_ssv", m["joint_ssv"])),
              "dqa": read_manual(source(cfg, "dqa", m["dqa"])),
              "ccmp": table(source(cfg, "ccmp", cfg["excel"]["ccmp"]))}
    from etl.dhis2_client import clean_state
    # Master facility list = both instances (the weekly one has sites the monthly one lacks)
    fac = pd.concat([fac_m, fac_w], ignore_index=True).drop_duplicates("facility_key")
    master = fac.assign(state=fac["state"].map(clean_state))[["state", "lga", "facility"]]
    if "site_type" not in fac.columns:
        from etl.dhis2_client import site_types
        wk_c, mo_c = clients(cfg)
        stypes = {**site_types(wk_c, h["level_facility"]), **site_types(mo_c, h["level_facility"])}
        master["site_type"] = fac["orgUnit"].map(stypes).fillna("Facility").values
    else:
        master["site_type"] = fac["site_type"].values
    out = finish(cfg, weekly, monthly, linelist, pbs, manual, master, as_of, demo=False, prev_quarter=pq, past=past)
    housekeeping(cfg, pbs_path, as_of)
    publish_live(cfg)
    return out


def publish_git(cfg: dict, data: dict) -> bool:
    """Push the figures-only dashboard (no client lists) to the GitHub branch publish.git_branch ('live'):
    index.html, index.html.gz, version.json and a README. The branch holds only the latest build (one
    commit, force-pushed), so the repository does not grow with every update. Other systems (e.g. a
    Docker deployment) take the dashboard from that branch. A failed push is logged; the update stands."""
    import subprocess
    pub = cfg.get("publish") or {}
    branch = pub.get("git_branch")
    if not branch:
        return False
    from dashboard.build import build, build_shared
    out = ROOT / "data" / "shared_build"
    full = bool(pub.get("client_lists"))           # private repository only: the full dashboard with client lists
    split = bool(pub.get("split"))                 # small page + data files (needs serve_dashboard.py of 2026-10-23 or later)
    (build if full else build_shared)(data, out, split=split)
    m = data["meta"]
    (out / "README.md").write_text(
        f"# SPEED KPI dashboard - latest shared build\n\nGenerated {m['generated_at']} (data as of {m['as_of']}, {m['quarter']}).\n\n"
        + ("Full dashboard, including client-level lists (ART / hospital numbers): keep this repository private and serve it\n"
           "only behind the ECEWS sign-in page (serve_dashboard.py). " if full else
           "Figures only: client-level lists (ART / hospital numbers) are removed. ")
        + "This branch holds just the latest build and is\n"
        "replaced on every update. The page is `index.html.gz` (the gzip-compressed index.html)"
        + (", and its data is in `data/<build>/*.json.gz`, fetched by the page as it needs it" if split else "")
        + ". Serve it with\n"
        "`python serve_dashboard.py --lan --port 5100 --no-browser` from `main` (ECEWS sign-in; unpacks it for any browser that\n"
        "needs it), or send it with `Content-Encoding: gzip`, or unpack it first (`gunzip -k index.html.gz`). `version.json` changes\n"
        "with each build and open pages reload when it does. The code is on the `main` branch.\n", encoding="utf-8")
    git = lambda *a, **k: subprocess.run(["git", "-C", str(ROOT), *a], check=True, capture_output=True, text=True, **k).stdout.strip()
    try:
        # the compressed page only (about 7 MB; the full index.html is over GitHub's 50 MB warning size):
        # serve_dashboard.py serves it as it is, or unpacks it for a browser that does not accept gzip
        files = {f: git("hash-object", "-w", str(out / f)) for f in ("README.md", "index.html.gz", "version.json")}
        if split:          # this build's data folder only, compressed files (the server unpacks for any browser)
            bid = max(d.name for d in (out / "data").iterdir() if d.is_dir() and not d.name.startswith("."))
            files["data"] = {bid: {f.name: git("hash-object", "-w", str(f)) for f in sorted((out / "data" / bid).glob("*.json.gz"))}}

        def mktree(entries: dict) -> str:
            lines = "".join(f"040000 tree {mktree(v)}\t{k}\n" if isinstance(v, dict) else f"100644 blob {v}\t{k}\n"
                            for k, v in sorted(entries.items()))
            # bytes, not text: on Windows, text mode would add a carriage return to every file name
            return subprocess.run(["git", "-C", str(ROOT), "mktree"], input=lines.encode("utf-8"), check=True,
                                  capture_output=True).stdout.decode().strip()
        reports = sorted((folder(cfg, "live", "live") / "reports").glob("*.pptx"))
        if reports:          # the monthly case finding reports (HIV Testing Services page)
            files["reports"] = {f.name: git("hash-object", "-w", str(f)) for f in reports}
        tree = mktree(files)
        commit = git("commit-tree", tree, "-m", f"Dashboard build {m['generated_at']} ({m['quarter']}, data as of {m['as_of']})")
        git("push", "--force", "-q", "origin", f"{commit}:refs/heads/{branch}", timeout=600)
        git("fetch", "-q", "origin", branch, timeout=300)
        git("prune", "--expire=now")          # drop the previous build's objects from the local repository
        print(f"  published: {'full dashboard (with client lists)' if full else 'figures-only dashboard'} pushed to GitHub branch '{branch}'")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  WARNING git publish failed: {(e.stderr or e.stdout or '').strip().splitlines()[-1:] or e.returncode}")
    except Exception as e:
        print(f"  WARNING git publish failed: {type(e).__name__}: {e}")
    return False


def publish_live(cfg: dict) -> bool:
    """Copy the built dashboard (live/index.html, index.html.gz, version.json) to the hosting server over
    SSH with the key in settings publish.key. Each file goes up under a temporary name and is then moved
    into place, version.json last, so open pages never load a half-copied dashboard. A failed copy is
    logged and does not fail the update (the server keeps the previous build)."""
    import subprocess
    pub = cfg.get("publish") or {}
    if not pub.get("enabled"):
        return False
    host, user, path = pub.get("host", ""), pub.get("user", ""), str(pub.get("path", "")).rstrip("/")
    key = str(Path(pub.get("key", "~/.ssh/speed_dashboard")).expanduser())
    if not (host and user and path):
        print("  publish: settings publish.host / user / path are not all set; skipped")
        return False
    live = folder(cfg, "live", "live")
    files = ["index.html.gz", "index.html", "version.json"]          # version.json last: open pages reload after it
    if not all((live / f).exists() for f in files):
        print("  publish: live/ is incomplete; skipped")
        return False
    opts = ["-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20"]
    target = f"{user}@{host}"
    try:
        # one scp per file to a temporary name, then one ssh command that moves them into place
        for f in files:
            subprocess.run(["scp", "-q", *opts, str(live / f), f"{target}:{path}/.{f}.uploading"],
                           check=True, capture_output=True, text=True, timeout=900)
        moves = " && ".join(f"mv -f '{path}/.{f}.uploading' '{path}/{f}'" for f in files)
        subprocess.run(["ssh", *opts, target, moves], check=True, capture_output=True, text=True, timeout=120)
        print(f"  publish: dashboard copied to {target}:{path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  WARNING publish failed ({' '.join(e.cmd[:1])}): {(e.stderr or e.stdout or '').strip().splitlines()[-1:] or e.returncode}")
    except Exception as e:
        print(f"  WARNING publish failed: {type(e).__name__}: {e}")
    return False


def cmd_publish():
    """python run_pipeline.py publish: push the current dashboard (figures only) to GitHub now, and copy
    live/ to the server when SSH publishing is on."""
    load_env()
    cfg = settings()
    if (cfg.get("publish") or {}).get("git_branch"):
        from dashboard.build import read_page_data
        live = folder(cfg, "live", "live")
        ok = publish_git(cfg, read_page_data((live / "index.html").read_text(encoding="utf-8"), live))
        if not (cfg.get("publish") or {}).get("enabled"):
            sys.exit(0 if ok else 1)
    if not (cfg.get("publish") or {}).get("enabled"):
        print("publish is off: set publish.enabled, user and path in config/settings.yaml once the server accepts the key")
        sys.exit(1)
    sys.exit(0 if publish_live(cfg) else 1)


def run_lock() -> None:
    """One update at a time: two runs writing the caches together corrupt them. A second run exits;
    a lock left by a run that died (its process is gone) is taken over."""
    import atexit, os
    lock = ROOT / "data" / ".run.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            os.kill(pid, 0)                                   # raises if that process no longer exists
            if sys.platform == "win32":                       # os.kill(pid, 0) can succeed for a reused pid: check it is python
                import subprocess
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout.lower()
                if "python" not in out:
                    raise OSError
            print(f"Another update is already running (process {pid}); this one stops. Started {dt.datetime.fromtimestamp(lock.stat().st_mtime):%H:%M}.")
            sys.exit(0)
        except (ValueError, OSError, SystemError):
            pass
    lock.write_text(str(os.getpid()))
    atexit.register(lambda: lock.unlink(missing_ok=True))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "run":
        run_lock()
    {"demo": cmd_demo, "run": cmd_run, "discover": cmd_discover, "publish": cmd_publish}[cmd]()
