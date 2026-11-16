"""PCS/VP (formerly CCMP) weekly bulletin: the "CCMP KPIs" sheet of the weekly bulletin workbook, one row per
state per programme week, read for the dashboard's PCS/VP page.

The workbook is the M&E team's own template (WeeklyBulletinCCMP_FY27_Week<N>.xlsx), saved in the inbox as
PCSVP_Weekly_Bulletin*.xlsx (the newest is used). Row 1 holds the section names, row 2 the column names; data
starts on row 3 with Week, Date, Quarter, State. Weeks with no figures are skipped. The bulletin's figures are
to-date values for each state (the dashboard shows the latest week in the selected period), as the bulletin
itself is read.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

STATES = ["Delta", "Ekiti", "Osun"]
SHEET = "CCMP KPIs"

# key, column name in the template (row 2), section (row 1). Matched by name; position is the fallback.
COLUMNS = [
    ("hts_comm", "HTS_TST_COMM", "Community HTS"), ("hts_pos_comm", "HTS_TST_POS_COMM", "Community HTS"),
    ("tx_linkage", "TX_LINKAGE", "Community HTS"),
    ("reach_0_17", "CALHIV Reached (0-17years)", "Beneficiaries reached"), ("reach_18_20", "CALHIV Reached (18-20years)", "Beneficiaries reached"),
    ("caregivers", "Positive Caregivers Reached", "Beneficiaries reached"),
    ("txc_0_14", "Peds & Adolescents Current on ART (0-14Years)", "Treatment & coverage"), ("cov_0_14", "CCMP Coverage (0-14years)", "Treatment & coverage"),
    ("txc_15_17", "Peds & Adolescents Current on ART (15-17Years)", "Treatment & coverage"), ("cov_15_17", "CCMP Coverage (15-17years)", "Treatment & coverage"),
    ("tx_new_0_17", "New on ART (0-17 years", "Treatment & coverage"), ("linked_ccmp", "# Linked to CCMP", "Treatment & coverage"),
    ("iit", "CALHIV Interrupted Rx", "IIT tracking"), ("rtt", "CALHIV Returned to Rx", "IIT tracking"),
    ("nut_assessed", "Nutrition Assessed", "CLHIV nutrition"), ("malnourished", "Malnourished CLHIV", "CLHIV nutrition"),
    ("nut_support", "On Nutrition Support", "CLHIV nutrition"), ("sam", "SAM_CLHIV", "CLHIV nutrition"),
    ("mam", "MAM/MOAM_CLHIV", "CLHIV nutrition"), ("obese", "Obese_CLHIV", "CLHIV nutrition"),
    ("hei_eligible", "HEI Eligible for EID (Enrolled)", "HEI & PMTCT"), ("hei_tested", "HEI eligible for EID Tested", "HEI & PMTCT"),
    ("pmtct_outcome", "PMTCT Outcome", "HEI & PMTCT"),
    ("surv_u15", "Survivors <15Yrs", "Violence & post-care"), ("pvc_u15", "Post-violence care <15Yrs", "Violence & post-care"),
    ("surv_15p", "Survivors 15+ Yrs", "Violence & post-care"), ("pvc_15p", "Post - Violence Care 15+ Yrs", "Violence & post-care"),
    ("pvc_phys", "PVC Physical/Emotional", "Violence & post-care"), ("pvc_sexual", "PVC Sexual Violence", "Violence & post-care"),
    ("pvc_total", "Total Post-Violence Care", "Violence & post-care"),
    ("surv_hiv_tst", "Survivor tested for HIV", "Survivor HTS & STI"), ("surv_hiv_pos", "Survivor tested Positive", "Survivor HTS & STI"),
    ("surv_hiv_art", "HIV+ linked for ART", "Survivor HTS & STI"), ("surv_sti_tst", "Survivor tested STI", "Survivor HTS & STI"),
    ("surv_sti_pos", "STI Positive", "Survivor HTS & STI"), ("surv_sti_rx", "STI Positive Treated", "Survivor HTS & STI"),
    ("sv_rape", "Total SV-Rape", "PEP"), ("pep_eligible", "PEP Eligible", "PEP"), ("pep_offered", "PEP Offered", "PEP"),
    ("pep_initiated", "PEP Initiated", "PEP"), ("pep_completed", "PEP Completed", "PEP"), ("post_pep_hts", "POST-PEP HTS", "PEP"),
    ("ipv_eac", "IPV - 1st EAC", "Intimate partner violence"), ("ipv_prep", "IPV - PrEP", "Intimate partner violence"),
    ("ipv_hts", "IPV - HTS", "Intimate partner violence"), ("ipv_total", "Total IPV", "Intimate partner violence"),
    ("ipv_tailored", "IPV - tailored Interventions", "Intimate partner violence"),
]


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9<+%#/]", "", str(s or "").lower())


def read_bulletin(path: Path) -> tuple[dict, list[str]]:
    """{'columns': [[key, label, section]], 'rows': [{'week', 'date', 'quarter', 'state', 'v': {key: value}}]}, issues."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"no '{SHEET}' sheet in {path.name}")
    rows = list(wb[SHEET].iter_rows(values_only=True))
    head = [_norm(c) for c in rows[1]] if len(rows) > 1 else []
    issues, pos = [], {}
    for j, (key, label, _) in enumerate(COLUMNS):
        i = head.index(_norm(label)) if _norm(label) in head else None
        if i is None:
            i = 4 + j                                    # the template's own order
            issues.append(f"column '{label}' not found by name; read from column {i + 1}")
        pos[key] = i
    out = []
    for r in rows[2:]:
        if not r or len(r) < 5:
            continue
        week, date, quarter, state = r[0], r[1], r[2], str(r[3] or "").strip().title()
        if state not in STATES or not re.match(r"^\s*week\s*\d+\s*$", str(week or ""), re.I):
            continue
        v = {}
        for key, i in pos.items():
            x = r[i] if i < len(r) else None
            if isinstance(x, (int, float)):
                v[key] = float(x)
            elif isinstance(x, str) and re.match(r"^\s*-?\d+(\.\d+)?\s*$", x):
                v[key] = float(x)
        if not v:
            continue
        d = date.date() if isinstance(date, dt.datetime) else date if isinstance(date, dt.date) else None
        out.append({"week": int(re.sub(r"\D", "", str(week))), "date": d.isoformat() if d else None,
                    "quarter": str(quarter or "").strip(), "state": state, "v": v})
    return {"columns": [list(c) for c in COLUMNS], "rows": out, "file": path.name}, issues
