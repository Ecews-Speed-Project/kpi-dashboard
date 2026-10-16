"""Turn the FY27 targets workbook into one tidy table.

Output columns:
  target_key, scope (ECEWS SPEED | Delta | Ekiti | Osun), age_band (All | <15 | 15+),
  unit (rate | count), annual, q1, q2, q3, q4, group, note

Rows in the IPI / Non-IPI sheets are matched by indicator *name*, not row
position, so inserting rows in the workbook does not break the mapping.
"""
from __future__ import annotations

import re
import pandas as pd

STATES = ["Delta", "Ekiti", "Osun"]

# name fragment (lower-case) -> target key
IPI_MATCH = {
    "pmtct art coverage": "IPI_PMTCT_ART_COV",
    "art coverage among people who know": "IPI_ART_COV",
    "viral suppression rate among clients on art": "IPI_VLS_ON_ART",
    "sustainability/transition": "IPI_JOINT_SSV",
}
NON_IPI_MATCH = {
    "newly enrolled on prep": "NIPI_PREP_AMONG_NEG",
    "knowledge of hiv status": "NIPI_KNOW_STATUS",
    "new hiv diagnoses among infants": "NIPI_INFANT_DX",
    "case finding": "NIPI_CASE_FINDING",
    "spectrum estimate": "NIPI_POP_VLS",
    "tx_curr": "NIPI_TX_CURR",
    "viral suppression rate among plhiv on art": "NIPI_VLS_WITH_RESULT",
    "dqa coverage": "NIPI_DQA_COVERAGE",
}
# Conventional sheet label -> target key
CONV_MATCH = {
    "TX_CURR": "TX_CURR", "TX_PVLS_D": "TX_PVLS_D", "TX_PVLS_N": "TX_PVLS_N",
    "TX_NEW": "TX_NEW", "HTS_TST_POS": "HTS_TST_POS", "HTS_TST": "HTS_TST",
    "PMTCT_STAT (N)": "PMTCT_STAT", "PMTCT_ART": "PMTCT_ART", "PrEP_NEW": "PrEP_NEW",
    "TB_ART (N)": "TB_ART", "TB_PREV (N)": "TB_PREV", "TX_TB (N)": "TX_TB",
    "POST _RESP": "POST_RESP", "POST_RESP": "POST_RESP",
}


def _pct(v):
    """0.98 | '98%' | 'Q1-98%' -> 0.98 ; '6 completed…' -> 6.0 ; else None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    if m:
        return float(m.group(1)) / 100
    m = re.search(r"-\s*(\d+(?:\.\d+)?)\s*$", s.strip())
    if m:
        return float(m.group(1))
    m = re.match(r"\s*(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _milestones(cells):
    out = {}
    for c in cells:
        if c is None or (isinstance(c, float) and pd.isna(c)):
            continue
        m = re.match(r"\s*Q([1-4])\s*-\s*(.+)$", str(c).strip())
        if m:
            out[f"q{m.group(1)}"] = _pct(m.group(2))
    return out


PRIOR_MATCH = {"HTS_POS": "HTS_TST_POS", "PMTCT_STAT (N)": "PMTCT_STAT", "TB_PREV (N)": "TB_PREV", "TX_TB (N)": "TX_TB"}


def load_prior_targets(path, fy: str) -> pd.DataFrame:
    """A past year's annual targets + Power BI achievement by state (no quarterly split),
    e.g. PBI_FY25_TARGET.csv used for FY26: State, Indicators, Target, Achievement."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    d = pd.read_csv(p)
    d.columns = [str(c).strip().lower() for c in d.columns]
    d["indicators"] = d["indicators"].astype(str).str.strip()
    d["target_key"] = d["indicators"].map(lambda s: PRIOR_MATCH.get(s, s))
    d["scope"] = d["state"].map(lambda s: next((x for x in STATES if x.lower() in str(s).lower()), s))
    d = d.groupby(["target_key", "scope"], as_index=False)[["target", "achievement"]].sum()
    tot = d.groupby("target_key", as_index=False)[["target", "achievement"]].sum().assign(scope="ECEWS SPEED")
    d = pd.concat([d, tot], ignore_index=True)
    return pd.DataFrame({"target_key": d["target_key"], "scope": d["scope"], "age_band": "All", "unit": "count",
                         "annual": d["target"].astype(float), "q1": None, "q2": None, "q3": None, "q4": None,
                         "group": "Conventional", "note": f"{fy} annual target (Power BI); no quarterly split",
                         "reported": d["achievement"].astype(float), "fy": fy})


def _sheet(xl, name_part):
    for s in xl.sheet_names:
        if name_part.lower() in s.lower():
            return pd.read_excel(xl, s, header=None)
    raise KeyError(f"No sheet containing '{name_part}'")


def _match(text, table):
    t = str(text).lower()
    for frag, key in table.items():
        if frag in t:
            return key
    return None


