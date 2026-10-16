"""Base measures — Python ports of the Power BI DAX measures, produced as a
long, drillable cube:

    facility · sex · age_band · modality · month · measure · value

Every value is an ADDITIVE count. Rates are formed after aggregation from
numerator/denominator, so they are right at every level (facility, LGA,
state, ECEWS SPEED) and for every sex / age-band slice, the way DHIS2
pivot tables work. Snapshot measures from the linelist carry month "".

Hard-coded DAX dates (DATE(2026,07,01), quarter = "Q3", VL window
2025-09-26..2026-09-26) are replaced by a Period derived from as_of.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

LEVEL = ["state", "lga", "facility"]
DIMS = ["state", "lga", "facility", "sex", "age", "mod", "period"]
# period keys: "YYYY-MM" monthly report · "YYYYWnn" weekly report ·
# "YYYY-MM-DD" a dated linelist event (TX_NEW, IIT) · "" point-in-time stock

AGE_BANDS = ["<1", "1-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39",
             "40-44", "45-49", "50-54", "55-59", "60-64", "65+", "50+"]  # 50+ = DHIS2 HTS top band
UNKNOWN_AGE = "Unknown"

# HTS modalities as they appear in DHIS2 data element names (the modality is a
# suffix: HTS_TST_VCT, HTS_TST_POS_MOBILE). Sex and age come from the category
# option combo ("Female, 20-24 years"). Order matters: first match wins.
MODALITIES = [
    ("Post ANC1", r"post\s*anc"), ("ANC1", r"\banc\s*1\b|\banc1\b|\banc\b"), ("Index", r"index"),
    ("KP", r"\bkp\b"), ("VCT", r"\bvct\b"), ("TB Clinic", r"\btb\b"), ("Emergency", r"emergency"),
    ("Inpatient", r"inpatient"), ("Mobile", r"mobile"), ("Paediatrics", r"paed|pediatric"),
    ("STI", r"\bsti\b"), ("SNS", r"\bsns\b"), ("Malnutrition", r"malnutrition"),
    ("Other PITC", r"other\s*pitc|\bpitc\b"),
]


def modality_of(text: str) -> str:
    t = str(text or "").lower().replace("_", " ")
    for name, pat in MODALITIES:
        if re.search(pat, t):
            return name
    return ""


@dataclass(frozen=True)
class Term:
    """One DHIS2 data element feeding a measure. coc: regex the category option
    combo must match; sign -1 subtracts (HTS_TB = TB_STAT_NUM - previously known)."""
    table: str
    dataname: str
    mod: str = ""
    coc: str | None = None
    sign: int = 1
    source: str | None = None     # "monthly" / "weekly": only that instance's reports (DHIS2 indicator terms)
    exact: bool = False           # coc is the exact category option combo name, not a regex


def _modality_terms(table: str, base: str, suffixes: list[str]) -> list[Term]:
    return [Term(table, f"{base}_{s}", modality_of(s)) for s in suffixes]


_HTS_MODS = ["EMERGENCY", "INPATIENT", "MALNUTRITION", "OTHER PITC", "PAEDIATRICS", "SNS", "STI", "VCT",
             "MOBILE", "PMTCT (POST ANC1: Breastfeeding)", "PMTCT (POST ANC1: Labour & Delivery)",
             "PMTCT (POST ANC1: Pregnancy)"]
_KP = ["KP-FSW", "KP-MSM", "KP-PPOCS", "KP-PWID", "KP-TG"]
_INDEX_RESULT = ["HTS_INDEX_CONTACTS TEST RESULT( Includes Previously Known)",
                 "HTS_INDEXMOD_CONTACTS TEST RESULT( Includes Previously Known)"]
_NEW_POS, _NEW_NEG = r"\bnew positives?\b", r"\bnew negatives?\b"
_PMTCT_NEW = "PMTCT_STAT_NUM (Newly Tested)"

# DHIS2 measure -> Terms. Mirrors the Power BI DAX (config/expo_python_formulas.xlsx):
# TOTAL HTS_TST, TOTAL HTS_TST_POS, PMTCT_STAT_D/N/POS, nTB Presumptive, ...
# Community PMTCT is added into HTS_TST (ANC) and PMTCT_*, as in the DAX and the
# deck's PMTCT slide (decided 2026-10-11); the *_comm measures hold the community
# part on its own for the facility / community table.
DHIS_MEASURES = {
    "hts_tst": _modality_terms("HTS_TST", "HTS_TST", _HTS_MODS)
        + _modality_terms("INDEX", "HTS_TST", _KP)
        + [Term("TB", "TB_STAT_NUM", "TB Clinic"),                               # HTS_TB
           Term("TB", "TB_STAT_POS_Previously known positive", "TB Clinic", sign=-1),
           Term("PMTCT", _PMTCT_NEW, "ANC1"),                                   # ANC
           Term("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New neg", "ANC1"),
           Term("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New pos", "ANC1")]
        + [Term("INDEX", e, "Index", c) for e in _INDEX_RESULT for c in (_NEW_POS, _NEW_NEG)],
    "hts_tst_pos": _modality_terms("HTS_TST", "HTS_TST_POS", _HTS_MODS)
        + _modality_terms("INDEX", "HTS_TST_POS", _KP)
        + [Term("TB", "TB_STAT_POS_New positive", "TB Clinic"),
           Term("PMTCT", _PMTCT_NEW, "ANC1", r"newly tested positive"),
           Term("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New pos", "ANC1")]
        + [Term("INDEX", e, "Index", _NEW_POS) for e in _INDEX_RESULT],
    "hts_tst_comm":     [Term("PMTCT_COMM", f"Community_PMTCT_STAT_NUM_{s}", "ANC1") for s in ("New neg", "New pos")],
    # community testing streams, as the Q3 deck splits facility vs community: the community HTS
    # dataset (HZLQXHypCbz) holds only the Mobile elements; CSCS index = HTS_INDEXMOD_*; cPMTCT above
    "hts_tst_mob":   [Term("HTS_TST", "HTS_TST_MOBILE", modality_of("MOBILE"))],
    "hts_pos_mob":   [Term("HTS_TST", "HTS_TST_POS_MOBILE", modality_of("MOBILE"))],
    "hts_tst_cidx":  [Term("INDEX", _INDEX_RESULT[1], "Index", c) for c in (_NEW_POS, _NEW_NEG)],
    "hts_pos_cidx":  [Term("INDEX", _INDEX_RESULT[1], "Index", _NEW_POS)],
    "hts_tst_pos_comm": [Term("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New pos", "ANC1")],
    "prep_new":       [Term("PREP", "PrEP_NEW")],
    "prep_offered":   [Term("PREP", "PrEP_Offered")],
    "prep_accepted":  [Term("PREP", "PrEP_Accepted")],
    "prep_ct":        [Term("PREP", "PrEP_CT")],
    "prep_new_preg":  [Term("PREP", "PrEP_NEW_Preg/BF", coc=r"pregnant")],
    "prep_new_bf":    [Term("PREP", "PrEP_NEW_Preg/BF", coc=r"breastfeeding")],
    "prep_ct_preg":   [Term("PREP", "PrEP_CT_Preg/BF", coc=r"pregnant")],
    "prep_ct_bf":     [Term("PREP", "PrEP_CT_Preg/BF", coc=r"breastfeeding")],
    "pmtct_pos_known": [Term("PMTCT", "PMTCT_STAT_NUM_Prev known", "Facility"),
                        Term("PMTCT_COMM", "Community_PMTCT_STAT_NUM_Prev known", "Community")],
    "pmtct_pos_new":   [Term("PMTCT", "PMTCT_STAT_NUM (Newly Tested)", "Facility", r"newly tested positive"),
                        Term("PMTCT_COMM", "Community_PMTCT_STAT_NUM_New pos", "Community")],
    "index_tst":      [Term("INDEX", e, "Index", c) for e in _INDEX_RESULT for c in (_NEW_POS, _NEW_NEG)],
    "index_pos":      [Term("INDEX", e, "Index", _NEW_POS) for e in _INDEX_RESULT],
    "hts_self":       [Term("HIVST", "HTS_SELF_ASSISTED"), Term("HIVST", "HTS_SELF_UNASSISTED")],
    "pmtct_stat_den": [Term("PMTCT", "PMTCT_STAT_DEN", "Facility"), Term("PMTCT_COMM", "Community_PMTCT_STAT_DEN", "Community")],
    "pmtct_stat_num": [Term("PMTCT", _PMTCT_NEW, "Facility"), Term("PMTCT", "PMTCT_STAT_NUM_Prev known", "Facility")]
                      + [Term("PMTCT_COMM", f"Community_PMTCT_STAT_NUM_{s}", "Community") for s in ("New neg", "New pos", "Prev known")],
    "pmtct_stat_pos": [Term("PMTCT", _PMTCT_NEW, "Facility", r"newly tested positive"),
                       Term("PMTCT", "PMTCT_STAT_NUM_Prev known", "Facility")]
                      + [Term("PMTCT_COMM", f"Community_PMTCT_STAT_NUM_{s}", "Community") for s in ("New pos", "Prev known")],
    "pmtct_art":      [Term("PMTCT", "PMTCT_ART", "Facility"), Term("PMTCT_COMM", "Community_PMTCT_ART_Already_on_ART", "Community"),
                       Term("PMTCT_COMM", "Community_PMTCT_ART_New_on_ART", "Community")],
    "pmtct_stat_den_comm": [Term("PMTCT_COMM", "Community_PMTCT_STAT_DEN", "Community")],
    "pmtct_stat_num_comm": [Term("PMTCT_COMM", f"Community_PMTCT_STAT_NUM_{s}", "Community")
                            for s in ("New neg", "New pos", "Prev known")],
    "pmtct_stat_pos_comm": [Term("PMTCT_COMM", f"Community_PMTCT_STAT_NUM_{s}", "Community")
                            for s in ("New pos", "Prev known")],
    "pmtct_art_comm":      [Term("PMTCT_COMM", "Community_PMTCT_ART_Already_on_ART", "Community"),
                            Term("PMTCT_COMM", "Community_PMTCT_ART_New_on_ART", "Community")],
    # weekly instance: PMTCT_EID; monthly instance splits it into first / second test
    "pmtct_eid":      [Term("PMTCT_EID", n) for n in ("PMTCT_EID", "PMTCT_EID_First Test")],   # first test (deck / DATIM)
    "pmtct_eid_pos":  [Term("PMTCT_EID", "PMTCT_HEI_POS")],
    "tb_prev_num":    [Term("TB", f"TB_PREV_NUM_{s} enrolled on ART") for s in ("Newly", "Previously")],
    "tb_prev_den":    [Term("TB", f"TB_PREV_DEN_{s} enrolled on ART") for s in ("Newly", "Previously")],
    "tb_art":         [Term("TB", "TB_ART_Already on ART"), Term("TB", "TB_ART_New on ART")],
    "tx_tb_den":      [Term("TX_TB", "TX_TB_DEN")],
    "tx_tb_num":      [Term("TX_TB", "TX_TB_NUM_Already on ART"), Term("TX_TB", "TX_TB_NUM_Newly started on ART")],
    "tb_presumptive": [Term("TX_TB", "TX_TB_POS_New on ART"), Term("TX_TB", "TX_TB_POS_Previously on ART")],
    "tb_specimen":    [Term("TX_TB", "TX_TB_Specimen sent")],
    "tb_pos_result":  [Term("TX_TB", "TX_TB_Pos result")],
    "ahd_diagnosed":  [Term("AHD", "AHD_MSF-Confirmed AHD _New")],
    # index testing cascade (facility HTS_INDEX + community CSCS/CSCC HTS_INDEXMOD)
    "index_cases":    [Term("INDEX", f"HTS_{x}_CASES") for x in ("INDEX", "INDEXMOD")],
    "index_offered":  [Term("INDEX", f"HTS_{x}_OFFERED") for x in ("INDEX", "INDEXMOD")],
    "index_accepted": [Term("INDEX", f"HTS_{x}_ACCEPTED") for x in ("INDEX", "INDEXMOD")],
    "index_elicited": [Term("INDEX", f"HTS_{x}_CONTACTS ELICITED") for x in ("INDEX", "INDEXMOD")],
    "index_reached":  [Term("INDEX", f"HTS_{x}_CONTACTS REACHED") for x in ("INDEX", "INDEXMOD")],
    "index_known_pos": [Term("INDEX", e, "Index", r"known positive") for e in _INDEX_RESULT],
    # HIV self-testing: kits distributed, results, confirmation and linkage
    "hivst_assisted":   [Term("HIVST", "HTS_SELF_ASSISTED")],
    "hivst_unassisted": [Term("HIVST", "HTS_SELF_UNASSISTED")],
    "hivst_results":    [Term("HIVST", f"HTS_SELF_{x}_TEST RESULT") for x in ("ASSISTED", "UNASSISTED")],
    "hivst_reactive":   [Term("HIVST", f"HTS_SELF_{x}_TEST RESULT", "", r"(?:^|,\s*)reactive") for x in ("ASSISTED", "UNASSISTED")],
    "hivst_referred":   [Term("HIVST", f"HTS_SELF_{x}_CONFIRMATORY TEST", "", r"referred") for x in ("ASSISTED", "UNASSISTED")],
    "hivst_confirmed":  [Term("HIVST", f"HTS_SELF_{x}_CONFIRMATORY TEST", "", r"confirmed") for x in ("ASSISTED", "UNASSISTED")],
    "hivst_linked":     [Term("HIVST", f"HTS_SELF_{x}_CONFIRMATORY TEST", "", r"linked") for x in ("ASSISTED", "UNASSISTED")],
    # TB/HIV (TB_STAT) and TB prevention (TPT) flows
    "tpt_eligible":   [Term("TB", "TPT_Eligible")],
    "tpt_started":    [Term("TB", "TPT_Started")],
    "tb_stat_den":    [Term("TB", "TB_STAT_DEN")],
    "tb_stat_num":    [Term("TB", "TB_STAT_NUM")],
    "tb_stat_pos":    [Term("TB", "TB_STAT_POS_New positive"), Term("TB", "TB_STAT_POS_Previously known positive")],
    # HIV-exposed infants (facility PMTCT): EID and HEI by infant age at test
    "hei_delivered":  [Term("PMTCT_EID", "HEI Delivered_Alive")],
    "hei_nvp":        [Term("PMTCT_EID", "HEI_NVP")],
    "pmtct_eid_lt2":  [Term("PMTCT_EID", n, "", r"<\s*2 months") for n in ("PMTCT_EID", "PMTCT_EID_First Test")],
    "pmtct_eid_2_12": [Term("PMTCT_EID", n, "", r"2\s*-\s*12 months") for n in ("PMTCT_EID", "PMTCT_EID_First Test")],
    "pmtct_eid_repeat": [Term("PMTCT_EID", "PMTCT_EID_Second Test or more")],
    "hei_neg":        [Term("PMTCT_EID", "PMTCT_HEI_NEG")],
    "hei_pos_lt2":    [Term("PMTCT_EID", "PMTCT_HEI_POS", "", r"<\s*2 months")],
    "hei_pos_2_12":   [Term("PMTCT_EID", "PMTCT_HEI_POS", "", r"2\s*-\s*12 months")],
    "hei_art":        [Term("PMTCT_EID", "PMTCT_HEI_POS on ART")],
    "hei_art_lt2":    [Term("PMTCT_EID", "PMTCT_HEI_POS on ART", "", r"<\s*2 months")],
    "hei_art_2_12":   [Term("PMTCT_EID", "PMTCT_HEI_POS on ART", "", r"2\s*-\s*12 months")],
}

# Advanced HIV disease cascade (AHD_MSF elements in HIV Care and Treatment), for clients
# newly enrolled ("_n") and clients already on ART ("_a"). None = no such element.
_AHD = "AHD_MSF-"
_AHD_STEPS = {
    "enrolled":   ("CARE_NEW", None),
    "cd4":        ("AHD_MSF-PLHIV with CD4 test_New", "AHD_MSF-PLHIV with CD4 test _Already on ART"),
    "cd4lt200":   ("AHD_MSF-CD4 < 200_New", "AHD_MSF-CD4 < 200_Already on ART"),
    "who34":      ("AHD_MSF- WHO 3/4 _CD4 ≥ 200 or No CD4_New", "AHD_MSF- WHO 3/4 _CD4 ≥ 200 or No CD4_Already On ART"),
    "confirmed":  ("AHD_MSF-Confirmed AHD _New", "AHD_MSF-Confirmed AHD _Already On ART"),
    "lam_test":   ("AHD_MSF-CD4 <200 _WHO 3/4 + CD4 >200_ TB Urine LAM test_New",
                   "AHD_MSF-CD4 <200_WHO 3/4 + CD4 >200_ TB Urine LAM test _Already On ART"),
    "lam_pos":    ("AHD_MSF-CD4 <200 _ WHO 3/4 + CD4 >200 _TB Urine LAM_POS _New",
                   "AHD_MSF-CD4 <200_ WHO 3/4 + CD4 >200 _TB Urine LAM_POS_Already On ART"),
    "xpert_test": ("AHD_MSF-CD4 <200 _ WHO 3/4 + CD4 >200 _TB Urine LAM_POS _Xpert MTB/RIF_New",
                   "AHD_MSF-CD4 <200 _ WHO 3/4 + CD4 >200 _TB Urine LAM_POS _Xpert MTB/RIF_Already on ART"),
    "xpert_pos":  ("AHD_MSF-CD4 <200 _ WHO 3/4 + CD4 >200 _TB Urine LAM_POS _Xpert MTB/RIF_POS_New",
                   "AHD_MSF-CD4 <200 _ WHO 3/4 + CD4 >200 _TB Urine LAM_POS _Xpert MTB/RIF_POS_Already on ART"),
    "tb_tx":      ("AHD_MSF-Confirmed on TB _TX _New", "AHD_MSF-Confirmed on TB _TX _Already on ART"),
    "crag_test":  ("AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg Test_New", "AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg Test_ Already on ART"),
    "crag_pos":   ("AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg_POS_New", "AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg_POS_ Already on ART"),
    "csf_test":   ("AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg_POS_CSF CrAg_ New",
                   "AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg_POS_CSF CrAg_ Already on ART"),
    "csf_pos":    ("AHD_MSF- CD4<200 or WHO 3/4_CSF CrAg_ POS_ New", "AHD_MSF- CD4<200 or WHO 3/4_CSF CrAg_ POS_ Already on ART"),
    "preempt":    ("AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg_POS_CSF CrAg_Neg on pre-emptive therapy_ New",
                   "AHD_MSF- CD4<200 or WHO 3/4_Serum CrAg_POS_CSF CrAg_Neg on pre-emptive therapy_ Already on ART"),
    "cm":         ("AHD_MSF- Confirmed CM_New", "AHD_MSF- Confirmed CM_ Already on ART"),
    "cm_rx":      ("AHD_MSF- Confirmed CM_ optimal CM treatment_ New", "AHD_MSF- Confirmed CM_ optimal CM treatment_ Already on ART"),
}
for _step, (_new, _art) in _AHD_STEPS.items():
    DHIS_MEASURES[f"ahd_{_step}_n"] = [Term("AHD", _new)]
    if _art:
        DHIS_MEASURES[f"ahd_{_step}_a"] = [Term("AHD", _art)]


def prefer_monthly_variants(monthly: pd.DataFrame) -> pd.DataFrame:
    """The monthly instance carries TX_TB elements twice: 'X (Monthly)' holds the month's
    value, plain 'X' holds a half-year cumulative total in March and September. Use the
    monthly variant under the plain name (the Power BI deck does the same)."""
    if monthly.empty:
        return monthly
    dn = monthly["dataname"].astype(str).str.strip()
    twin = dn.str.endswith("(Monthly)")
    bases = set(dn[twin].str.replace(r"\s*\(Monthly\)$", "", regex=True))
    out = monthly[~dn.isin(bases)].copy()
    out["dataname"] = out["dataname"].astype(str).str.strip().str.replace(r"\s*\(Monthly\)$", "", regex=True)
    return out

STATUS = {
    # "Actives" (2,267 clients at 85 sites, 2026-10-11) is a variant label for Active in
    # some EMR extracts; counted as active by programme decision (2026-10-11)
    "active": {"active", "actives"},
    "iit": {"ltfu", "lost to followup", "lost to follow up"},
    "dead": {"death", "dead", "known death"},
    "to": {"transferred out", "transfer out"},
    "stopped": {"stopped", "discontinued care", "stopped treatment"},
}


@dataclass
class Period:
    as_of: dt.date
    fy_start: dt.date

    @property
    def q_index(self) -> int:
        months = (self.as_of.year - self.fy_start.year) * 12 + self.as_of.month - self.fy_start.month
        return months // 3 + 1

    @property
    def q_start(self) -> dt.date:
        m0 = self.fy_start.month - 1 + (self.q_index - 1) * 3
        return dt.date(self.fy_start.year + m0 // 12, m0 % 12 + 1, 1)

    @property
    def q_end(self) -> dt.date:
        m0 = self.q_start.month - 1 + 3
        return dt.date(self.q_start.year + m0 // 12, m0 % 12 + 1, 1) - dt.timedelta(days=1)

    @property
    def q_months(self) -> list[str]:
        out, d = [], self.q_start
        while d <= self.q_end:
            out.append(f"{d.year}-{d.month:02d}")
            d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return out

    @property
    def label(self) -> str:
        return f"FY{str(self.fy_start.year + 1)[-2:]}Q{self.q_index}"

    def elapsed(self, through: dt.date | None = None) -> float:
        t = min(through or self.as_of, self.q_end)
        return max(0.0, min(1.0, ((t - self.q_start).days + 1) / ((self.q_end - self.q_start).days + 1)))


# ------------------------------------------------------------ dimensions ----
def age_band(age) -> str:
    try:
        a = float(age)
    except (TypeError, ValueError):
        return UNKNOWN_AGE
    if np.isnan(a) or a < 0:
        return UNKNOWN_AGE
    if a < 1:
        return "<1"
    if a < 5:
        return "1-4"
    if a >= 65:
        return "65+"
    lo = int(a // 5 * 5)
    return f"{lo}-{lo + 4}"


def age_bands(s: pd.Series) -> pd.Series:
    a = pd.to_numeric(s, errors="coerce")
    bins = [-0.001, 0.999, 4.999] + [x + 4.999 for x in range(5, 65, 5)] + [200]
    out = pd.cut(a, bins=bins, labels=[b for b in AGE_BANDS if b != "50+"]).astype(object)
    return out.where(out.notna(), UNKNOWN_AGE)


def norm_sex(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip().str.lower()
    return np.select([t.str.startswith("f"), t.str.startswith("m")], ["F", "M"], "U")


_RANGE = re.compile(r"(\d+)\s*(?:-|to)\s*(\d+)\s*(month|mnth|mon|m\b)?", re.I)


def parse_disagg(text: str) -> tuple[str, str]:
    """'Female, 15-19' / '15-19yrs, Male' / '<1, F' / '2-12 Months, Male' -> (sex, age band)."""
    t = str(text or "").lower()
    sex = "F" if re.search(r"\bfemale\b|\bf\b|\bwomen\b", t) else "M" if re.search(r"\bmale\b|\bm\b|\bmen\b", t) else "U"
    age = UNKNOWN_AGE
    if re.search(r"month", t) or re.search(r"<\s*1(?!\d)|less than 1\b", t):
        age = "<1"
    else:
        m = _RANGE.search(t)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            cand = "1-4" if (lo, hi) == (1, 4) else f"{lo}-{hi}"
            age = cand if cand in AGE_BANDS else UNKNOWN_AGE
        elif re.search(r"65\s*\+", t):
            age = "65+"
        elif re.search(r"50\s*\+", t):
            age = "50+"
    return sex, age


def _norm(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower()


def _datetimes(s: pd.Series) -> pd.Series:
    """Central-repo linelist dates are dd/mm/yyyy text; DB date columns and
    Excel cells arrive as dates/ISO strings. Never read 04/09 as April 9."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if pd.api.types.is_numeric_dtype(s):  # Excel serial days (PBS line list)
        return pd.to_datetime(s, unit="D", origin="1899-12-30", errors="coerce")
    t = s.astype(str).str.strip()
    dmy = pd.to_datetime(t, format="%d/%m/%Y", errors="coerce")
    iso = pd.to_datetime(t, format="ISO8601", errors="coerce")
    return dmy.fillna(iso)


