"""TRM deck: figures follow the cube, and no sample number from the template reaches the file."""
import re
import zipfile
from pathlib import Path

from kpi import trm_report as T

TEMPLATE = Path("dashboard/assets/trm_template.pptx")


def _data():
    periods = [{"key": "", "kind": "", "start": None, "end": None, "month": ""},
               {"key": "2026-10", "kind": "M", "start": "2026-10-01", "end": "2026-10-31", "month": "2026-10"},
               {"key": "2026W45", "kind": "W", "start": "2026-11-02", "end": "2026-11-08", "month": "2026-11"},
               {"key": "2026-11-03", "kind": "D", "start": "2026-11-03", "end": "2026-11-03", "month": "2026-11"}]
    dims = {"facility": [["Ekiti", "ek Ado", "A"], ["Osun", "os Iwo", "B"]], "sex": ["F", "M"], "age": ["10-14", "25-29"], "mod": ["", "ANC1"],
            "measure": ["hts_tst", "hts_tst_pos", "tx_new_q", "hivst_assisted"]}
    rows = [(0, 0, 1, 0, 1, 0, 1000), (0, 1, 1, 0, 2, 0, 200), (0, 1, 1, 0, 1, 1, 40), (0, 1, 1, 0, 2, 1, 8), (0, 1, 1, 0, 3, 2, 5),
            (0, 0, 1, 1, 2, 0, 30), (1, 0, 1, 0, 1, 0, 777), (0, 0, 1, 0, 2, 3, 12)]
    return {"meta": {"fy_start": "2026-10-01", "q_start": "2026-10-01", "q_end": "2026-12-31", "q_index": 1, "fiscal_year": "FY27", "as_of": "2026-11-13",
                     "dhis_through": "2026-11-08", "linelist_as_of": "2026-11-10"},
            "periods": periods, "cube": {"dims": dims, "cols": [list(c) for c in zip(*rows)]}}


def test_monthly_then_weeks_after_it_for_one_state():
    f = T.Figures(_data(), "Ekiti")
    assert f.fy("hts_tst") == 1230                     # October monthly + the week after it; Osun left out
    assert f.week("hts_tst") == 230 and f.week("tx_new_q") == 5 and f.week_label() == "Week 45 - 2026-11-02 - 2026-11-08"
    assert f.week("hts_tst", not_mods={"ANC1"}) == 200
    assert [f.label(i) for i in f.points] == ["Oct", "W45"]


def test_deck_holds_real_figures_and_no_sample_numbers(tmp_path):
    out = tmp_path / "trm.pptx"
    T.build_deck(_data(), TEMPLATE, "Ekiti", out)
    z = zipfile.ZipFile(out)
    names = z.namelist()
    slides = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    assert len(slides) == len(T.KEEP)
    charts = [n for n in names if re.match(r"ppt/charts/chart\d+\.xml$", n)]
    assert sorted(Path(n).stem for n in charts) == sorted("chart%d" % i for i in range(1, 39))
    text = " ".join(t for n in slides for t in re.findall(r"<a:t>([^<]*)</a:t>", z.read(n).decode("utf-8")))
    for sample in ("126,030", "3,009", "1,646", "2,894", "7,379", "2,890", "[ STATE ]"):
        assert sample not in text
    assert "Ekiti" in text and "1,230" in text
    assert not any(b"illustrative" in z.read(n) for n in names if "notesSlide" in n and n.endswith(".xml"))
    assert sorted(Path(n).stem for n in names if re.match(r"ppt/charts/chartEx\d+\.xml$", n)) == ["chartEx1", "chartEx23", "chartEx24", "chartEx25"]
    assert not any(b"<mc:Fallback>" in z.read(n) and b"cx:chart" in z.read(n) for n in slides)
    for sample in ("15,555", "2,987", "212 of 868 due", "2 community", "49,588", "30,193", "2,384 of 2,980", "89,674", "−7,378", "11,615 of 12,625 visits", "273", "F 24% · M 39%", "2,664", "1,134 of 1,960 eligible", "F 92% · M 86%",
                   "45,376", "46,301 of 49,585", "542 of 597", "311 of 397 with results", "66 of 72", "1,571",
                   "48 of 50 HIV-positive pregnant women on ART this week", "FY 14,860", "38 known · 12 new", "Samples pending testing: 0", "25,962", "20,976", "2,402", "701 of 714", "27 new positives",
                   "4,545", "4,002", "1,001", "50,303", "6 sites", "610"):
        assert sample not in text
    bio = next(n for n in slides if b"Biometric capture" in z.read(n))
    newly = " ".join(re.findall(r"<a:t>([^<]*)</a:t>", z.read(bio).decode("utf-8"))).split("Newly initiated on ART · index week")[1]
    assert not re.search(r"\b(75|72|71|66)\b", newly)          # the template's sample for the newly initiated
    cascade = z.read("ppt/charts/chartEx24.xml").decode("utf-8")
    assert re.search(r'<cx:subtotals>.*val="4".*val="10"', cascade) and "GAIN/LOSS" in cascade


def test_status_colours_follow_the_value():
    run = '<a:r><a:rPr lang="en-US" b="1"><a:solidFill><a:srgbClr val="{}"/></a:solidFill></a:rPr><a:t>{}</a:t></a:r>'
    out = T.status_colours(run.format("C0392B", "97%") + run.format("08684E", "57%") + run.format("B7791F", "92%") + run.format("1F2A26", "40%"), .95, .05)
    assert re.findall(r'val="([0-9A-F]{6})"', out) == ["08684E", "C0392B", "B7791F", "1F2A26"]


def test_targets_follow_the_dashboard_rules():
    meta = {"fiscal_year": "FY27", "q_index": 1, "dhis_through": "2026-11-15", "as_of": "2026-11-15", "rag": {"red_below": .5, "green_from": .75, "rate_warning_gap": .05}}
    q = {"start": "2026-10-01", "end": "2026-12-31"}
    tg = [{"target_key": "CF", "scope": "Ekiti", "age_band": "All", "annual": 400, "fy": "FY27"}, {"target_key": "COV", "scope": "Ekiti", "age_band": "All", "annual": .98, "fy": "FY27"}]
    count = T.dash_target({"target": "CF", "kind": "count", "flow": "cumulative", "source": "DHIS2"}, 40, tg, meta, "Ekiti", q)
    assert count["target"] == 100 and abs(count["share"] - .4) < 1e-9 and count["label"] == "On track"     # 40 of 50 expected by 15 Nov
    rate = T.dash_target({"target": "COV", "kind": "rate"}, .95, tg, meta, "Ekiti", q)
    assert rate["label"] == "Watch" and T.dash_target({"target": "NONE", "kind": "rate"}, .9, tg, meta, "Ekiti", q)["label"] == "No target"
