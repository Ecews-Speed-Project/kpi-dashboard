"""Cross-checks that the same figure is the same everywhere on the dashboard.

    python run_pipeline.py run --cached --export      (writes exports/cube.csv)
    python scripts\\check_consistency.py

Each check compares numbers that must agree (or add up) in the current stock figures, by state and for
the programme. Prints OK / DIFF for each; exit code 1 if anything differs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    c = pd.read_csv(ROOT / "exports" / "cube.csv", dtype={"period": str}, low_memory=False)
    c["period"] = c["period"].fillna("")
    stock = c[(c["period"] == "") & (c["mod"].fillna("") == "")]
    by = stock.pivot_table(index="state", columns="measure", values="value", aggfunc="sum").fillna(0)
    by.loc["ECEWS SPEED"] = by.sum()
    g = lambda m: by[m] if m in by else pd.Series(0.0, index=by.index)
    s = lambda *ms: sum(g(m) for m in ms)

    checks = [
        # treatment: one TX_CURR
        ("TX_CURR headline = TX_CURR used for VL / MMD / DSD", g("tx_curr"), g("tx_curr_emr")),
        ("Waterfall gain/loss (should be small)", s("wf_prev", "wf_rtt", "wf_new", "wf_ti") - s("wf_to", "wf_iit", "wf_dead", "wf_stopped"), g("tx_curr")),
        ("NDR fiscal-year waterfall end = TX_CURR", g("ndr_wf_curr"), g("tx_curr")),
        ("NDR fiscal-year start = quarter waterfall start", g("ndr_wf_prev"), g("wf_prev")),
        # biometrics: categories cover every active client; unique = counted categories
        ("Biometric categories add up to active (NDR)", s("bio_u15", "bio_enrolled", "bio_dup_kept", "bio_dup_supp", "bio_aw", "bio_invalid", "bio_noprint"), g("pbs_active")),
        ("TX_CURR_Unique = under 15 + enrolled + duplicate kept", s("bio_u15", "bio_enrolled", "bio_dup_kept"), g("tx_curr")),
        ("Active in the NDR file = active in the treatment line list", g("pbs_active"), g("emr_active")),
        ("Remediation tiers 1+2 = adjudication waiting", s("tier1_aw_contact", "tier2_aw_trace"), g("bio_aw")),
        # treatment breakdowns add up to TX_CURR
        ("MMD bands add up to TX_CURR", s("mmd_lt3", "mmd_3", "mmd_45", "mmd_6"), g("tx_curr_emr")),
        ("DSD groups add up to TX_CURR", s("dsd_fac", "dsd_com", "dsd_none"), g("tx_curr_emr")),
        # EMR vs NDR comparison: both sides add up to their TX_CURR
        ("Comparison NDR side: EMR records counted twice (same client entered twice in the CDR)", g("tx_curr"), s("conc_both", "conc_ndr_emr_inactive", "conc_ndr_missing")),
        ("Comparison: in both + EMR only = EMR TX_CURR (CDR)", s("conc_both", "conc_emr_ndr_inactive", "conc_emr_ndr_noprint", "conc_emr_missing"), g("tx_curr_cdr")),
        # viral load
        ("Suppressed + unsuppressed = results", s("vl_suppressed", "vl_unsuppressed"), g("vl_result")),
        ("LLV + undetectable = suppressed", s("vl_llv", "vl_undetectable"), g("vl_suppressed")),
    ]
    bounds = [
        ("Results <= eligible <= TX_CURR", (g("vl_result") <= g("vl_eligible")) & (g("vl_eligible") <= g("tx_curr_emr"))),
        ("Pending <= samples", g("vl_pending") <= g("vl_sample")),
        ("Unique <= active", g("tx_curr") <= g("pbs_active")),
    ]
    bad = 0
    pd.set_option("display.width", 200)
    for name, a, b in checks:
        diff = (a - b).round()
        ok = (diff.abs() <= 0.5).all()
        if name.startswith(("Waterfall gain/loss", "Comparison NDR side")):
            print(f"INFO  {name}: " + ", ".join(f"{k} {int(v):+,}" for k, v in (b - a).round().items()))
            continue
        bad += not ok
        print(f"{'OK  ' if ok else 'DIFF'}  {name}" + ("" if ok else ": " + ", ".join(f"{k} {int(x):,} vs {int(y):,}" for k, x, y in zip(by.index, a, b) if round(x - y))))
    for name, m in bounds:
        ok = bool(m.all())
        bad += not ok
        print(f"{'OK  ' if ok else 'DIFF'}  {name}" + ("" if ok else ": " + ", ".join(by.index[~m])))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