def _dates(s: pd.Series) -> pd.Series:
    return _datetimes(s).dt.date


def _iso(s: pd.Series) -> pd.Series:
    """Dates as YYYY-MM-DD text; missing dates as blank (not 'NaT' / 'nan')."""
    return s.map(lambda x: "" if pd.isna(x) else x.isoformat())


def emr_unique(d: pd.DataFrame, c: dict, active: pd.Series) -> pd.Series:
    """EMR TX_CURR on the NDR rule: active AND (a valid biometric OR aged under 15). Without a
    valid-biometric column (older extracts) every active client counts."""
    if c.get("valid_bio") not in d:
        return active
    valid = _norm(d[c["valid_bio"]]).isin({"yes", "true", "1", "valid"})
    u15 = pd.to_numeric(d[c["age"]], errors="coerce") < 15 if c.get("age") in d else False
    return active & (valid | u15)


def _long(d: pd.DataFrame, masks: dict, mod: str = "", dated: dict | None = None) -> pd.DataFrame:
    """Count rows per facility x sex x age for each boolean mask -> long cube rows.
    dated: measure -> Series of event dates; those measures get a day period."""
    parts = []
    dated = dated or {}
    for k, mask in masks.items():
        x = d[mask]
        keys = ["state", "lga", "facility", "sex", "age"]
        if k in dated:
            x = x.assign(period=dated[k][mask].astype(str))
            keys = keys + ["period"]
        g = x.groupby(keys, dropna=False).size()
        if len(g):
            parts.append(g.rename("value").reset_index().assign(measure=k))
    if not parts:
        return pd.DataFrame(columns=DIMS + ["measure", "value"])
    out = pd.concat(parts, ignore_index=True)
    out["mod"] = mod
    if "period" not in out:
        out["period"] = ""
    out["period"] = out["period"].fillna("")
    return out[DIMS + ["measure", "value"]]


