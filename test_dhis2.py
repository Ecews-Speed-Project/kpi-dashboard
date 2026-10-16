"""Connection test for the two SPEED DHIS2 instances.

    python test_dhis2.py

Uses DHIS2_WEEKLY_USER/PASS and DHIS2_MONTHLY_USER/PASS from .env if they are
set; otherwise asks for them (the password is typed, never saved).

For each instance it checks the login and the server version, then lists the
datasets and org-unit levels. It pulls the last two weeks (weekly instance) or
the last closed month (monthly instance) for every dataset.

It writes data/raw/dhis2_test_report.md (send this back) plus catalogs of
datasets, data elements and category option combos.
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
INSTANCES = {
    "weekly": "https://speeddhis-weekly.ecews.org",
    "monthly": "https://speeddhis-monthly.ecews.org",
}


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def creds(name: str, shared: dict) -> tuple[str, str]:
    u = os.environ.get(f"DHIS2_{name.upper()}_USER") or shared.get("user")
    p = os.environ.get(f"DHIS2_{name.upper()}_PASS") or shared.get("pass")
    if not u:
        u = input(f"DHIS2 username for {name}: ").strip()
    if not p:
        p = getpass.getpass(f"DHIS2 password for {u} ({name}): ")
    shared.setdefault("user", u)
    shared.setdefault("pass", p)
    return u, p


def get(s: requests.Session, base: str, path: str, **params):
    t0 = time.time()
    r = s.get(f"{base}/api/{path}", params=params, timeout=300)
    r.raise_for_status()
    return r.json(), time.time() - t0


def test_instance(name: str, base: str, user: str, pwd: str) -> dict:
    s = requests.Session()
    s.auth = (user, pwd)
    s.headers["Accept"] = "application/json"
    out = {"instance": name, "url": base, "ok": False}
    try:
        info, _ = get(s, base, "system/info")
        me, _ = get(s, base, "me", fields="username,organisationUnits[id,name,level]")
        out.update(version=info.get("version"), server_date=info.get("serverDate"),
                   last_analytics=info.get("lastAnalyticsTableSuccess") or info.get("lastAnalyticsTableGeneration"),
                   user=me.get("username"), roots=me.get("organisationUnits", []), ok=True)
    except requests.HTTPError as e:
        out["error"] = f"HTTP {e.response.status_code} on login — check username/password or account access"
        return out
    except requests.RequestException as e:
        out["error"] = f"Cannot reach server: {e.__class__.__name__}: {e}"
        return out

    ds, _ = get(s, base, "dataSets", fields="id,name,code,periodType", paging="false")
    ds = pd.DataFrame(ds["dataSets"])
    lv, _ = get(s, base, "organisationUnitLevels", fields="level,name", paging="false")
    levels = sorted(lv["organisationUnitLevels"], key=lambda x: x["level"])
    counts = {}
    for l in levels:
        js, _ = get(s, base, "organisationUnits", filter=f"level:eq:{l['level']}", fields="id", pageSize=1)
        counts[l["level"]] = js.get("pager", {}).get("total")
    de, _ = get(s, base, "dataElements", fields="id,name,shortName,code", paging="false")
    coc, _ = get(s, base, "categoryOptionCombos", fields="id,name", paging="false")
    de = pd.DataFrame(de["dataElements"]).set_index("id")
    coc = pd.DataFrame(coc["categoryOptionCombos"]).set_index("id")["name"]
    RAW.mkdir(parents=True, exist_ok=True)
    ds.to_csv(RAW / f"dhis2_catalog_{name}_datasets.csv", index=False)
    de.reset_index().to_csv(RAW / f"dhis2_catalog_{name}_dataelements.csv", index=False)
    coc.reset_index().to_csv(RAW / f"dhis2_catalog_{name}_catcombos.csv", index=False)
    out.update(levels=[dict(l, count=counts.get(l["level"])) for l in levels], n_datasets=len(ds))

    today = dt.date.today()
    if name == "weekly":
        start, end = today - dt.timedelta(days=today.weekday() + 14), today
    else:
        first = today.replace(day=1)
        end = first - dt.timedelta(days=1)
        start = end.replace(day=1)
    pulls = []
    for _, d in ds.iterrows():
        row = dict(dataset=d["name"], id=d["id"], periodType=d.get("periodType"), values=0)
        try:
            vals = []
            secs = 0.0
            for root in out["roots"]:
                js, t = get(s, base, "dataValueSets", dataSet=d["id"], orgUnit=root["id"], children="true",
                            startDate=start.isoformat(), endDate=end.isoformat())
                vals += js.get("dataValues", [])
                secs += t
            row.update(values=len(vals), seconds=round(secs, 1))
            if vals:
                v = pd.DataFrame(vals)
                row["periods"] = ", ".join(sorted(v["period"].unique())[-4:])
                row["facilities"] = int(v["orgUnit"].nunique())
                names = v["dataElement"].map(de["name"]).fillna(v["dataElement"])
                row["top_data_elements"] = "; ".join(names.value_counts().index[:6])
                row["sample_disaggs"] = "; ".join(v["categoryOptionCombo"].map(coc).fillna("").drop_duplicates().head(5))
        except requests.RequestException as e:
            row["error"] = str(e)[:160]
        pulls.append(row)
        print(f"  [{name}] {d['name'][:50]:50} {row.get('values', 0):>7} values")
    out.update(window=f"{start} to {end}", pulls=pulls)
    return out


def report(results: list[dict]) -> str:
    L = [f"# SPEED DHIS2 connection test — {dt.datetime.now():%Y-%m-%d %H:%M}", ""]
    for r in results:
        L += [f"## {r['instance'].title()} instance — {r['url']}"]
        if not r["ok"]:
            L += [f"**FAILED:** {r.get('error')}", ""]
            continue
        L += [f"- Logged in as **{r['user']}** · DHIS2 {r['version']} · server date {r['server_date']}",
              f"- Last analytics run: {r.get('last_analytics')}",
              f"- User org units: {', '.join(o['name'] for o in r['roots'])}",
              "- Org unit levels: " + ", ".join(f"{l['level']}={l['name']} ({l['count']})" for l in r["levels"]),
              f"- Datasets: {r['n_datasets']} · test window {r['window']}", "",
              "| Dataset | Period type | Values | Facilities | Periods | Top data elements | Sample disaggs |",
              "|---|---|---|---|---|---|---|"]
        for p in sorted(r["pulls"], key=lambda x: -x.get("values", 0)):
            L.append(f"| {p['dataset']} (`{p['id']}`) | {p.get('periodType', '')} | {p.get('values', 0)} | {p.get('facilities', '')} | "
                     f"{p.get('periods', '')} | {p.get('top_data_elements', p.get('error', ''))} | {p.get('sample_disaggs', '')} |")
        L.append("")
    return "\n".join(L)


def main():
    load_env()
    shared: dict = {}
    results = []
    for name, base in INSTANCES.items():
        u, p = creds(name, shared)
        print(f"Testing {name} instance as {u} …")
        results.append(test_instance(name, base, u, p))
    RAW.mkdir(parents=True, exist_ok=True)
    text = report(results)
    (RAW / "dhis2_test_report.md").write_text(text, encoding="utf-8")
    (RAW / "dhis2_test_report.json").write_text(json.dumps(results, indent=1, default=str))
    print("\n" + text)
    print(f"\nReport written to {RAW / 'dhis2_test_report.md'}")
    sys.exit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
