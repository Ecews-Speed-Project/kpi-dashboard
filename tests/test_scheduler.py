"""The server job's scheduler: run times, and what it records when an update works or fails."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("scheduler", Path(__file__).resolve().parents[1] / "scripts" / "scheduler.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def test_schedule_and_next_run(monkeypatch):
    monkeypatch.setenv("SCHEDULE", "12:30, 06:30")
    monkeypatch.setenv("DAYS", "mon-fri")
    times, days = S.schedule()
    assert times == [dt.time(6, 30), dt.time(12, 30)] and days == {0, 1, 2, 3, 4}
    fri_eve = dt.datetime(2026, 10, 23, 18, 0)                       # Friday evening -> Monday 06:30
    assert S.next_run(fri_eve, times, days) == dt.datetime(2026, 10, 26, 6, 30)
    tue_mid = dt.datetime(2026, 10, 20, 9, 0)                        # Tuesday morning -> same day 12:30
    assert S.next_run(tue_mid, times, days) == dt.datetime(2026, 10, 20, 12, 30)
    monkeypatch.setenv("DAYS", "mon,wed")
    assert S.schedule()[1] == {0, 2}


def _fake_root(tmp_path, monkeypatch, script: str):
    (tmp_path / "run_pipeline.py").write_text(script, encoding="utf-8")
    monkeypatch.setattr(S, "ROOT", tmp_path)
    monkeypatch.setattr(S, "LIVE", tmp_path / "live")
    monkeypatch.setattr(S, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(S, "STATUS", tmp_path / "live" / "status.json")
    sent = []
    monkeypatch.setattr(S, "alert", lambda subject, body: sent.append(subject))
    return sent


def test_failed_then_working_run(tmp_path, monkeypatch):
    sent = _fake_root(tmp_path, monkeypatch, "import sys\nprint('DHIS2 pull')\nprint('ConnectionError: dropped')\nsys.exit(1)\n")
    assert S.run_once() is False
    st = json.loads((tmp_path / "live" / "status.json").read_text())
    assert st["ok"] is False and st["failures"] == 1 and "ConnectionError" in st["error"] and not st["running"]
    assert sent == ["SPEED dashboard: the scheduled update failed"]

    (tmp_path / "run_pipeline.py").write_text("print('FY27Q1  as of 2026-10-23  cube rows 1  -> live')\n", encoding="utf-8")
    assert S.run_once() is True
    st = json.loads((tmp_path / "live" / "status.json").read_text())
    assert st["ok"] is True and st["failures"] == 0 and st["error"] is None and st["last_success"] == st["finished"]
    assert sent[-1] == "SPEED dashboard: updates are working again"
    assert len(list((tmp_path / "logs").glob("run_*.log"))) >= 1


def test_exit_zero_without_a_build_counts_as_failed(tmp_path, monkeypatch):
    # e.g. "Another update is already running": nothing was built, so the dashboard did not move on
    _fake_root(tmp_path, monkeypatch, "print('Another update is already running (process 1); this one stops.')\n")
    assert S.run_once() is False