# ---------------------------------------------------------------- DHIS2 ----
def _dn_key(s: pd.Series) -> pd.Series:
    """Match data element names ignoring case and repeated spaces
    ('AHD_MSF-Confirmed  AHD _New' == 'AHD_MSF-Confirmed AHD _New')."""
    return s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.upper()


def _dhis_facts(merged: pd.DataFrame) -> pd.DataFrame:
    f = merged.copy()
    # monthly reports keep the month as their period; weekly rows keep their week
    if "period" not in f:
        f["period"] = f["month"]
    f["period"] = [(f"{p[:4]}-{p[4:]}" if len(str(p)) == 6 and str(p).isdigit() else str(p)) for p in f["period"]]
    f["_dn"] = _dn_key(f["dataname"])
    parsed = f["disagg"].map(parse_disagg)
    f["sex"] = [p[0] for p in parsed]
    f["age"] = [p[1] for p in parsed]
    # PMTCT mothers carry no sex in their disaggregation; infants (EID/HEI) do
    mothers = f["table"].isin(["PMTCT", "PMTCT_COMM"]) & ~f["_dn"].str.contains("EID|HEI") & (f["sex"] == "U")
    f.loc[mothers, "sex"] = "F"
    return f


_UNIQUE: dict = {}


def _term_rows(f: pd.DataFrame, t: Term) -> pd.DataFrame:
    """Facts feeding one Term (signed). Shared by the cube and the 'How it's calculated' breakdown.
    table "*" (DHIS2 indicator terms) = any table, each value counted once even when the same
    data element is pulled into two tables (PMTCT / PMTCT_EID share a dataset)."""
    if t.table == "*":
        if id(f) not in _UNIQUE:
            # one table per data element and instance (rows are not unique on facility x
            # disagg, so de-duplicating rows would drop sub-facility values)
            src = f["source"] if "source" in f else pd.Series("", index=f.index)
            first = f.assign(_s=src).groupby(["_s", "_dn"])["table"].transform("min")
            _UNIQUE.clear(); _UNIQUE[id(f)] = f[f["table"] == first]
        base = _UNIQUE[id(f)]
        sel = base[base["_dn"] == _dn_key(pd.Series([t.dataname]))[0]]
    else:
        sel = f[(f["table"] == t.table) & (f["_dn"] == _dn_key(pd.Series([t.dataname]))[0])]
    if t.source and "source" in sel:
        sel = sel[sel["source"] == t.source]
    if t.coc:
        sel = (sel[sel["disagg"].str.strip().str.lower() == t.coc.strip().lower()] if t.exact
               else sel[sel["disagg"].str.contains(t.coc, case=False, regex=True)])
    return sel.assign(mod=t.mod, value=sel["value"] * t.sign)


def parse_indicator(expr: str) -> list[tuple[int, str, str | None]]:
    """A DHIS2 indicator expression (sums and differences of #{dataElement} / #{dataElement.coc},
    with brackets) -> [(sign, dataElement uid, coc uid or None)]."""
    import re
    toks = re.findall(r"#\{[^}]+\}|[()+\-]", expr)
    pos = 0

    def term(sign):
        nonlocal pos
        tok = toks[pos]; pos += 1
        if tok == "(":
            out = expr_(sign)
            pos += 1                                   # ")"
            return out
        ref = tok[2:-1].split(".")
        return [(sign, ref[0], ref[1] if len(ref) > 1 and ref[1] != "*" else None)]

    def expr_(sign):
        nonlocal pos
        out, s = [], 1
        while pos < len(toks) and toks[pos] != ")":
            tok = toks[pos]
            if tok in "+-":
                s = -1 if tok == "-" else 1
                pos += 1
                continue
            out += term(sign * s)
            s = 1
        return out

    return expr_(1)


