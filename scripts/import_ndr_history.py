"""Import past NDR PBS line lists (the weekly downloads) so earlier periods have NDR figures.

    python scripts\\import_ndr_history.py                 # looks in Downloads (files and .zip files)
    python scripts\\import_ndr_history.py <folder or zip> ...

Each file is dated from its name (e.g. "PBS Line List_13thJuly2026.xlsx", "PBS Linelist_6thOct2025.xlsx").
For every date inside the dashboard's history window it writes, to data/history/sources:
  ndr_<date>.parquet       the NDR columns the pipeline reads (biometrics, NDR TX_CURR)
  linelist_<date>.parquet  the same clients in the line-list layout (treatment, VL, MMD); the last file of
                           each past quarter is marked as that quarter's file, so it also gives the waterfall
and adds that date's stock figures to data/history/snapshots.csv (the weekly trend lines).
Dates that already have a copy are skipped. Nothing is changed in the source files.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import run_pipeline as rp  # noqa: E402
from etl.dhis2_client import clean_state  # noqa: E402
from etl.sources import read_excel  # noqa: E402
from kpi.engine import TREND_MEASURES, attach_master, set_aliases  # noqa: E402
from kpi.measures import STATUS, Period, linelist_cube, pbs_cube, quarter_start  # noqa: E402

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NAME = re.compile(r"pbs\s*line\s*list", re.I)
DATE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]{3,9})\s*(\d{4})")


def file_date(name: str) -> dt.date | None:
    m = DATE.search(name)
    if not m or not NAME.search(name):
        return None
    mon = MONTHS.get(m.group(2)[:3].lower())
    try:
        return dt.date(int(m.group(3)), mon, int(m.group(1))) if mon else None
    except ValueError:
        return None


def candidates(paths: list[Path]) -> dict[dt.date, tuple[Path, str | None]]:
    """{date: (file or zip, member)} - one per date, the largest file when a date appears twice."""
    found: dict[dt.date, tuple[int, Path, str | None]] = {}
    for p in paths:
        items = []
        if p.is_dir():
            items += [(f, None, f.name, f.stat().st_size) for f in p.glob("*.xlsx")]
            for z in p.glob("*.zip"):
                try:
                    with zipfile.ZipFile(z) as zz:
                        items += [(z, i.filename, Path(i.filename).name, i.file_size) for i in zz.infolist() if i.filename.lower().endswith(".xlsx")]
                except zipfile.BadZipFile:
                    continue
        elif p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as zz:
                items += [(p, i.filename, Path(i.filename).name, i.file_size) for i in zz.infolist() if i.filename.lower().endswith(".xlsx")]
        elif p.suffix.lower() == ".xlsx":
            items.append((p, None, p.name, p.stat().st_size))
        for src, member, name, size in items:
            d = file_date(name)
            if d and (d not in found or size > found[d][0]):
                found[d] = (size, src, member)
    return {d: (s, m) for d, (_, s, m) in sorted(found.items())}


def master_list() -> pd.DataFrame:
    set_aliases(rp.settings().get("facility_aliases"))       # facility names as the pipeline matches them
    cache = ROOT / "data" / "raw" / "cache"
    fac = pd.concat([pd.read_parquet(cache / "fac_m.parquet"), pd.read_parquet(cache / "fac_w.parquet")],
                    ignore_index=True).drop_duplicates("facility_key")
    m = fac.assign(state=fac["state"].map(clean_state))[["state", "lga", "facility"]]
    m["site_type"] = fac["site_type"].values if "site_type" in fac else "Facility"
    return m


def main(argv: list[str]) -> None:
    rp.load_env()
    cfg = rp.settings()
    cols = cfg["columns"]
    paths = [Path(a) for a in argv if not a.startswith("--")] or [Path.home() / "Downloads"]
    fy_start = dt.date.fromisoformat(str(cfg["fy_start"]))
    start = (pd.Timestamp(fy_start) - pd.DateOffset(months=cfg["reporting"].get("history_months", 12))).date()
    src = rp.SOURCES
    src.mkdir(parents=True, exist_ok=True)
    found = {d: v for d, v in candidates(paths).items() if start <= d < dt.date.today()}
    have_ndr = {f.stem.split("_")[1] for f in src.glob("ndr_*.parquet")}
    have_ll = {f.stem.split("_")[1] for f in src.glob("linelist_*.parquet")}
    # quarters that already have a quarter-end file (e.g. the Q4 PHIS3 line list)
    qfile_q = {quarter_start(dt.date.fromisoformat(f.stem.split("_")[1]), fy_start.month)
               for f in src.glob("linelist_*.parquet") if "quarter_file" in pd.read_parquet(f, columns=None).columns}
    # the last file of each past quarter carries that quarter's waterfall
    last_in_q: dict[dt.date, dt.date] = {}
    for d in found:
        last_in_q[quarter_start(d, fy_start.month)] = d
    cur_q = quarter_start(dt.date.today(), fy_start.month)
    qends = {d for q, d in last_in_q.items() if q != cur_q and q not in qfile_q}
    master = master_list()
    snaps = []
    print(f"{len(found)} dated NDR files from {start} on")
    for d, (path, member) in found.items():
        iso = d.isoformat()
        if iso in have_ndr and iso in have_ll and "--resnap" not in sys.argv:
            print(f"  {iso}: already saved, skipped")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            f = path
            if member:
                with zipfile.ZipFile(path) as zz:
                    f = Path(zz.extract(member, tmp))
            pbs = read_excel(f)
        keep = [c for c in dict.fromkeys(str(v) for v in cols["pbs_all"].values()) if c in pbs.columns]
        if iso not in have_ndr:
            rp._as_text(pbs[keep]).to_parquet(src / f"ndr_{iso}.parquet", index=False)
        ll = rp.ndr_linelist(pbs, pd.DataFrame(), cfg, d)
        act = int((ll["currentartstatus"] == "Active").sum())
        prev_act = int((ll["artstatus_previousquarter"] == "Active").sum())
        all_status = act < len(ll)                      # the download includes inactive clients
        # a quarter's waterfall needs inactive clients and a filled previous-quarter status
        qfile = d in qends and all_status and prev_act >= 0.8 * act
        if qfile:
            ll["quarter_file"] = f"NDR PBS line list {iso}"
        if iso not in have_ll or ("--resnap" in sys.argv and (src / f"linelist_{iso}.parquet").exists()):   # --resnap: refresh with today's rules
            rp._as_text(ll).to_parquet(src / f"linelist_{iso}.parquet", index=False)
        # that week's stock figures for the trend lines
        p = Period(as_of=d, fy_start=fy_start)
        x = pd.concat([pbs_cube(pbs, cols["pbs_all"]), linelist_cube(ll, cols["linelist"], p, None, today=d)], ignore_index=True)
        x = x[(x["period"].fillna("") == "") & (x["mod"].fillna("") == "")]
        x = pd.concat([x[x["measure"] != "ndr_tx_curr"], x[x["measure"] == "ndr_tx_curr"].assign(measure="tx_curr")])
        trend = [m for m in TREND_MEASURES if all_status or m != "iit_now"]     # active-only files have no IIT
        x = attach_master(x[x["measure"].isin(trend)], master, "ndr_history", {})
        s = x.groupby(["state", "facility", "measure"])["value"].sum().reset_index().assign(date=iso)
        snaps.append(s[["date", "state", "facility", "measure", "value"]])
        note = " - quarter file (waterfall)" if qfile else " - last of its quarter, but no waterfall (active clients only or previous status incomplete)" if d in qends else ""
        print(f"  {iso}: {len(pbs):,} clients, {act:,} active, {'active + inactive' if all_status else 'active only'}{note}")
    if snaps:
        new = pd.concat(snaps, ignore_index=True)
        old = pd.read_csv(rp.HISTORY, dtype={"date": str}) if rp.HISTORY.exists() else pd.DataFrame()
        if len(old):
            old = old[~old["date"].isin(set(new["date"]))]
        pd.concat([old, new], ignore_index=True).sort_values(["date", "state", "facility", "measure"]).to_csv(rp.HISTORY, index=False)
        print(f"trend history: {new['date'].nunique()} weeks added")


def refresh_trends() -> None:
    """Recompute the biometric trend figures (TX_CURR_Unique, categories, unusable prints) for every date in the
    trend history from the saved NDR copies (each date uses the newest copy on or before it)."""
    cfg = rp.settings()
    pc = cfg["columns"]["pbs_all"]
    copies = sorted((dt.date.fromisoformat(f.stem.split("_")[1]), f) for f in rp.SOURCES.glob("ndr_*.parquet"))
    hist = pd.read_csv(rp.HISTORY, dtype={"date": str})
    bio = [m for m in TREND_MEASURES if m.startswith(("pbs_", "bio_")) or m == "tx_curr"]
    dates = sorted(set(hist["date"]) | {d.isoformat() for d, _ in copies})
    master, cache, out = master_list(), {}, []
    for ds in dates:
        d = dt.date.fromisoformat(ds)
        use = [f for cd, f in copies if cd <= d]
        if not use:
            continue
        f = use[-1]
        if f not in cache:
            x = pbs_cube(pd.read_parquet(f), pc)
            x = x[x["period"].fillna("") == ""] if "period" in x else x
            x = pd.concat([x[x["measure"] != "ndr_tx_curr"], x[x["measure"] == "ndr_tx_curr"].assign(measure="tx_curr")])
            x = attach_master(x[x["measure"].isin(bio)], master, "ndr_history", {})
            cache[f] = x.groupby(["state", "facility", "measure"])["value"].sum().reset_index()
        out.append(cache[f].assign(date=ds))
    new = pd.concat(out, ignore_index=True)
    keep = hist[~(hist["date"].isin(set(new["date"])) & hist["measure"].isin(bio))]
    pd.concat([keep, new[["date", "state", "facility", "measure", "value"]]], ignore_index=True)       .sort_values(["date", "state", "facility", "measure"]).to_csv(rp.HISTORY, index=False)
    print(f"biometric trend refreshed for {new['date'].nunique()} dates from {len(cache)} NDR copies")


def refresh_iit() -> None:
    """Recompute the 'clients in IIT' trend (lost to follow-up, last pickup in the last 12 months) for every
    date from the saved NDR copies that list inactive clients; earlier dates (active-only downloads, or the
    CDR count used before the NDR line list) get no point, so the line uses one definition throughout."""
    cfg = rp.settings()
    lc = cfg["columns"]["linelist"]
    fy = pd.to_datetime(str(cfg["fy_start"])).date()
    hist = pd.read_csv(rp.HISTORY, dtype={"date": str})
    copies = []
    for f in sorted(rp.SOURCES.glob("ndr_*.parquet")):
        d = dt.date.fromisoformat(f.stem.split("_")[1])
        pbs = pd.read_parquet(f)
        if (pbs["currentstatus28"].astype(str).str.strip().str.lower() != "active").any():   # all-status download
            copies.append((d, pbs))
    master, out = master_list(), []
    for d, pbs in copies:
        ll = rp.ndr_linelist(pbs, pd.DataFrame(), cfg, d)
        # same rule as kpi.measures iit_now: IIT status, last pickup within 12 months of the file date
        pick = pd.to_datetime(ll["pharmacy_lastpickupdate"], errors="coerce")
        hit = ll[ll["currentartstatus"].astype(str).str.strip().str.lower().isin(STATUS["iit"])
                 & (pick > pd.Timestamp(d - dt.timedelta(days=365)))]
        x = hit.groupby(["state", "facilityname"]).size().reset_index(name="value").rename(columns={"facilityname": "facility"})
        x = x.assign(lga="", sex="U", age="Unknown", mod="", period="", measure="iit_now")
        if hit.empty:                  # e.g. a quarter-end file without the weekly statuses: no point, not a zero
            print(f"  NDR copy {d}: no IIT statuses - skipped")
            continue
        x = attach_master(x, master, "ndr_history", {}).groupby(["state", "facility", "measure"])["value"].sum().reset_index()
        later = [c for c, _ in copies if c > d]
        dates = [s for s in sorted(set(hist["date"])) if s >= d.isoformat() and (not later or s < later[0].isoformat())]
        out += [x.assign(date=s) for s in dates]
        print(f"  NDR copy {d}: {int(x['value'].sum()):,} in IIT (last pickup within 12 months), {len(dates)} trend dates")
    keep = hist[hist["measure"] != "iit_now"]
    new = pd.concat(out, ignore_index=True) if out else hist.iloc[:0]
    pd.concat([keep, new[["date", "state", "facility", "measure", "value"]]], ignore_index=True)       .sort_values(["date", "state", "facility", "measure"]).to_csv(rp.HISTORY, index=False)
    print(f"clients-in-IIT trend refreshed from {len(copies)} NDR copies")


if __name__ == "__main__":
    if "--refresh-iit" in sys.argv:
        refresh_iit()
    elif "--refresh-trends" in sys.argv:
        refresh_trends()
    else:
        main(sys.argv[1:])
