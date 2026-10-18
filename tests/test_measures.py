import datetime as dt

import pandas as pd

from kpi.measures import _dates, dhis_cube, parse_disagg


def test_parse_dhis2_disaggs():
    assert parse_disagg("Female, 20-24 years") == ("F", "20-24")
    assert parse_disagg("Male, <1yr") == ("M", "<1")
    assert parse_disagg("Female, 1-4yrs") == ("F", "1-4")
    assert parse_disagg("Male, 50+") == ("M", "50+")
    assert parse_disagg("<10 years")[1] != "<1"
    assert parse_disagg("New positives, Male, 10-14 years") == ("M", "10-14")


def _facts(rows):
    df = pd.DataFrame(rows, columns=["table", "dataname", "disagg", "value"])
    return df.assign(state="Delta", lga="Warri", facility="F1", period="202609")


def test_hts_totals_follow_dax():
    f = _facts([
        ("HTS_TST", "HTS_TST_VCT", "Female, 20-24 years", 10),
        ("HTS_TST", "HTS_TST_POS_VCT", "Female, 20-24 years", 2),     # must not count as a test
        ("TB", "TB_STAT_NUM", "Male, 30-34 years", 5),
        ("TB", "TB_STAT_POS_Previously known positive", "Male, 30-34 years", 1),
        ("PMTCT", "PMTCT_STAT_NUM (Newly Tested)", "25-29 years, Newly Tested Positives", 1),
        ("PMTCT", "PMTCT_STAT_NUM (Newly Tested)", "25-29 years, New Negative", 7),
        ("INDEX", "HTS_INDEX_CONTACTS TEST RESULT( Includes Previously Known)", "New positives, Male, 35-39 years", 3),
        ("INDEX", "HTS_INDEX_CONTACTS TEST RESULT( Includes Previously Known)", "known positives, Male, 35-39 years", 9),
        ("AHD", "AHD_MSF-Confirmed  AHD _New", "Male, 40-44 years", 4),
    ])
    tot = dhis_cube(f).groupby("measure")["value"].sum()
    assert tot["hts_tst"] == 10 + (5 - 1) + 8 + 3
    assert tot["hts_tst_pos"] == 2 + 1 + 3
    assert tot["ahd_diagnosed"] == 4
    pmtct = dhis_cube(f).query("measure == 'hts_tst' and mod == 'ANC1'")
    assert set(pmtct["sex"]) == {"F"}


def test_linelist_dates_are_day_first():
    s = pd.Series(["04/09/2019", "20/05/2013", "", None, "2027-07-01"])
    assert list(_dates(s)[:2]) == [dt.date(2019, 9, 4), dt.date(2013, 5, 20)]
    assert _dates(s)[2:4].isna().all()
    assert _dates(s)[4] == dt.date(2027, 7, 1)


def test_pbs_cards_follow_ndr_portal():
    from kpi.measures import pbs_clients, pbs_cube
    c = {"state": "statename", "facility": "facilityname", "sex": "sex", "age": "currentage",
         "status28": "currentstatus28", "baseline": "fingerprint enrolled", "valid": "fingerprint valid",
         "recapture": "recapture fingerprint enrolled", "match_outcome": "match_outcome",
         "fp_status": "fingerprintstatus", "dup_type": "duplicate_type", "unique_id": "unique_id"}
    rows = [  # enrolled, valid, status, dup type, recapture, match
        ("YES", "YES", "ENROLLED", "", "YES", "Match"),
        ("YES", "YES", "DUPLICATE", "Duplicate", "YES", "No match"),
        ("YES", "YES", "DUPLICATE", "Mis-Match", "NO", ""),
        ("YES", "YES", "ADJUDICATIONWAITING", "", "NO", ""),
        ("YES", "NO", "NO_VALID_DATA", "", "NO", ""),
        ("NO", "NO", "", "", "NO", ""),
    ]
    df = pd.DataFrame(rows, columns=["fingerprint enrolled", "fingerprint valid", "fingerprintstatus",
                                     "duplicate_type", "recapture fingerprint enrolled", "match_outcome"])
    df = df.assign(statename="Osun", facilityname="F1", sex="F", currentage=30, currentstatus28="Active",
                   unique_id=[f"U{i}" for i in range(len(df))])
    v = pbs_cube(df, c).groupby("measure")["value"].sum()
    assert (v["pbs_active"], v["pbs_baseline"], v["pbs_valid"], v["pbs_invalid"]) == (6, 5, 4, 1)
    assert (v["pbs_duplicate"], v["pbs_mismatch"], v["pbs_fp_duplicate"], v["pbs_adjudication"]) == (1, 1, 2, 1)
    assert v["pbs_valid"] - v["pbs_duplicate"] == 3          # portal "Unique"
    assert v["ndr_tx_curr"] == 2          # valid, minus the confirmed duplicate and the adjudication waiting (all 15+ here)
    kid = df.iloc[[5]].assign(currentage=9)                   # not captured, but under 15 -> counted
    assert pbs_cube(kid, c).query("measure == 'ndr_tx_curr'")["value"].sum() == 1
    waiting_kid = df.iloc[[3]].assign(currentage=12)          # under 15 waiting for adjudication -> counted
    assert pbs_cube(waiting_kid, c).query("measure == 'ndr_tx_curr'")["value"].sum() == 1
    cl = pbs_clients(df, c).set_index("unique_id")["cats"]
    assert cl["U5"] == "nocap" and set(cl["U1"].split(";")) == {"dup", "fpdup", "nomatch"}
    assert "U0" not in cl.index