def indicator_mod(name: str) -> str:
    """Modality slot for an indicator's data element (keeps the HTS modality breakdown)."""
    n = str(name).upper()
    if "INDEX" in n:
        return "Index"
    if n.startswith("TB_STAT"):
        return "TB Clinic"
    if "PMTCT_STAT" in n:
        return "ANC1"
    import re
    return modality_of(re.sub(r"^HTS_TST(_POS)?_", "", str(name)))


def dhis_terms(merged: pd.DataFrame) -> pd.DataFrame:
    """Per measure x term x facility x period totals: shows which data elements make each number."""
    f = _dhis_facts(merged)
    out = []
    for key, terms in DHIS_MEASURES.items():
        for i, t in enumerate(terms):
            sel = _term_rows(f, t)
            if len(sel):
                g = sel.groupby(["state", "facility", "period"], dropna=False)["value"].sum().reset_index()
                out.append(g.assign(measure=key, term=i))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["state", "facility", "period", "value", "measure", "term"])


def dhis_cube(merged: pd.DataFrame) -> pd.DataFrame:
    """Merged DHIS2 facts -> cube rows, all months (pages pick the window)."""
    f = _dhis_facts(merged)
    out = []
    for key, terms in DHIS_MEASURES.items():
        parts = []
        for t in terms:
            sel = _term_rows(f, t)
            if sel.empty:
                continue
            parts.append(sel)
        if not parts:
            continue
        g = pd.concat(parts).groupby(["state", "lga", "facility", "sex", "age", "mod", "period"], dropna=False)["value"].sum()
        out.append(g.reset_index().assign(measure=key))
    if not out:
        return pd.DataFrame(columns=DIMS + ["measure", "value"])
    res = pd.concat(out, ignore_index=True)
    # HTS negatives (for the PrEP indicator) per slice
    t = res[res.measure.isin(["hts_tst", "hts_tst_pos"])]
    if len(t):
        p = t.pivot_table(index=DIMS, columns="measure", values="value", aggfunc="sum", fill_value=0).reset_index()
        if "hts_tst" in p:
            p["value"] = p["hts_tst"] - p.get("hts_tst_pos", 0)
            res = pd.concat([res, p[DIMS + ["value"]].assign(measure="hts_tst_neg")], ignore_index=True)
    return res[DIMS + ["measure", "value"]]


# ------------------------------------------------------------- linelist ----
def quarter_start(d: dt.date, fy_start_month: int = 10) -> dt.date:
    m0 = (d.month - fy_start_month) % 12
    m = d.month - m0 % 3
    return dt.date(d.year + (m <= 0) * -1, (m - 1) % 12 + 1, 1)


def _long_by(d: pd.DataFrame, mask: pd.Series, col: pd.Series, measure: str) -> pd.DataFrame:
    """Like _long for one mask, with the cube 'mod' slot carrying a category (regimen line, ...)."""
    x = d[mask].assign(mod=col[mask].fillna("").astype(str).str.strip().replace("", "Not recorded"))
    g = x.groupby(["state", "lga", "facility", "sex", "age", "mod"], dropna=False).size()
    return g.rename("value").reset_index().assign(measure=measure, period="")[DIMS + ["measure", "value"]]


def linelist_cube(ll: pd.DataFrame, c: dict, p: Period, history_start: dt.date | None = None,
                  today: dt.date | None = None) -> pd.DataFrame:
    d = ll.rename(columns={c["state"]: "state", c["lga"]: "lga", c["facility"]: "facility"}).copy()
    d["sex"] = norm_sex(d[c["sex"]]) if c.get("sex") in d else "U"
    d["age"] = age_bands(d[c["age"]]) if c.get("age") in d else UNKNOWN_AGE
    status = _norm(d[c["status"]])
    prev = _norm(d[c["prev_status"]]) if c["prev_status"] in d else pd.Series("", index=d.index)
    start = _dates(d[c["art_start"]])
    pickup = _datetimes(d[c["last_pickup"]])
    refill = pd.to_numeric(d[c["refill_days"]], errors="coerce").fillna(0)
    iit_date = (pickup + pd.to_timedelta(refill + 28, unit="D")).dt.date
    dos = pd.to_numeric(d[c["days_on_art"]], errors="coerce")
    vl_date = _dates(d[c["vl_date"]])
    vl = pd.to_numeric(d[c["vl_value"]], errors="coerce")
    ti = _norm(d[c["ti"]])

    # The treatment cohort follows the NDR unique rule: active AND (valid print OR under 15).
    # Everything below (TX_CURR, VL, MMD, the waterfall) uses that cohort; emr_active keeps
    # every client the EMR marks Active. Previous quarter: same rule on last quarter's status.
    active_all = status.isin(STATUS["active"])
    active = emr_unique(d, c, active_all)
    prev_active = emr_unique(d, c, prev.isin(STATUS["active"]))
    # TX_PVLS: on ART 6 months (183 days) or more; a result in the 12 months to the date (as the NDR summary)
    eligible = active & (dos >= 183)
    has_result = eligible & (vl_date > p.as_of - dt.timedelta(days=365)) & (vl_date <= p.as_of)
    # TX_NEW on the unique rule too: counted when the client is in the unique TX_CURR cohort
    # (active with a valid print, or under 15), as the NDR quarter summary
    uniq = active
    if c.get("new_unique") in d:      # a quarter file: the NDR's own unique flag (q<N>_algo 1-4)
        uniq = _norm(d[c["new_unique"]]).eq("yes")

    h0 = history_start or p.fy_start
    in_hist = lambda s: (s >= h0) & (s <= p.as_of)
    masks = {
        # EMR (central-repository linelist) count; the headline TX_CURR is the NDR figure
        # when the NDR PBS line list is loaded (see engine.run_engine)
        "tx_curr_emr": active,                               # valid print or under 15, as the NDR TX_CURR
        "emr_active": active_all,                            # every client the EMR marks Active
        "iit_now": status.isin(STATUS["iit"]),      # stock of clients currently in IIT (trend snapshots)
        "prev_tx_curr": prev_active,
        # dated events: the dashboard picks them up for any date range
        "tx_new_q": in_hist(start) & (ti != "yes") & uniq,
        "ti_q": active & (ti == "yes") & ~prev_active,
        "rtt_q": active & prev.isin(STATUS["iit"] | STATUS["to"] | STATUS["stopped"] | STATUS["dead"]),
        "iit_q": status.isin(STATUS["iit"]) & in_hist(iit_date),
        "dead_q": prev_active & status.isin(STATUS["dead"]),
        "to_q": prev_active & status.isin(STATUS["to"]),
        "stopped_q": prev_active & status.isin(STATUS["stopped"]),
        "vl_eligible": eligible,
        "vl_result": has_result,
        "vl_suppressed": has_result & (vl < 1000),
        "vl_unsuppressed": has_result & (vl >= 1000),
        "vl_undetectable": has_result & (vl < 50),
        "vl_llv": has_result & (vl >= 50) & (vl < 1000),
        "mmd3": active & (pd.to_numeric(d[c["refill_days"]], errors="coerce") >= 90),
        "mmd_lt3": active & (pd.to_numeric(d[c["refill_days"]], errors="coerce").fillna(0) < 90),
        "mmd_3": active & pd.to_numeric(d[c["refill_days"]], errors="coerce").between(90, 119),
        "mmd_45": active & pd.to_numeric(d[c["refill_days"]], errors="coerce").between(120, 179),
        "mmd_6": active & (pd.to_numeric(d[c["refill_days"]], errors="coerce") >= 180),
    }
    # VL flow: sample collected in the 12 months; a sample with no later result is pending
    yr = lambda s: (s > p.as_of - dt.timedelta(days=365)) & (s <= p.as_of)
    if c.get("sample_date") in d:
        sample = _dates(d[c["sample_date"]])
        masks["vl_sample"] = eligible & yr(sample)
        masks["vl_pending"] = eligible & yr(sample) & ~(vl_date >= sample)
    # EMR waterfall for the quarter the linelist date falls in (deck 'Continuity on Treatment'):
    # previous-quarter status from previous_quarter_status; the starting TX_CURR comes from that
    # table itself (engine), so clients no longer in the linelist land in 'gain/loss'
    qs = quarter_start(p.as_of, p.fy_start.month)
    new_q = (start >= qs) & (start <= p.as_of) & (ti != "yes") & uniq
    masks.update({
        "wf_new": new_q,
        "wf_ti": active & (ti == "yes") & ~new_q & ((lambda t: (t >= qs) & (t <= p.as_of))(_dates(d[c["ti_date"]]))
                                                    if c.get("ti_date") in d else ~prev_active),
        "wf_rtt": active & prev.isin(STATUS["iit"] | STATUS["to"] | STATUS["stopped"] | STATUS["dead"]) & ~new_q,
        "wf_to": prev_active & status.isin(STATUS["to"]),
        "wf_iit": prev_active & status.isin(STATUS["iit"]),
        "wf_dead": prev_active & status.isin(STATUS["dead"]),
        "wf_stopped": prev_active & status.isin(STATUS["stopped"]),
    })
    # appointments are classified in the dashboard from appointment_clients() (any date range)
    dated = {"tx_new_q": pd.Series(start, index=d.index), "iit_q": pd.Series(iit_date, index=d.index)}
    col = lambda k: _norm(d[c[k]]).fillna("").astype(object) if c.get(k) in d else pd.Series("", index=d.index)   # blanks -> ""
    has = lambda k: ~col(k).isin({"", "nan", "none", "<na>", "null", "nat", "n/a"})   # a value was recorded

    # ---- AHD among clients already on ART (EMR). Entry = unsuppressed (VL >= 1000) or returned to
    # treatment; CD4 for AHD screening (Indication_AHD), CD4 < 200 (LFA or count), WHO 3/4, LF-LAM,
    # serum and CSF CrAg, GeneXpert. Newly initiated AHD stays on DHIS2 (AHD_MSF ..._New).
    if c.get("ahd_indication") in d:
        rtt = active & prev.isin(STATUS["iit"] | STATUS["to"] | STATUS["stopped"] | STATUS["dead"])
        entry = active & ((has_result & (vl >= 1000)) | rtt)
        cd4n = pd.to_numeric(d[c["cd4"]], errors="coerce") if c.get("cd4") in d else pd.Series(float("nan"), index=d.index)
        cd4_done = entry & (has("ahd_indication") | has("cd4_lfa"))
        lt200 = cd4_done & (col("cd4_lfa").str.contains("lessthan200") | (cd4n < 200))
        who = pd.to_numeric(col("who_stage").str.extract(r"(\d)")[0], errors="coerce")
        who34 = entry & who.isin([3, 4]) & ~lt200
        conf = lt200 | who34
        gxp_pos = col("tb_gxp_result").str.contains("mtb detected") & ~col("tb_gxp_result").str.contains("not detected")
        masks.update({
            "ahde_entry": entry, "ahde_unsupp": active & has_result & (vl >= 1000), "ahde_rtt": rtt,
            "ahde_cd4": cd4_done, "ahde_cd4lt200": lt200, "ahde_who34": who34, "ahde_confirmed": conf,
            "ahde_lam_test": conf & has("lflam"), "ahde_lam_pos": conf & col("lflam").eq("positive"),
            "ahde_xpert_test": conf & col("tb_gxp").eq("yes"), "ahde_xpert_pos": conf & gxp_pos,
            "ahde_tb_tx": conf & col("tb_status").str.contains("on treatment"),
            "ahde_crag_test": conf & has("crag"), "ahde_crag_pos": conf & col("crag").eq("positive"),
            "ahde_csf_test": conf & has("csf_crag"), "ahde_csf_pos": conf & col("csf_crag").eq("positive"),
        })

    # ---- DSD (EMR): facility dispensing vs decentralised (community) vs not recorded, with VL by group
    if c.get("dsd_model") in d:
        dm = col("dsd_model")
        grp = pd.Series("none", index=d.index).mask(dm.str.contains("facility"), "fac").mask(dm.str.contains("decentral|ddd|community"), "com")
        for g in ("fac", "com", "none"):
            m = grp.eq(g)
            masks.update({f"dsd_{g}": active & m, f"dsd_{g}_elig": eligible & m, f"dsd_{g}_res": has_result & m,
                          f"dsd_{g}_sup": has_result & m & (vl < 1000)})

    # ---- TB screening among PLHIV on ART (EMR), dated by the TB status date; treatment by its start date
    if c.get("tb_status") in d and c.get("tb_status_date") in d:
        sdate = _dates(d[c["tb_status_date"]])
        scr = active & pd.Series(sdate, index=d.index).notna()
        symptom = sum((col(k) == "yes").astype(int) for k in ("tb_cough", "tb_fever", "tb_weight", "tb_sweats")) > 0
        st = col("tb_status")
        presump = scr & (symptom | st.str.contains("suspected|diagnosed|on treatment"))
        gxp_pos = col("tb_gxp_result").str.contains("mtb detected") & ~col("tb_gxp_result").str.contains("not detected")
        diag = scr & (gxp_pos | col("tb_afb_result").eq("positive") | col("tb_cxr_result").eq("suggestive")
                      | col("tb_culture_result").eq("positive") | st.str.contains("diagnosed|on treatment"))
        tbm = {"tbs_screened": scr, "tbs_presumptive": presump,
               "tbs_gxp": scr & col("tb_gxp").eq("yes"), "tbs_gxp_pos": scr & gxp_pos,
               "tbs_afb": scr & col("tb_afb").eq("yes"), "tbs_afb_pos": scr & col("tb_afb_result").eq("positive"),
               "tbs_cxr": scr & col("tb_cxr").eq("yes"), "tbs_cxr_pos": scr & col("tb_cxr_result").eq("suggestive"),
               "tbs_tested": scr & (col("tb_gxp").eq("yes") | col("tb_afb").eq("yes") | col("tb_cxr").eq("yes") | col("tb_culture").eq("yes")),
               "tbs_diagnosed": diag}
        masks.update(tbm)
        dated.update({k: pd.Series(sdate, index=d.index) for k in tbm})
        if c.get("tb_tx_start") in d:
            txd = _dates(d[c["tb_tx_start"]])
            masks["tbs_tx_started"] = active & pd.Series(txd, index=d.index).notna()
            dated["tbs_tx_started"] = pd.Series(txd, index=d.index)
        # current TB status of everyone on treatment (stock)
        for k, pat in (("tbst_none", "no signs"), ("tbst_susp", "suspected"), ("tbst_diag", "diagnosed"),
                       ("tbst_ontx", "on treatment"), ("tbst_inh", "inh|prophylaxis")):
            masks[k] = active & st.str.contains(pat)

    # ---- OTZ (EMR): enrolled = enrolment date recorded and not exited / opted out
    if c.get("otz_enrolled") in d:
        enr = pd.Series(_dates(d[c["otz_enrolled"]]), index=d.index).notna() & ~col("otz_outcome").str.contains("exit|opt")
        masks.update({"otz_enrolled": active & enr, "otz_vl_res": has_result & enr, "otz_vl_sup": has_result & enr & (vl < 1000),
                      "otz_vl_und": has_result & enr & (vl < 50), "otz_iit": status.isin(STATUS["iit"]) & enr,
                      "otz_transitioned": active & col("otz_transitioned").eq("yes")})

    out = [_long(d, masks, dated=dated)]
    for key, meas in (("regimen_line", "tx_regline"), ("regimen", "tx_regimen")):
        if c.get(key) in d:
            out.append(_long_by(d, active, d[c[key]], meas))
    if c.get("dsd_type") in d:
        out.append(_long_by(d, active, d[c["dsd_type"]], "dsd_type"))
    if c.get("regimen_line") in d:
        out.append(_long_by(d, has_result, d[c["regimen_line"]], "vlr_regline"))
        out.append(_long_by(d, has_result & (vl < 1000), d[c["regimen_line"]], "vls_regline"))
    return pd.concat(out, ignore_index=True)


