from openpyxl import Workbook

from kpi.pcsvp import COLUMNS, read_bulletin


def test_reads_weekly_bulletin(tmp_path):
    wb = Workbook(); ws = wb.active; ws.title = "CCMP KPIs"
    ws.append(["Week", "Date", "Quarter", "State", "COMMUNITY HTS"])
    ws.append([None, None, None, None] + [c[1] for c in COLUMNS])
    ws.append(["Week5", None, "FY27Q1", "Delta"])                       # blank week: skipped
    ws.append(["Week6", None, "FY27Q1", "Delta", 61, 3, 3, 521])
    ws.append(["Week6", None, "FY27Q1", "Lagos", 1, 1, 1, 1])            # not a SPEED state: skipped
    f = tmp_path / "PCSVP_Weekly_Bulletin_FY27.xlsx"; wb.save(f)
    data, issues = read_bulletin(f)
    assert not issues and len(data["rows"]) == 1
    r = data["rows"][0]
    assert r["week"] == 6 and r["state"] == "Delta" and r["v"]["hts_comm"] == 61 and r["v"]["reach_0_17"] == 521
