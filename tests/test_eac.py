import datetime as dt

import pandas as pd
import yaml

from kpi.measures import EAC_COLS, eac_clients

C = yaml.safe_load(open("config/settings.yaml", encoding="utf-8"))["columns"]["eac"]


def _script_rows():
    """Rows shaped like EAC_Script_Version11 output (lower-cased column names), including the
    name / DOB columns the pipeline must never carry forward."""
    return pd.DataFrame({
        "datim_code": ["D1", "D1", "D2"], "pepid": ["A1", "A2", "A3"], "patienthospitalno": ["H1", "H2", "H3"],
        "surname": ["X", "Y", "Z"], "firstname": ["P", "Q", "R"], "dob": ["01/01/1990"] * 3,
        "state": ["Delta"] * 3, "facilityname": ["Site A", "Site A", "Site B"], "sex": ["Female", "Male", "F"],
        "current_age": [34, 12, 41], "current_status": ["Active", "Active", "Lost to Follow-up"], "transferred_in": ["No"] * 3,
        "first_high_vl_value": [1500, 80, 20000], "first_high_vl_result_date": ["10/01/2026", "05/03/2026", "01/02/2020"],
        "eac_triggering_high_vl_value": [2500, None, 20000], "eac_triggering_high_vl_date": ["15/04/2026", None, "01/02/2020"],
        "eac_cycle_number": [1, 0, 1],
        "session_1_date": ["20/04/2026", None, "05/02/2020"], "session_2_date": ["20/05/2026", None, None],
        "session_3_date": ["20/06/2026", None, None], "session_4_extended_date": [None] * 3,
        "session_5_extended_date": [None] * 3, "session_6_extended_date": [None] * 3,
        "total_eac_sessions_all_cycles": [3, 0, 1],
        "followup_vl_value": [40, None, None], "followup_vl_result_date": ["01/08/2026", None, None],
        "followup_vl_suppression_status": ["Suppressed", None, None], "switched_to_second_line": ["No", "No", "No"],
        "date_generated": ["2026-10-12"] * 3})


def test_eac_clients_follow_the_script_and_drop_identifiers():
    out = eac_clients(_script_rows(), C, since=dt.date(2025, 10, 1))
    assert list(out["art_no"]) == ["A1", "A2"]                       # A3's trigger (2020) is before the window
    assert not {"surname", "firstname", "dob"} & set(out.columns)
    assert set(EAC_COLS) <= set(out.columns)
    a1, a2 = out.iloc[0], out.iloc[1]
    assert (a1["trigger_vl"], a1["trigger_date"]) == ("2500", "2026-04-15")   # latest EAC episode
    assert (a2["trigger_vl"], a2["trigger_date"]) == ("80", "2026-03-05")     # no EAC: first high VL
    assert a1["s3"] == "2026-06-20" and a2["s1"] == ""
    assert a1["fu_status"] == "Suppressed" and a1["fu_vl"] == "40"
    assert list(out["sex"]) == ["F", "M"]


def test_eac_clients_empty_input():
    assert eac_clients(pd.DataFrame(), C, since=dt.date(2025, 10, 1)).empty
    assert eac_clients(None, C, since=dt.date(2025, 10, 1)).empty