def ndr_waterfall(pbs: pd.DataFrame, c: dict, q_start: dt.date, as_of: dt.date) -> pd.DataFrame:
    """NDR continuity for the fiscal year to date, on the NDR TX_CURR basis (valid unique print
    or under 15): previous TX_CURR = counted and active at the end of last FY's Q4
    (CurrentStatus28Q4; print status as of the file), current = NDR TX_CURR now. ART-start,
    transfer and death dates from the NDR line list, from q_start (1 October)."""
    need = ("prev_status28", "status28", "art_start")
    if pbs.empty or any(c.get(k) not in pbs for k in need):
        return pd.DataFrame(columns=DIMS + ["measure", "value"])
    d = pbs.rename(columns={c["state"]: "state", c["facility"]: "facility"}).copy()
    d["lga"] = ""
    d["sex"] = norm_sex(d[c["sex"]]) if c.get("sex") in d else "U"
    d["age"] = age_bands(d[c["age"]]) if c.get("age") in d else UNKNOWN_AGE
    x = _pbs_flags(d, c)
    u15 = pd.to_numeric(d[c["age"]], errors="coerce") < 15 if c.get("age") in d else False
    countable = (x["valid"] & (x["dt"] != "duplicate")) | u15          # NDR TX_CURR rule
    act_now, act_prev = _col(d, c, "status28") == "active", _col(d, c, "prev_status28") == "active"
    now, prev = act_now & countable, act_prev & countable
    inq = lambda k: (lambda s: (s >= q_start) & (s <= as_of))(_dates(d[c[k]])) if c.get(k) in d else pd.Series(False, index=d.index)
    flag = lambda k: pd.to_numeric(d[c[k]], errors="coerce").fillna(0).astype(int).eq(1) if c.get(k) in d else pd.Series(False, index=d.index)
    new = inq("art_start")
    ti = flag("ti_flag") & inq("ti_date") & ~new
    to = prev & flag("to_flag") & inq("to_date")
    dead = prev & flag("dead_flag") & inq("dead_date")
    masks = {"ndr_wf_prev": prev, "ndr_wf_new": new, "ndr_wf_ti": ti,
             "ndr_wf_rtt": now & ~prev & ~new & ~ti,
             "ndr_wf_to": to, "ndr_wf_dead": dead & ~to,
             "ndr_wf_iit": prev & ~act_now & ~to & ~dead, "ndr_wf_curr": now}
    return _long(d, masks)


VL_COLS = ["datim", "art_no", "hosp_no", "vl_value", "vl_band", "vl_date", "sample_date", "repeat", "regimen_line",
           "regimen", "art_start", "days_on_art", "last_pickup", "next_appt"]


def vl_clients(ll: pd.DataFrame, lc: dict, ll_date: dt.date) -> pd.DataFrame:
    """Virally unsuppressed clients, the same rule as the vl_unsuppressed measure: active, 180+ days
    on ART, VL result in the 12 months to the linelist date, VL >= 1000 copies/mL. 'repeat' says
    whether a sample has been collected after that result (EAC / repeat VL follow-up). No names."""
    if ll.empty or lc.get("vl_value") not in ll:
        return pd.DataFrame()
    active = emr_unique(ll, lc, _norm(ll[lc["status"]]).isin(STATUS["active"]))   # same cohort as the cascade
    dos = pd.to_numeric(ll[lc["days_on_art"]], errors="coerce")
    vld = _dates(ll[lc["vl_date"]])
    vl = pd.to_numeric(ll[lc["vl_value"]], errors="coerce")
    has = active & (dos >= 180) & (vld >= ll_date - dt.timedelta(days=365)) & (vld <= ll_date)
    sel = ll[has & (vl >= 1000)]
    if sel.empty:
        return pd.DataFrame()
    txt = lambda k: sel[lc[k]].fillna("").astype(str).replace({"nan": "", "None": "", "NaT": ""}) if lc.get(k) in sel else ""
    day = lambda k: _iso(_dates(sel[lc[k]])) if lc.get(k) in sel else ""
    smp = _dates(sel[lc["sample_date"]]) if lc.get("sample_date") in sel else pd.Series(pd.NaT, index=sel.index)
    v = vl[sel.index]
    out = pd.DataFrame({
        "state": sel[lc["state"]], "facility": sel[lc["facility"]], "sex": norm_sex(sel[lc["sex"]]),
        "age": age_bands(sel[lc["age"]]) if lc.get("age") in sel else UNKNOWN_AGE,
        "datim": txt("datim"), "art_no": txt("art_no"), "hosp_no": txt("hosp_no"),
        "vl_value": v.round().astype("Int64").astype(str), "vl_band": np.where(v >= 10000, "≥10,000", "1,000–9,999"),
        "vl_date": _iso(vld[sel.index]), "sample_date": _iso(smp),
        "repeat": np.where(smp > vld[sel.index], "Repeat sample collected", "No repeat sample yet"),
        "regimen_line": txt("regimen_line"), "regimen": txt("regimen"), "art_start": day("art_start"),
        "days_on_art": dos[sel.index].round().astype("Int64").astype(str), "last_pickup": day("last_pickup"),
        "next_appt": day("next_appt")}, index=sel.index)
    return out.reset_index(drop=True)


