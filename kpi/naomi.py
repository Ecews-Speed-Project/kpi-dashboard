"""Load Naomi (UNAIDS) PLHIV estimates.

Handles three shapes:
  1. ECEWS summary (the current file): one row per state x sex, age groups as
     columns — area_name | age_group (holds both/female/male) | less_than_15 |
     fifteen_plus | all_ages
  2. Standard Naomi long export: area_name, sex, age_group (Y015_019 …),
     calendar_quarter, indicator (plhiv, art_coverage …), mean
  3. A plain table: state, [lga], [sex], [age], plhiv

Returns tidy rows: state, lga, sex (F/M/U), age, plhiv
where age is "All", "<15", "15+" or a five-year band ("0-4" … "65+").
The finest level present is kept and coarser duplicates are dropped, so a
total is never counted twice.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

STATES = ["Delta", "Ekiti", "Osun"]
AGE5 = ["0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39", "40-44",
        "45-49", "50-54", "55-59", "60-64", "65+"]
COLS = ["state", "lga", "sex", "age", "plhiv"]

AGE_COLUMNS = {  # wide age columns -> label
    "less_than_15": "<15", "lt15": "<15", "under_15": "<15", "<15": "<15", "0-14": "<15",
    "fifteen_plus": "15+", "15_plus": "15+", "15+": "15+", "adults": "15+",
    "all_ages": "All", "total": "All", "all": "All",
}


def _age(v) -> str | None:
    s = str(v).strip()
    if s in AGE_COLUMNS:
        return AGE_COLUMNS[s]
    m = re.match(r"Y(\d{3})_(\d{3})$", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r"(\d+)\s*[-–]\s*(\d+)", s)
        if not m:
            if re.match(r"\s*15\s*\+", s):
                return "15+"
            if re.match(r"\s*<\s*15", s):
                return "<15"
            return "All" if s.lower() in ("all", "total", "all ages", "") else None
        lo, hi = int(m.group(1)), int(m.group(2))
    if (lo, hi) == (0, 14):
        return "<15"
    if lo == 15 and hi >= 99:
        return "15+"
    if lo == 0 and hi >= 99:
        return "All"
    if lo >= 65:
        return "65+"
    if hi - lo == 4 and lo % 5 == 0:
        return f"{lo}-{hi}"
    return None   # other coarse groups (15-49 …) are skipped


def _sex(v) -> str | None:
    s = str(v).strip().lower()
    if s.startswith("f"):
        return "F"
    if s.startswith("m"):
        return "M"
    if s in ("both", "all", "total", "", "nan", "none"):
        return "U"
    return None


def _state(name) -> str | None:
    for st in STATES:
        if st.lower() in str(name).lower():
            return st
    return None


def _finest(df: pd.DataFrame) -> pd.DataFrame:
    if (df["lga"] != "").any():
        df = df[df["lga"] != ""]
    if df["sex"].isin(["F", "M"]).any():
        df = df[df["sex"].isin(["F", "M"])]
    ages = set(df["age"])
    if ages & set(AGE5):
        df = df[df["age"].isin(AGE5)]
    elif {"<15", "15+"} <= ages:
        df = df[df["age"].isin(["<15", "15+"])]
    return df


def load_naomi(path: str | Path, lga_lookup: dict[str, str] | None = None,
               quarter: str | None = None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(p) if p.suffix.lower() == ".csv" else pd.read_excel(p)
    df.columns = [str(c).strip().lower() for c in df.columns]
    lga_lookup = {k.lower(): v for k, v in (lga_lookup or {}).items()}
    area_col = next((c for c in ("area_name", "state", "area", "lga") if c in df), df.columns[0])

    def place(a):
        if str(a).lower() in lga_lookup:
            return lga_lookup[str(a).lower()], str(a)
        return _state(a), ""

    # which column holds sex? (the ECEWS file keeps it under "age_group")
    sex_col = next((c for c in df.columns if df[c].astype(str).str.lower().isin(["both", "female", "male"]).mean() > 0.6), None)
    age_cols = [c for c in df.columns if c in AGE_COLUMNS]

    if age_cols:                                         # shape 1 (wide ages)
        long = df.melt(id_vars=[area_col] + ([sex_col] if sex_col else []), value_vars=age_cols,
                       var_name="agecol", value_name="plhiv")
        long["age"] = long["agecol"].map(AGE_COLUMNS)
        long["sex"] = long[sex_col].map(_sex) if sex_col else "U"
        long["area"] = long[area_col]
    elif {"indicator", "mean"} <= set(df.columns):      # shape 2 (Naomi long)
        if "calendar_quarter" in df and df["calendar_quarter"].notna().any():
            q = quarter or sorted(df["calendar_quarter"].dropna().astype(str).unique())[-1]
            df = df[df["calendar_quarter"].astype(str) == q]
        long = df[df["indicator"].astype(str).str.lower() == "plhiv"].rename(columns={"mean": "plhiv"})
        long["age"] = long["age_group"].map(_age) if "age_group" in long else "All"
        long["sex"] = long["sex"].map(_sex) if "sex" in long else "U"
        long["area"] = long[area_col]
    else:                                                # shape 3 (plain)
        val = next(c for c in df.columns if c in ("plhiv", "plhiv_estimate", "estimate", "mean", "plhiv_est"))
        long = df.rename(columns={val: "plhiv"})
        agec = next((c for c in df.columns if c in ("age", "age_band", "age_group")), None)
        long["age"] = long[agec].map(_age) if agec else "All"
        long["sex"] = long[sex_col].map(_sex) if sex_col else "U"
        long["area"] = long.get("lga", long[area_col])

    pl = long["area"].map(place)
    long["state"] = [x[0] for x in pl]
    long["lga"] = [x[1] for x in pl]
    long["plhiv"] = pd.to_numeric(long["plhiv"], errors="coerce")
    long = long[long["state"].notna() & long["sex"].notna() & long["age"].notna() & long["plhiv"].notna()]
    return _finest(long)[COLS].reset_index(drop=True)


if __name__ == "__main__":
    import sys
    t = load_naomi(sys.argv[1])
    print(t.to_string())
    print("total PLHIV:", round(t["plhiv"].sum()))
