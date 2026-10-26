"""Monthly case finding report: figures from the monthly instance only, Gen Pop / CSCC split, cumulative from October."""
import re
import zipfile
from pathlib import Path
from xml.dom import minidom

import pytest

from kpi.hts_report import write_reports

TEMPLATE = Path(__file__).resolve().parents[1] / "dashboard" / "assets" / "hts_case_finding_template.pptx"


def _data():
    dims = {"facility": [["Delta", "a", "Asaba GH"], ["Delta", "b", "Udu CSCC"], ["Ekiti", "c", "Ado GH"], ["Osun", "d", "Iwo GH"]],
            "sex": ["F", "M", "U"], "age": ["<1", "1-4", "5-9", "10-14", "15-19", "20-24", "Unknown"],
            "mod": ["", "Index", "VCT"],
            "period": ["2026-10", "2026-11", "2026W45", "2026-11-03"],
            "measure": ["hts_tst", "hts_tst_neg", "hts_tst_pos", "tx_new_q"]}
    periods = [{"key": "2026-10", "kind": "M", "month": "2026-10"}, {"key": "2026-11", "kind": "M", "month": "2026-11"},
               {"key": "2026W45", "kind": "W", "month": "2026-11"}, {"key": "2026-11-03", "kind": "D", "month": "2026-11"}]
    r = []
    # facility, sex, age, mod, period, measure, value
    r += [[0, 0, 5, 2, 0, 0, 100], [0, 0, 5, 2, 0, 1, 98], [0, 0, 5, 2, 0, 2, 2]]        # Oct, Delta GH
    r += [[0, 1, 5, 1, 1, 0, 50], [0, 1, 5, 1, 1, 1, 45], [0, 1, 5, 1, 1, 2, 5]]         # Nov, Delta GH
    r += [[1, 1, 5, 1, 1, 0, 20], [1, 1, 5, 1, 1, 1, 16], [1, 1, 5, 1, 1, 2, 4]]         # Nov, Delta CSCC (KP)
    r += [[2, 0, 5, 2, 2, 0, 999]]                                                       # weekly report: ignored
    r += [[0, 1, 5, 0, 3, 3, 4], [1, 1, 5, 0, 3, 3, 3]]                                  # TX_NEW in November
    return {"cube": {"dims": dims, "rows": r}, "periods": periods, "meta": {"fy_start": "2026-10-01"},
            "dataflow": [{"month": "2026-10", "closed": True}, {"month": "2026-11", "closed": False}]}


def _table(pptx: Path, slide_no: int) -> list[list[str]]:
    z = zipfile.ZipFile(pptx)
    pres = z.read("ppt/presentation.xml").decode()
    rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', z.read("ppt/_rels/presentation.xml.rels").decode()))
    order = [rels[x] for x in re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres)]
    doc = minidom.parseString(z.read("ppt/" + order[slide_no - 1]))
    txt = lambda n: "".join(t.firstChild.data if t.firstChild else "" for t in n.getElementsByTagName("a:t")).strip()
    return [[txt(c) for c in row.getElementsByTagName("a:tc")] for row in doc.getElementsByTagName("a:tr")]


@pytest.mark.skipif(not TEMPLATE.is_file(), reason="template not bundled")
def test_reports_month_and_cumulative(tmp_path):
    made = write_reports(_data(), tmp_path, TEMPLATE)
    assert [m["month"] for m in made] == ["2026-10", "2026-11"]
    assert [m["provisional"] for m in made] == [False, True]
    nov = tmp_path / "HTS_case_finding_2026-11.pptx"
    for name in zipfile.ZipFile(nov).namelist():                       # every part is valid XML
        if name.endswith((".xml", ".rels")):
            minidom.parseString(zipfile.ZipFile(nov).read(name))
    gen_month = _table(nov, 3)                                          # Gen Pop, November: CSCC and weekly excluded
    assert gen_month[1][:7] == ["Delta", "50", "45", "5", "10.0%", "4", "80.0%"]
    assert gen_month[2][1] == "0"                                       # Ekiti: only a weekly report, so nothing
    gen_cum = _table(nov, 4)                                            # Gen Pop, October + November
    assert gen_cum[1][1:4] == ["150", "143", "7"]
    kp = _table(nov, 5)                                                 # high risk = the CSCC site
    assert kp[1][:4] == ["Delta", "20", "16", "4"] and kp[4][0] == "Total"
    assert len(zipfile.ZipFile(nov).read("ppt/presentation.xml").decode().split("<p:sldId ")) - 1 == 16   # modality slide per state