EAC_COLS = ["datim", "art_no", "hosp_no", "status", "transferred_in", "trigger_vl", "trigger_date", "cycle",
            "s1", "s2", "s3", "s4", "s5", "s6", "sessions", "fu_vl", "fu_date", "fu_status", "switched"]


def eac_clients(df: pd.DataFrame, c: dict, since: dt.date) -> pd.DataFrame:
    """EAC_Script_Version11 output, one row per client (latest EAC episode), as the clinical team
    defined it: entry = first VL >= 50; sessions counted from the triggering high VL, 1-3 standard,
    4-6 extended; follow-up VL = first result after the triggering VL (<50 suppressed, 50-999 LLV,
    >=1000 unsuppressed). The trigger falls back to the first high VL when no EAC is documented.
    Kept: triggering VL dated on/after `since` (the history window). Names and DOB are never read."""
    if df is None or df.empty or c.get("trigger_vl") not in df and c.get("first_vl") not in df:
        return pd.DataFrame()
    col = lambda k: df[c[k]] if c.get(k) in df else pd.Series(None, index=df.index, dtype=object)
    num = lambda k: pd.to_numeric(col(k), errors="coerce")
    day = lambda k: _datetimes(col(k)).dt.date
    tv = num("trigger_vl").fillna(num("first_vl"))
    td = day("trigger_date")
    td = td.where(td.notna(), day("first_vl_date"))
    keep = td.notna() & (td >= since)
    d = df[keep]
    if d.empty:
        return pd.DataFrame()
    txt = lambda k: col(k)[keep].fillna("").astype(str).replace({"nan": "", "None": "", "NaT": ""}).str.strip()
    iso = lambda k: _iso(day(k)[keep])
    out = pd.DataFrame({
        "state": txt("state"), "facility": txt("facility"), "sex": norm_sex(col("sex")[keep]),
        "age": age_bands(col("age")[keep]),
        "datim": txt("datim"), "art_no": txt("art_no"), "hosp_no": txt("hosp_no"),
        "status": txt("status"), "transferred_in": txt("transferred_in"),
        "trigger_vl": tv[keep].round().astype("Int64").astype(str).replace({"<NA>": ""}),
        "trigger_date": _iso(td[keep]),
        "cycle": num("cycle")[keep].fillna(0).astype(int).astype(str),
        **{f"s{i}": iso(f"s{i}") for i in range(1, 7)},
        "sessions": num("sessions")[keep].fillna(0).astype(int).astype(str),
        "fu_vl": num("fu_vl")[keep].round().astype("Int64").astype(str).replace({"<NA>": ""}),
        "fu_date": iso("fu_date"), "fu_status": txt("fu_status"), "switched": txt("switched"),
    }, index=d.index)
    return out.reset_index(drop=True)


APPT_COLS = ["datim", "art_no", "hosp_no", "status", "appt", "last_pickup", "refill_days", "regimen_line",
             "vl_value", "vl_date", "flags"]


def appointment_clients(ll: pd.DataFrame, lc: dict, pbs: pd.DataFrame, pc: dict, ll_date: dt.date,
                        today: dt.date, lo: dt.date | None = None, hi: dt.date | None = None) -> pd.DataFrame:
    """Active clients with a next pharmacy appointment between lo and hi (the dashboard picks a
    range inside it), each with 'nopick' (no pickup on or after the appointment, as of the
    linelist date) and service-delivery flags: biometric issues (NDR), VL due / unsuppressed /
    result pending. No names."""
    if ll.empty or lc.get("next_appt") not in ll:
        return pd.DataFrame()
    appt = _dates(ll[lc["next_appt"]])
    pickup = _dates(ll[lc["last_pickup"]])
    active = _norm(ll[lc["status"]]).isin(STATUS["active"])
    lo = lo or ll_date - dt.timedelta(days=28)
    hi = hi or (pd.Timestamp(today) + pd.offsets.MonthEnd(0)).date()
    nopick = ~(pickup >= appt)
    # active clients, plus those the EMR has since moved to IIT: they missed the visit
    iit = _norm(ll[lc["status"]]).isin(STATUS["iit"])
    sel = ll[(active | iit) & (appt >= lo) & (appt <= hi)]
    if sel.empty:
        return pd.DataFrame()
    txt = lambda k: sel[lc[k]].fillna("").astype(str).replace({"nan": "", "None": ""}) if lc.get(k) in sel else ""
    dos = pd.to_numeric(sel[lc["days_on_art"]], errors="coerce")
    vl = pd.to_numeric(sel[lc["vl_value"]], errors="coerce")
    vld = _dates(sel[lc["vl_date"]])
    in12 = (vld >= ll_date - dt.timedelta(days=365)) & (vld <= ll_date)
    flags = pd.Series("", index=sel.index)
    add = lambda m, t: flags.where(~m, flags + ("; " if True else "") + t)
    vl_due = (dos >= 180) & ~in12
    flags = add(vl_due, "VL due (no result in 12 months)")
    flags = add(in12 & (vl >= 1000), "Unsuppressed (≥1000): EAC / repeat VL")
    if lc.get("sample_date") in sel:
        smp = _dates(sel[lc["sample_date"]])
        flags = add((smp >= ll_date - dt.timedelta(days=365)) & ~(vld >= smp), "VL sample awaiting result")
    # biometric issues from the NDR line list (same client link as the concurrence)
    if not pbs.empty and pc.get("datim") in pbs and pc.get("art_no") in pbs:
        x = _pbs_flags(pbs, pc)
        nd = pd.DataFrame({"key": _client_key(pbs[pc["datim"]], pbs[pc["art_no"]]),
                           "datim": pbs[pc["datim"]], "art_no": pbs[pc["art_no"]],
                           "issue": np.select([~x["base"], x["fp"] == "adjudicationwaiting", x["dt"] == "duplicate",
                                               x["fp"] == "duplicate", x["fp"] == "no_valid_data", x["dt"] == "mis-match"],
                                              ["not captured", "awaiting adjudication", "confirmed duplicate",
                                               "ABIS-flagged duplicate", "invalid prints", "mis-match"], "")})
        e = pd.DataFrame({"key": _client_key(ll[lc["datim"]], ll[lc["art_no"]]), "datim": ll[lc["datim"]], "art_no": ll[lc["art_no"]]})
        nd = nd.drop_duplicates("key")
        _relink(e, nd)
        issue = e["key"].map(dict(zip(nd["key"], nd["issue"])))
        on = e["key"].isin(set(nd["key"]))
        bio = pd.Series(np.where(~on, "no NDR record", issue.fillna("")), index=ll.index)[sel.index]
        flags = add(bio != "", "Biometrics: " + bio)
    out = pd.DataFrame({
        "state": sel[lc["state"]], "facility": sel[lc["facility"]], "sex": norm_sex(sel[lc["sex"]]),
        "age": age_bands(sel[lc["age"]]) if lc.get("age") in sel else UNKNOWN_AGE,
        "nopick": nopick[sel.index].astype(int), "status": txt("status"),
        "datim": txt("datim"), "art_no": txt("art_no"), "hosp_no": txt("hosp_no"),
        "appt": _iso(appt[sel.index]), "last_pickup": _iso(pickup[sel.index]),
        "refill_days": txt("refill_days"), "regimen_line": txt("regimen_line"),
        "vl_value": txt("vl_value"), "vl_date": _iso(vld),
        "flags": flags.str.lstrip("; ")}, index=sel.index)
    return out.reset_index(drop=True)


# ------------------------------------------------------------ biometrics ----
# Follow-up categories for the biometrics client list (code -> label), most urgent first
PBS_FOLLOWUP = {
    "nocap": "Not captured", "adj": "Awaiting adjudication", "dup": "Duplicate",
    "mis": "Mis-match", "fpdup": "Flagged duplicate (ABIS)", "inv": "Invalid prints",
    "nomatch": "Recapture no match", "nobase": "Recapture no base",
}


def _col(d: pd.DataFrame, c: dict, key: str) -> pd.Series:
    """Normalised column by settings key; blank when the source lacks it."""
    name = c.get(key)
    return _norm(d[name]) if name in d else pd.Series("", index=d.index)


def _pbs_flags(d: pd.DataFrame, c: dict) -> dict[str, pd.Series]:
    active = _col(d, c, "status28") == "active"
    base = _col(d, c, "baseline") == "yes"
    rec = _col(d, c, "recapture") == "yes"
    mo, fp, dt_ = _col(d, c, "match_outcome"), _col(d, c, "fp_status"), _col(d, c, "dup_type")
    return dict(active=active, base=base, rec=rec, mo=mo, fp=fp, dt=dt_,
                valid=_col(d, c, "valid") == "yes")


