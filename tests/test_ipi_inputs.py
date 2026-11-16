from kpi.ipi_inputs import read_inputs, write_template


def test_template_round_trip(tmp_path):
    f = write_template(tmp_path / "IPI_manual_inputs.xlsx")
    data, issues = read_inputs(f)
    assert not issues
    assert data["dqa"] == {"FY27Q1": {"Delta": [7, 6], "Ekiti": [4, 2], "Osun": [4, 3]}}
    assert data["jssv"] == {"FY27Q1": {"Delta": 0, "Ekiti": 8, "Osun": 0}}
    assert data["jssv_target"] == {"FY27": {"Delta": 2, "Ekiti": 2, "Osun": 2}}


def test_bad_rows_are_skipped(tmp_path):
    f = write_template(tmp_path / "x.xlsx", dqa=[("FY27Q2", "Lagos", 3, 2), ("fy27 q2", "delta", 5, 4)], jssv=[], targets=[])
    data, issues = read_inputs(f)
    assert data["dqa"] == {"FY27Q2": {"Delta": [4, 5]}} and len(issues) == 1