def load_targets(path: str, case_finding_source: str = "conventional") -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    rows = []

    # ---- IPI / Non-IPI (programme-wide rates or counts) -------------------
    for sheet, table, group in [("Q1_IPI", IPI_MATCH, "IPI"), ("Non_IPI", NON_IPI_MATCH, "Non-IPI")]:
        df = _sheet(xl, sheet)
        for _, r in df.iloc[2:].iterrows():
            name = r[2] if pd.notna(r[2]) else r[0]
            if pd.isna(name):
                continue
            key = _match(f"{r[2]} {r[0]}", table)
            if not key:
                continue
            raw = r[8]
            annual = _pct(raw)
            unit = "rate" if (isinstance(raw, float) and raw <= 1) or "%" in str(raw) else "count"
            ms = _milestones([r[10], r[11], r[12], r[13]])
            note = "" if isinstance(raw, (int, float)) else str(raw)
            rows.append(dict(target_key=key, scope="ECEWS SPEED", age_band="All", unit=unit,
                             annual=annual, group=group, note=note, **ms))

    # ---- Conventional (by state, by age band) -----------------------------
    conv = _sheet(xl, "Conventional")
    current = None
    for _, r in conv.iloc[1:].iterrows():
        label = str(r[0]).strip() if pd.notna(r[0]) else ""
        if label in CONV_MATCH:
            current = CONV_MATCH[label]
            band = "All"
        elif label in ("<15", "15+") and current:
            band = label
        else:
            continue
        vals = {}
        for i, st in enumerate(STATES, start=1):
            v = r[i]
            vals[st] = float(v) if isinstance(v, (int, float)) and not pd.isna(v) else None
        rows.extend(dict(target_key=current, scope=st, age_band=band, unit="count",
                         annual=v, group="Conventional", note="") for st, v in vals.items())
    t = pd.DataFrame(rows)

    # Header rows whose state cells are formulas (=B3+B4) come through blank:
    # rebuild "All" from the age bands, then SPEED totals from the states.
    conv_t = t[t.group == "Conventional"]
    bands = conv_t[conv_t.age_band != "All"].groupby(["target_key", "scope"], as_index=False)["annual"].sum()
    for _, b in bands.iterrows():
        m = (t.target_key == b.target_key) & (t.scope == b.scope) & (t.age_band == "All")
        if t.loc[m, "annual"].isna().all():
            t.loc[m, "annual"] = b.annual
    t.loc[(t.group == "Conventional"), "annual"] = t.loc[(t.group == "Conventional"), "annual"].round(0)
    speed = (t[t.group == "Conventional"].groupby(["target_key", "age_band"], as_index=False)["annual"].sum()
             .assign(scope="ECEWS SPEED", unit="count", group="Conventional", note=""))
    t = pd.concat([t, speed], ignore_index=True)

    # ---- Derivations & decisions ------------------------------------------
    def setv(key, scope, **kw):
        m = (t.target_key == key) & (t.scope == scope) & (t.age_band == "All")
        for k, v in kw.items():
            t.loc[m, k] = v

    # Rate targets apply equally to each state
    rate_rows = t[(t.group.isin(["IPI", "Non-IPI"])) & (t.scope == "ECEWS SPEED")]
    extra = []
    for _, r in rate_rows.iterrows():
        for st in STATES:
            rr = r.copy(); rr["scope"] = st
            if r.target_key == "IPI_JOINT_SSV":
                rr["annual"] = 2.0  # "2 per state"
                for q in ("q1", "q2", "q3", "q4"):
                    rr[q] = None
            extra.append(rr)
    t = pd.concat([t, pd.DataFrame(extra)], ignore_index=True)

    # PrEP: "10% of HTS_TST_NEG" ; DQA: 10% of facilities per state per quarter
    for sc in ["ECEWS SPEED", *STATES]:
        setv("NIPI_PREP_AMONG_NEG", sc, annual=0.10, unit="rate")
        setv("NIPI_DQA_COVERAGE", sc, annual=0.40, unit="rate", q1=0.10, q2=0.20, q3=0.30, q4=0.40)
        # VLS among those with a result: no target in the IPI sheet -> use the
        # conventional assumption (95% of results suppressed)
        setv("NIPI_VLS_WITH_RESULT", sc, annual=0.95, unit="rate",
             note="No target in sheet; uses conventional assumption (95% of results suppressed)")

    # Case finding: Non-IPI sheet says 4,545, conventional HTS_TST_POS sums to 6,423
    conv_pos = t[(t.target_key == "HTS_TST_POS") & (t.age_band == "All")].set_index("scope")["annual"]
    for sc in ["ECEWS SPEED", *STATES]:
        if case_finding_source == "conventional":
            setv("NIPI_CASE_FINDING", sc, annual=conv_pos.get(sc), unit="count",
                 note="Using conventional HTS_TST_POS; Non-IPI sheet states 4,545")
        elif sc != "ECEWS SPEED":
            share = conv_pos.get(sc) / conv_pos.get("ECEWS SPEED")
            setv("NIPI_CASE_FINDING", sc, annual=round(4545 * share))
        conv_curr = t[(t.target_key == "TX_CURR") & (t.age_band == "All") & (t.scope == sc)]["annual"]
        setv("NIPI_TX_CURR", sc, annual=float(conv_curr.iloc[0]), unit="count")
        # Infant diagnoses: split 49 by PMTCT_ART share
        if sc != "ECEWS SPEED":
            pa = t[(t.target_key == "PMTCT_ART") & (t.age_band == "All")].set_index("scope")["annual"]
            setv("NIPI_INFANT_DX", sc, annual=round(49 * pa[sc] / pa["ECEWS SPEED"]), unit="count")

    for q in ("q1", "q2", "q3", "q4"):
        if q not in t:
            t[q] = None
    cols = ["target_key", "scope", "age_band", "unit", "annual", "q1", "q2", "q3", "q4", "group", "note"]
    return t[cols].sort_values(["group", "target_key", "scope", "age_band"]).reset_index(drop=True)


if __name__ == "__main__":
    import sys
    pd.set_option("display.width", 200, "display.max_rows", 200)
    print(load_targets(sys.argv[1] if len(sys.argv) > 1 else "data/excel/FY27_Targets.xlsx").to_string())