def pbs_cube(pbs: pd.DataFrame, c: dict) -> pd.DataFrame:
    """Biometrics cube. Card definitions reproduce the NDR PBS portal
    (TX_CURR, Captured, Valid, Invalid, Duplicate, Unique = Valid - Duplicate)."""
    if pbs.empty:
        return pd.DataFrame(columns=DIMS + ["measure", "value"])
    d = pbs.rename(columns={c["state"]: "state", c["facility"]: "facility"}).copy()
    if "lga" not in d:
        d["lga"] = ""
    d["sex"] = norm_sex(d[c["sex"]]) if c.get("sex") in d else "U"
    d["age"] = age_bands(d[c["age"]]) if c.get("age") in d else UNKNOWN_AGE
    x = _pbs_flags(d, c)
    active, base, rec, mo, fp = x["active"], x["base"], x["rec"], x["mo"], x["fp"]
    masks = {
        "pbs_active": active,
        "pbs_baseline": active & base,                      # portal "Captured"/"Processed"
        "pbs_valid": active & x["valid"],
        "pbs_invalid": active & (fp == "no_valid_data"),
        "pbs_enrolled": active & (fp == "enrolled"),
        "pbs_adjudication": active & (fp == "adjudicationwaiting"),
        "pbs_fp_duplicate": active & (fp == "duplicate"),  # ABIS-flagged, before adjudication
        # PBS portal "Duplicate" card = duplicate_type; FingerPrintStatus DUPLICATE is far wider
        "pbs_duplicate": active & ((x["dt"] == "duplicate") if c.get("dup_type") in d else (fp == "duplicate")),
        "pbs_mismatch": active & (x["dt"] == "mis-match"),
        "pbs_recapture": active & base & rec,
        "pbs_match": active & rec & (mo == "match"),
        "pbs_nomatch": active & rec & (mo == "no match"),
        "pbs_nobase": active & rec & (mo == "no base"),
        "pbs_rejected": active & rec & (mo == "rejected"),
    }
    if c.get("last_pickup") in d and c.get("art_start") in d:
        masks["pbs_returned_refill"] = active & (_datetimes(d[c["last_pickup"]]) > _datetimes(d[c["art_start"]]))
    if c.get("valid") in d:
        # NDR TX_CURR (CDC): valid recognised prints - the portal's "Unique" (valid minus
        # duplicate type "Duplicate") - plus every active client under 15 whatever the print status
        under15 = pd.to_numeric(d[c["age"]], errors="coerce") < 15 if c.get("age") in d else False
        masks["ndr_tx_curr"] = active & ((x["valid"] & (x["dt"] != "duplicate")) | under15)
        if c.get("unique_flag") in d:   # a quarter file carries the NDR's own unique classification
            masks["ndr_tx_curr"] = active & (_norm(d[c["unique_flag"]]).eq("yes") | under15)
        excl = active & ~masks["ndr_tx_curr"]
        masks["ndr_excl"] = excl
        masks["ndr_excl_adj"] = excl & (fp == "adjudicationwaiting")
        masks["ndr_excl_dup"] = excl & ~(fp == "adjudicationwaiting") & ((x["dt"] == "duplicate") | (fp == "duplicate"))
        masks["ndr_excl_nocap"] = excl & ~base & ~masks["ndr_excl_adj"] & ~masks["ndr_excl_dup"]   # no print captured
        masks["ndr_excl_other"] = excl & ~masks["ndr_excl_adj"] & ~masks["ndr_excl_dup"] & ~masks["ndr_excl_nocap"]
        if c.get("last_pickup") in d:          # excluded clients still picking up drugs (last 3 months)
            lp = _datetimes(d[c["last_pickup"]])
            masks["ndr_excl_refill"] = excl & (lp >= lp.max() - pd.Timedelta(days=91))
    return _long(d, masks)


def _client_key(datim: pd.Series, art_no: pd.Series) -> pd.Series:
    n = lambda s: s.astype(str).str.strip().str.upper().str.replace(r"\s+", "", regex=True)
    return n(datim) + "|" + n(art_no)


_ART_LEVELS = [
    ("punctuation", lambda s: s.str.replace(r"[^A-Z0-9]", "", regex=True)),                          # DEL\GHO\00914 = DELGHO00914
    ("leading zeros", lambda s: s.str.replace(r"[^A-Z0-9]", "", regex=True)
                                 .str.replace(r"\d+", lambda m: str(int(m.group())), regex=True)),    # 0000133 = 133
]


def _relink(e: pd.DataFrame, n: pd.DataFrame) -> set:
    """Second and third tries for NDR records with no exact EMR match: ART numbers
    compared ignoring punctuation, then also leading zeros. Only one-to-one matches
    within the same DATIM site are accepted. Rewrites n['key'] to the EMR key in place;
    returns the EMR keys linked this way."""
    ek, loose = set(e["key"]), set()
    for _, norm in _ART_LEVELS:
        taken = set(n["key"]) & ek
        un = n[~n["key"].isin(ek)]
        ue = e[~e["key"].isin(taken)].drop_duplicates("key")
        if un.empty or ue.empty:
            break
        up = lambda s: s.fillna("").astype(str).str.upper().str.strip()
        kn = up(un["datim"]) + "|" + norm(up(un["art_no"]))
        ke = up(ue["datim"]) + "|" + norm(up(ue["art_no"]))
        kn, ke = kn[~kn.duplicated(keep=False) & ~kn.str.endswith("|")], ke[~ke.duplicated(keep=False) & ~ke.str.endswith("|")]
        target = dict(zip(ke.values, ue.loc[ke.index, "key"].values))
        hit = kn[kn.isin(target)]
        n.loc[hit.index, "key"] = hit.map(target)
        loose |= set(hit.map(target))
    return loose


def facility_key(name) -> str:
    """Name key that survives the differences between DHIS2, linelist and PBS
    spellings: state-code prefix ('de ', 'os ', 'ek '), punctuation, quotes
    (incl. a mis-encoded ’), centre/center and word order
    ('de General Hospital Okwe' == 'Okwe General Hospital')."""
    t = str(name).lower().strip()
    t = re.sub(r"^(de|os|ek)\s+", "", t)
    t = re.sub(r"(â€™|[\"'’`.])", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).replace("centre", "center")
    return " ".join(sorted(t.split()))


# EMR TX_CURR vs NDR TX_CURR, client by client: measure -> line-list label
CONC_CATS = {
    "conc_emr_ndr_inactive": "EMR active · inactive on NDR",
    "conc_emr_ndr_noprint": "EMR active · active on NDR but no valid unique print",
    "conc_emr_missing": "EMR active · not found on NDR",
    "conc_ndr_emr_inactive": "NDR TX_CURR · inactive in EMR",
    "conc_ndr_missing": "NDR TX_CURR · not in EMR linelist",
}
CONC_COLS = ["datim", "art_no", "hosp_no", "unique_id", "emr_status", "emr_art_start", "emr_last_pickup",
             "emr_refill_days", "ndr_status28", "ndr_last_pickup", "ndr_fp_status", "ndr_valid", "ndr_dup_type",
             "ndr_captured", "reason"]


