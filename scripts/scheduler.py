"""Run the dashboard update on a schedule (the server job, inside its Docker container).

    python scripts/scheduler.py            # wait for each scheduled time, run the update, repeat
    python scripts/scheduler.py --now      # run once now (then exit)

Settings (environment variables):
    SCHEDULE      run times, local time, comma separated        default "06:30,12:30"
    DAYS          days to run: mon-fri, daily, or e.g. mon,wed,fri default "mon-fri"
    RETRY_MINUTES a failed run is retried once after this long   default 20 (0 = no retry)
    TZ            time zone of the times above                   e.g. Africa/Lagos
    ALERT_TO, ALERT_FROM, ALERT_SMTP_HOST, ALERT_SMTP_PORT (587), ALERT_SMTP_USER, ALERT_SMTP_PASS
                  optional: e-mail these addresses when a run fails, and when it works again

Each run is `run_pipeline.py run`: it pulls DHIS2, the CDR line list and the newest NDR file in the inbox,
and writes the dashboard to live/ (data files first, the page last), so a failed run leaves the last good
dashboard in place. The outcome goes to live/status.json, which the dashboard reads to warn when the
figures are older than they should be, and to logs/run_<date>_<time>.log.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE, LOGS = ROOT / "live", ROOT / "logs"
STATUS = LIVE / "status.json"
DAYNAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def schedule() -> tuple[list[dt.time], set[int]]:
    times = sorted(dt.time.fromisoformat(t.strip()) for t in os.environ.get("SCHEDULE", "06:30,12:30").split(",") if t.strip())
    spec = os.environ.get("DAYS", "mon-fri").strip().lower()
    if spec in ("daily", "all", "*"):
        days = set(range(7))
    elif "-" in spec:
        a, b = (DAYNAMES.index(x.strip()[:3]) for x in spec.split("-", 1))
        days = set(range(a, b + 1))
    else:
        days = {DAYNAMES.index(x.strip()[:3]) for x in spec.split(",") if x.strip()}
    return times, days


def next_run(now: dt.datetime, times: list[dt.time], days: set[int]) -> dt.datetime:
    for add in range(8):
        day = now.date() + dt.timedelta(days=add)
        if day.weekday() in days:
            for t in times:
                at = dt.datetime.combine(day, t)
                if at > now:
                    return at
    raise ValueError("no run time in the schedule")


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_status(st: dict) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, STATUS)


def alert(subject: str, body: str) -> None:
    to, host = os.environ.get("ALERT_TO"), os.environ.get("ALERT_SMTP_HOST")
    if not (to and host):
        return
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("ALERT_FROM") or os.environ.get("ALERT_SMTP_USER") or "speed-dashboard@localhost"
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("ALERT_SMTP_PORT", 587)), timeout=60) as s:
            s.starttls()
            if os.environ.get("ALERT_SMTP_USER"):
                s.login(os.environ["ALERT_SMTP_USER"], os.environ.get("ALERT_SMTP_PASS", ""))
            s.send_message(msg)
    except Exception as e:                       # an alert that cannot be sent must not stop the job
        print(f"alert not sent: {type(e).__name__}: {e}", flush=True)


def run_once() -> bool:
    """One update; returns True when it finished. Records the outcome in live/status.json."""
    LOGS.mkdir(parents=True, exist_ok=True)
    start = dt.datetime.now()
    log = LOGS / f"run_{start:%Y-%m-%d_%H%M}.log"
    st = read_status()
    st.update(running=True, started=start.isoformat(timespec="seconds"))
    write_status(st)
    with open(log, "w", encoding="utf-8") as f:
        code = subprocess.run([sys.executable, str(ROOT / "run_pipeline.py"), "run"], cwd=ROOT, stdout=f,
                              stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUTF8="1")).returncode
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    ok = code == 0 and any(l.startswith("FY") and " as of " in l for l in lines)
    end = dt.datetime.now()
    was_failing = st.get("ok") is False
    st.update(running=False, finished=end.isoformat(timespec="seconds"), ok=ok, log=log.name,
              minutes=round((end - start).total_seconds() / 60, 1))
    if ok:
        st.update(last_success=st["finished"], error=None, failures=0)
        if was_failing:
            alert("SPEED dashboard: updates are working again", f"The update of {st['finished']} finished.")
    else:
        tail = [l for l in lines if l.strip()][-6:]
        st.update(error="\n".join(tail)[-1500:], failures=int(st.get("failures") or 0) + 1)
        alert("SPEED dashboard: the scheduled update failed",
              f"The update started {st['started']} failed (exit code {code}). The dashboard still shows the last good "
              f"figures, from {st.get('last_success') or 'an earlier run'}.\n\nLast lines of {log.name}:\n" + "\n".join(tail))
    write_status(st)
    print(f"{end:%Y-%m-%d %H:%M} update {'finished' if ok else 'FAILED'} ({st['minutes']} min) - {log.name}", flush=True)
    return ok


def main() -> None:
    if "--now" in sys.argv:
        sys.exit(0 if run_once() else 1)
    times, days = schedule()
    retry = int(os.environ.get("RETRY_MINUTES", 20))
    print(f"scheduler: runs at {', '.join(t.strftime('%H:%M') for t in times)} on "
          f"{', '.join(DAYNAMES[d] for d in sorted(days))} (time zone {os.environ.get('TZ', 'system')})", flush=True)
    st = read_status()
    if st.get("running"):                        # the container stopped during a run: record it as failed
        st.update(running=False, ok=False, error="the job stopped during this run (container restart)")
        write_status(st)
    while True:
        at = next_run(dt.datetime.now(), times, days)
        print(f"next update: {at:%a %d %b %H:%M}", flush=True)
        while (wait := (at - dt.datetime.now()).total_seconds()) > 0:
            time.sleep(min(wait, 300))           # short naps: a clock change or a long sleep cannot skip a run
        if not run_once() and retry:
            time.sleep(retry * 60)
            run_once()


if __name__ == "__main__":
    main()