def test_concurrence_links_on_datim_and_art_number():
    from kpi.measures import concurrence
    lc = {"datim": "datimcode", "art_no": "pepid", "status": "currentartstatus", "state": "state",
          "facility": "facilityname", "sex": "sex", "age": "currentage"}
    pc = {"datim": "datimcode", "art_no": "patientartnumber", "status28": "currentstatus28", "state": "statename",
          "facility": "facilityname", "sex": "sex", "age": "currentage"}
    pc.update({"valid": "fingerprint valid", "dup_type": "duplicate_type", "fp_status": "fingerprintstatus"})
    ll = pd.DataFrame({"datimcode": "D1", "pepid": ["A1", "A2", "A3", "A4", "A5"],
                       "currentartstatus": ["Active", "Active", "Active", "LTFU", "Active"]}).assign(
        state="Delta", facilityname="F1", sex="F", currentage=30, firstname="NEVER READ")
    ndr = pd.DataFrame({"datimcode": "d1", "patientartnumber": ["a1 ", "A2", "A4", "A9", "A5"],     # case/space differences
                        "currentstatus28": ["Active", "Inactive", "Active", "Active", "Active"],
                        "fingerprint valid": ["YES", "YES", "YES", "YES", "NO"],
                        "fingerprintstatus": ["ENROLLED", "ENROLLED", "ENROLLED", "ENROLLED", ""],
                        "duplicate_type": ""}).assign(statename="Delta", facilityname="F1", sex="F", currentage=30)
    rows, clients = concurrence(ll, lc, ndr, pc)
    v = rows.groupby("measure")["value"].sum()
    assert v.to_dict() == {"conc_both": 1, "conc_emr_ndr_inactive": 1, "conc_emr_ndr_noprint": 1, "conc_emr_missing": 1,
                           "conc_ndr_emr_inactive": 1, "conc_ndr_missing": 1}
    assert len(clients) == 5 and "firstname" not in clients and all(clients["reason"] != "")
    # waiting for adjudication (15+) is not in NDR TX_CURR_Unique, so it is not an "NDR only" client
    aw = ndr.assign(fingerprintstatus=["ENROLLED", "ENROLLED", "ENROLLED", "ADJUDICATIONWAITING", ""])
    v_aw = concurrence(ll, lc, aw, pc)[0].groupby("measure")["value"].sum()
    assert v_aw.get("conc_ndr_missing", 0) == 0

    # levels 2-3: punctuation and leading zeros ignored, one-to-one within the site
    ll2 = pd.DataFrame({"datimcode": "D1", "pepid": ["DELGHO00914", "133", "77"],
                        "currentartstatus": ["Active", "LTFU", "Active"]}).assign(state="Delta", facilityname="F1", sex="F", currentage=30)
    ndr2 = pd.DataFrame({"datimcode": "D1", "patientartnumber": ["DEL\\GHO\\00914", "0000133", "077", "0077"],
                         "currentstatus28": "Active", "fingerprint valid": "YES", "fingerprintstatus": "ENROLLED",
                         "duplicate_type": ""}).assign(statename="Delta", facilityname="F1", sex="F", currentage=30)
    rows2, cl2 = concurrence(ll2, lc, ndr2, pc)
    v2 = rows2.groupby("measure")["value"].sum().to_dict()
    assert v2["conc_both"] == 1                          # DEL\GHO\00914 = DELGHO00914
    assert v2["conc_ndr_emr_inactive"] == 1              # 0000133 = 133 (inactive in EMR)
    assert v2["conc_emr_missing"] == 1 and v2["conc_ndr_missing"] == 2   # '077' and '0077' both claim '77': not one-to-one
    assert "leading zeros" in cl2.set_index("art_no").loc["0000133", "reason"]


def test_actives_counts_as_active():
    from kpi.measures import STATUS
    assert {"active", "actives"} <= STATUS["active"]


def test_excel_serial_dates():
    assert list(_dates(pd.Series([43831, 43826]))) == [dt.date(2020, 1, 1), dt.date(2019, 12, 27)]


def test_db_date_objects_pass_through():
    s = pd.Series([dt.date(2026, 9, 27), None])
    assert _dates(s)[0] == dt.date(2026, 9, 27)