def concurrence(ll: pd.DataFrame, lc: dict, pbs: pd.DataFrame, pc: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """EMR TX_CURR (central-repository linelist, Active) vs NDR TX_CURR (valid unique
    print or <15, CurrentStatus28 Active), linked on DATIM code + ART number
    (linelist pepid = NDR PatientARTNumber). Returns (cube rows, one row per client
    who differs, with the reason; no names). Needs the NDR line list with active AND
    inactive clients to tell 'inactive on NDR' from 'not on NDR'."""
    empty = pd.DataFrame(columns=DIMS + ["measure", "value"]), pd.DataFrame()
    need_l, need_p = ("datim", "art_no", "status"), ("datim", "art_no", "status28")
    if ll.empty or pbs.empty or any(lc.get(k) not in ll for k in need_l) or any(pc.get(k) not in pbs for k in need_p):
        return empty
    txt = lambda df, k, c: df[c[k]].fillna("").astype(str).replace({"nan": "", "None": "", "NaT": ""}) if c.get(k) in df else ""
    day = lambda df, k, c: _iso(_dates(df[c[k]])) if c.get(k) in df else ""
    e = pd.DataFrame({"key": _client_key(ll[lc["datim"]], ll[lc["art_no"]]), "act": emr_unique(ll, lc, _norm(ll[lc["status"]]).isin(STATUS["active"])),
                      "state": ll[lc["state"]], "facility": ll[lc["facility"]], "sex": norm_sex(ll[lc["sex"]]),
                      "age": age_bands(ll[lc["age"]]) if lc.get("age") in ll else UNKNOWN_AGE,
                      "datim": txt(ll, "datim", lc), "art_no": txt(ll, "art_no", lc), "hosp_no": txt(ll, "hosp_no", lc),
                      "unique_id": txt(ll, "id", lc), "emr_status": txt(ll, "status", lc), "emr_art_start": day(ll, "art_start", lc),
                      "emr_last_pickup": day(ll, "last_pickup", lc), "emr_refill_days": txt(ll, "refill_days", lc)})
    x = _pbs_flags(pbs, pc)
    u15 = pd.to_numeric(pbs[pc["age"]], errors="coerce") < 15 if pc.get("age") in pbs else False
    n = pd.DataFrame({"key": _client_key(pbs[pc["datim"]], pbs[pc["art_no"]]), "act": x["active"],
                      "counted": x["active"] & ((x["valid"] & (x["dt"] != "duplicate")) | u15),
                      "state": pbs[pc["state"]], "facility": pbs[pc["facility"]],
                      "sex": norm_sex(pbs[pc["sex"]]) if pc.get("sex") in pbs else "U",
                      "age": age_bands(pbs[pc["age"]]) if pc.get("age") in pbs else UNKNOWN_AGE,
                      "datim": txt(pbs, "datim", pc), "art_no": txt(pbs, "art_no", pc), "hosp_no": txt(pbs, "hosp_no", pc),
                      "unique_id": txt(pbs, "unique_id", pc), "ndr_status28": txt(pbs, "status28", pc),
                      "ndr_last_pickup": day(pbs, "last_pickup", pc), "ndr_fp_status": txt(pbs, "fp_status", pc),
                      "ndr_valid": txt(pbs, "valid", pc), "ndr_dup_type": txt(pbs, "dup_type", pc),
                      "ndr_captured": day(pbs, "date_captured", pc)})
    n = n.sort_values(["counted", "act"], ascending=False).drop_duplicates("key")   # a counted/active record wins
    loose = _relink(e, n)                     # ART numbers differing only in punctuation / leading zeros
    ndr = n.set_index("key")
    emr = e.drop_duplicates("key").set_index("key")
    ndr_cols = ["ndr_status28", "ndr_last_pickup", "ndr_fp_status", "ndr_valid", "ndr_dup_type", "ndr_captured"]
    emr_cols = ["emr_status", "emr_art_start", "emr_last_pickup", "emr_refill_days"]

    # EMR side: every EMR-active row (so the categories add up to EMR TX_CURR)
    E = e[e["act"]].copy()
    on = E["key"].isin(ndr.index)
    cnt = E["key"].map(ndr["counted"]).fillna(False).astype(bool)
    nact = E["key"].map(ndr["act"]).fillna(False).astype(bool)
    E["measure"] = np.select([cnt, on & ~nact, on & nact], ["conc_both", "conc_emr_ndr_inactive", "conc_emr_ndr_noprint"],
                             "conc_emr_missing")
    for col in ndr_cols:
        E[col] = E["key"].map(ndr[col]).fillna("")
    es = lambda col: E[col].fillna("").astype(str)
    fpr = es("ndr_fp_status").str.upper().replace("", "NOT CAPTURED")
    dup = pd.Series(np.where(es("ndr_dup_type").str.lower() == "duplicate", " (confirmed duplicate)", ""), index=E.index)
    E["reason"] = np.select(
        [E["measure"] == "conc_emr_ndr_inactive", E["measure"] == "conc_emr_ndr_noprint", E["measure"] == "conc_emr_missing"],
        ["NDR CurrentStatus28 = Inactive (NDR last pickup " + es("ndr_last_pickup").replace("", "unknown") + ")",
         "Active on NDR, print not counted: " + fpr + dup,
         pd.Series("No NDR record with this DATIM code + ART number", index=E.index)], "")

    # NDR side: NDR TX_CURR clients not active in the EMR
    N = n[n["counted"]].copy()
    inE = N["key"].isin(emr.index)
    eact = N["key"].map(emr["act"]).fillna(False).astype(bool)
    N = N[~eact].copy()
    inE = inE[N.index]
    N["measure"] = np.where(inE, "conc_ndr_emr_inactive", "conc_ndr_missing")
    for col in emr_cols:
        N[col] = N["key"].map(emr[col]).fillna("")
    ll_sites = set(ll[lc["facility"]].map(facility_key))
    s = lambda col: N[col].fillna("").astype(str)
    site = pd.Series(np.where(N["facility"].map(facility_key).isin(ll_sites), "Site is in the EMR linelist; client not found",
                              "Site not in the EMR linelist (central repository)"), index=N.index)
    first = ("EMR status " + s("emr_status").replace("", "unknown")).where(N["measure"] == "conc_ndr_emr_inactive", site)
    fp_u = s("ndr_fp_status").str.upper()
    counted_as = ("valid print (" + fp_u + ")").where(s("ndr_valid").str.lower() == "yes",
                                                     "under 15, " + fp_u.replace("", "NOT CAPTURED"))
    N["reason"] = first + "; counted on NDR as " + counted_as

    rows = pd.concat([E, N], ignore_index=True)
    lz = rows["key"].isin(loose)
    rows.loc[lz, "reason"] = rows.loc[lz, "reason"] + " · ART no. matched ignoring punctuation / leading zeros"
    g = rows.groupby(["state", "facility", "sex", "age", "measure"]).size().rename("value").reset_index()
    clients = rows[rows["measure"] != "conc_both"][["state", "facility", "sex", "age", "measure"] + CONC_COLS].fillna("")
    return g.assign(lga="", mod="", period="")[DIMS + ["measure", "value"]], clients.reset_index(drop=True)


def pbs_clients(pbs: pd.DataFrame, c: dict) -> pd.DataFrame:
    """Active clients needing biometric follow-up, one row per client with a
    ';'-joined list of PBS_FOLLOWUP codes."""
    if pbs.empty:
        return pd.DataFrame()
    x = _pbs_flags(pbs, c)
    a = x["active"]
    cats = {
        "nocap": a & ~x["base"], "adj": a & (x["fp"] == "adjudicationwaiting"),
        "dup": a & (x["dt"] == "duplicate"), "mis": a & (x["dt"] == "mis-match"),
        "fpdup": a & (x["fp"] == "duplicate"), "inv": a & (x["fp"] == "no_valid_data"),
        "nomatch": a & x["rec"] & (x["mo"] == "no match"), "nobase": a & x["rec"] & (x["mo"] == "no base"),
    }
    flags = pd.DataFrame(cats)
    keep = flags.any(axis=1)
    if not keep.any():
        return pd.DataFrame()
    sub = pbs[keep]
    get = lambda k: sub[c[k]].fillna("").astype(str).replace({"nan": "", "None": ""}) if c.get(k) in sub else ""
    out = pd.DataFrame({
        "state": get("state"), "facility": get("facility"),
        "sex": norm_sex(sub[c["sex"]]) if c.get("sex") in sub else "U",
        "age": age_bands(sub[c["age"]]) if c.get("age") in sub else UNKNOWN_AGE,
        "unique_id": get("unique_id"), "art_no": get("art_no"), "hosp_no": get("hosp_no"),
        "fp_status": get("fp_status"), "dup_type": get("dup_type"), "match": get("match_outcome"),
        "captured": _iso(_dates(sub[c["date_captured"]]))
                    if c.get("date_captured") in sub else "",
        "last_pickup": _iso(_dates(sub[c["last_pickup"]]))
                       if c.get("last_pickup") in sub else "",
    }, index=sub.index)
    out["cats"] = flags[keep].apply(lambda r: ";".join(k for k, v in r.items() if v), axis=1)
    return out.reset_index(drop=True)


def pbs_from_linelist(ll: pd.DataFrame, lc: dict) -> tuple[pd.DataFrame, dict]:
    """Fallback when no NDR PBS line list is available: baseline and recapture
    from the central-repo linelist. Adjudication / duplicates stay blank."""
    if ll.empty or lc.get("pbs") not in ll:
        return pd.DataFrame(), {}
    d = pd.DataFrame({
        "state": ll[lc["state"]], "facility": ll[lc["facility"]], "sex": ll[lc["sex"]],
        "age": ll[lc["age"]] if lc.get("age") in ll else None,
        "currentstatus28": ll[lc["status"]],
        "fingerprint enrolled": ll[lc["pbs"]],
        "recapture fingerprint enrolled": ll[lc["recapture_flag"]] if lc.get("recapture_flag") in ll else "",
        "unique_id": ll[lc["id"]] if lc.get("id") in ll else "",
        "date_captured": ll[lc["pbs_date"]] if lc.get("pbs_date") in ll else "",
        "lastdrugpickupdate": ll[lc["last_pickup"]],
    })
    c = dict(state="state", facility="facility", sex="sex", age="age", status28="currentstatus28",
             baseline="fingerprint enrolled", recapture="recapture fingerprint enrolled",
             unique_id="unique_id", date_captured="date_captured", last_pickup="lastdrugpickupdate")
    return d, c


# ---------------------------------------------------------------- trends ----
def week_ends(start: dt.date, end: dt.date) -> list[dt.date]:
    """Sundays (end of each Monday–Sunday week) from start to end."""
    d = start + dt.timedelta(days=(6 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def linelist_backfill(ll: pd.DataFrame, c: dict, ends: list[dt.date]) -> pd.DataFrame:
    """ESTIMATED week-end (or month-end) TX_CURR per facility from one linelist snapshot.
    A client counts at month end T when ART started by T, not dead / transferred
    out / stopped by T, and their last pickup either falls after T or its refill
    (+28 days) still covers T. Clients who lapsed and later returned look
    continuously active, so earlier months are slightly overstated."""
    if ll.empty or not ends:
        return pd.DataFrame(columns=["state", "facility", "date", "measure", "value"])
    start = _datetimes(ll[c["art_start"]])
    pickup = _datetimes(ll[c["last_pickup"]])
    refill = pd.to_numeric(ll[c["refill_days"]], errors="coerce").fillna(0)
    covered_to = pickup + pd.to_timedelta(refill + 28, unit="D")
    status = _norm(ll[c["status"]])
    closed = status.isin(STATUS["dead"] | STATUS["to"] | STATUS["stopped"])
    out_date = _datetimes(ll[c["outcome_date"]]) if c.get("outcome_date") in ll else pd.Series(pd.NaT, index=ll.index)
    rows = []
    for T in ends:
        t = pd.Timestamp(T)
        on = (start <= t) & ((pickup > t) | (covered_to >= t)) & ~(closed & (out_date <= t))
        g = ll[on].groupby([c["state"], c["facility"]]).size()
        rows.append(g.rename("value").reset_index().assign(date=T.isoformat(), measure="tx_curr_est"))
    df = pd.concat(rows, ignore_index=True)
    return df.rename(columns={c["state"]: "state", c["facility"]: "facility"})


def pbs_backfill(pbs: pd.DataFrame, c: dict, ends: list[dt.date]) -> pd.DataFrame:
    """Month-end biometric capture among today's active clients, from the capture
    date: shows how coverage of the current cohort was built up."""
    if pbs.empty or c.get("date_captured") not in pbs or not ends:
        return pd.DataFrame(columns=["state", "facility", "date", "measure", "value"])
    x = _pbs_flags(pbs, c)
    cap = _datetimes(pbs[c["date_captured"]])
    rows = []
    for T in ends:
        on = x["active"] & x["base"] & (cap <= pd.Timestamp(T))
        g = pbs[on].groupby([c["state"], c["facility"]]).size()
        rows.append(g.rename("value").reset_index().assign(date=T.isoformat(), measure="pbs_baseline_est"))
    df = pd.concat(rows, ignore_index=True)
    return df.rename(columns={c["state"]: "state", c["facility"]: "facility"})
