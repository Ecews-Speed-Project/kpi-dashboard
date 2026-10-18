"""DHIS2 extraction for the SPEED weekly and monthly instances.

Uses /api/dataValueSets (raw values, available the moment they are saved)
rather than analytics or favourites, which only refresh when the analytics
job runs. Credentials come from environment variables, never from config.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from functools import cached_property
from pathlib import Path

import pandas as pd
import requests


class DHIS2:
    def __init__(self, base_url: str, user_env: str, pass_env: str, timeout: int = 180):
        user, pwd = os.environ.get(user_env), os.environ.get(pass_env)
        if not user or not pwd:
            raise RuntimeError(f"Set {user_env} and {pass_env} in .env")
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.auth = (user, pwd)
        self.s.headers["Accept"] = "application/json"
        self.timeout = timeout

    def get(self, path: str, **params):
        r = self.s.get(f"{self.base}/api/{path.lstrip('/')}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---------- metadata ---------------------------------------------------
    def datasets(self) -> pd.DataFrame:
        js = self.get("dataSets", fields="id,name,code,periodType", paging="false")
        return pd.DataFrame(js["dataSets"])

    @cached_property
    def data_elements(self) -> pd.DataFrame:
        js = self.get("dataElements", fields="id,name,shortName,code", paging="false")
        return pd.DataFrame(js["dataElements"]).set_index("id")

    @cached_property
    def category_option_combos(self) -> pd.Series:
        js = self.get("categoryOptionCombos", fields="id,name", paging="false")
        return pd.DataFrame(js["categoryOptionCombos"]).set_index("id")["name"]

    def org_units(self, level: int) -> pd.DataFrame:
        js = self.get("organisationUnits", fields="id,name,code,level,ancestors[id,name,level]",
                      filter=f"level:eq:{level}", paging="false")
        return pd.DataFrame(js["organisationUnits"])

    def root_org_units(self) -> list[str]:
        js = self.get("me", fields="organisationUnits[id,name]")
        return [o["id"] for o in js.get("organisationUnits", [])]

    def favourite_data_elements(self, visualization_id: str) -> list[str]:
        """Optional: read a DHIS2 favourite's data-element selection so the list
        of what to pull can be maintained in DHIS2 instead of config."""
        js = self.get(f"visualizations/{visualization_id}",
                      fields="dataDimensionItems[dataElement[id],dataElementOperand[id]]")
        out = []
        for item in js.get("dataDimensionItems", []):
            if item.get("dataElement"):
                out.append(item["dataElement"]["id"])
            elif item.get("dataElementOperand"):
                out.append(item["dataElementOperand"]["id"].split(".")[0])
        return sorted(set(out))

    # ---------- data -------------------------------------------------------
    def _get_retry(self, path: str, tries: int = 5, **params):
        """Retry dropped connections / timeouts / truncated responses; HTTP errors
        (e.g. 409 no access) are not retried."""
        for i in range(tries):
            try:
                return self.get(path, **params)
            except requests.HTTPError:
                raise
            except requests.RequestException as e:
                if i == tries - 1:
                    raise
                wait = 20 * (i + 1)
                print(f"    retry {i + 1} after {type(e).__name__} ({wait}s)", flush=True)
                time.sleep(wait)

    checkpoint_dir: Path | None = None  # set by the caller: finished chunks are saved here so a rerun resumes

    def _chunk(self, dataset: str, ou: str, a: dt.date, b: dt.date) -> pd.DataFrame:
        f = None
        if self.checkpoint_dir is not None:
            host = self.base.split("//")[-1].split(".")[0]
            f = self.checkpoint_dir / f"{host}_{dataset}_{ou}_{a}_{b}.parquet"
            if f.exists() and time.time() - f.stat().st_mtime < 12 * 3600:
                return pd.read_parquet(f)
        js = self._get_retry("dataValueSets", dataSet=dataset, orgUnit=ou, children="true",
                             startDate=a.isoformat(), endDate=b.isoformat())
        df = pd.DataFrame(js.get("dataValues", []))
        if f is not None:
            f.parent.mkdir(parents=True, exist_ok=True)
            df.astype(str).to_parquet(f, index=False)
        return df

    def data_values(self, dataset: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """One request per root org unit per month: a 13-month pull of a big
        dataset in one go times out on the server."""
        frames = []
        roots = self.root_org_units()
        chunks, d = [], dt.date(start.year, start.month, 1)
        while d <= end:
            nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
            # DHIS2 returns periods lying wholly inside the dates: overlap by 6 days so a
            # week spanning two months is caught; duplicates are dropped below
            chunks.append((max(d - dt.timedelta(days=6), start), min(nxt + dt.timedelta(days=5), end)))
            d = nxt
        for ou in roots:
            for a, b in chunks:
                frames.append(self._chunk(dataset, ou, a, b))
        n = sum(len(f) for f in frames)
        print(f"  {self.base.split('//')[-1]} {dataset}: {n:,} values ({len(chunks)} months x {len(roots)} org units)", flush=True)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            return df
        return df.drop_duplicates(["dataElement", "period", "orgUnit", "categoryOptionCombo", "attributeOptionCombo"])


def facility_lookup(client: DHIS2, level_state: int, level_lga: int, level_facility: int,
                    match: str = "name") -> pd.DataFrame:
    def anc(row, lvl, field="name"):
        for a in row["ancestors"]:
            if a.get("level") == lvl:
                return a[field]
        return None

    ous = client.org_units(level_facility)
    # Sub-facilities (one level down) report too: count them under their facility
    sub = client.org_units(level_facility + 1)
    if len(sub):
        parent = ous.set_index("id")
        sub["pid"] = sub.apply(lambda r: anc(r, level_facility, "id"), axis=1)
        sub = sub[sub["pid"].isin(parent.index)]
        for col in ("name", "code", "ancestors"):
            if col in parent:
                sub[col] = sub["pid"].map(parent[col])
        ous = pd.concat([ous, sub.drop(columns="pid")], ignore_index=True)

    ous["state"] = ous.apply(lambda r: anc(r, level_state), axis=1)
    ous["lga"] = ous.apply(lambda r: anc(r, level_lga), axis=1)
    key_src = ous[match] if match in ous else ous["name"]
    ous["facility_key"] = key_src.fillna(ous["name"]).astype(str).str.strip().str.lower()
    return ous.rename(columns={"id": "orgUnit", "name": "facility"})[
        ["orgUnit", "facility_key", "facility", "lga", "state"]]


def site_types(client: DHIS2, level_facility: int) -> dict:
    """orgUnit id -> 'Facility' (member of DHIS2's 'Health Facility Group') or 'Community'
    (other reporting sites). Used for the PMTCT facility / community split, as in the deck."""
    out = {}
    for lvl in (level_facility, level_facility + 1):
        js = client.get("organisationUnits", fields="id,organisationUnitGroups[name]", filter=f"level:eq:{lvl}", paging="false")
        for o in js.get("organisationUnits", []):
            grp = " ".join(g.get("name", "") for g in o.get("organisationUnitGroups", [])).lower()
            out[o["id"]] = "Facility" if "health facility" in grp else "Community"
    return out


def clean_state(name: str | None) -> str | None:
    """'de Delta State' -> 'Delta' (the DHIS2 names carry a prefix and suffix)."""
    if not isinstance(name, str) or not name:
        return None
    for s in ("Delta", "Ekiti", "Osun"):
        if s.lower() in name.lower():
            return s
    return name


def extract_instance(client: DHIS2, tables: dict, which: str, start: dt.date, end: dt.date,
                     fac: pd.DataFrame, dataname_field: str = "name", community: set | None = None) -> pd.DataFrame:
    """Pull every configured table from one instance into the long format
    expected by period_merge. community: org units outside the Health Facility group; on the monthly
    instance their PMTCT rows are community PMTCT (table PMTCT_COMM), as the cPMTCT favourite."""
    de = client.data_elements
    coc = client.category_option_combos
    out = []
    cache: dict[str, pd.DataFrame] = {}  # tables may share a dataset (PMTCT/PMTCT_EID, TX_TB/AHD)
    for table, ids in tables.items():
        ds_ids = ids.get(which) or []
        if isinstance(ds_ids, str):
            ds_ids = [ds_ids]
        frames = []
        for ds in ds_ids:
            if ds not in cache:
                cache[ds] = client.data_values(ds, start, end)
            frames.append(cache[ds])
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if raw.empty:
            continue
        raw = raw.drop_duplicates(["dataElement", "period", "orgUnit", "categoryOptionCombo", "attributeOptionCombo"])
        raw["value"] = pd.to_numeric(raw["value"], errors="coerce")
        raw = raw.dropna(subset=["value"])
        raw["dataname"] = raw["dataElement"].map(de[dataname_field]).fillna(raw["dataElement"])
        raw["disagg"] = raw["categoryOptionCombo"].map(coc).fillna("")
        raw = raw.merge(fac, on="orgUnit", how="left")
        raw["table"] = table
        if community and which == "monthly" and table == "PMTCT":
            raw.loc[raw["orgUnit"].isin(community), "table"] = "PMTCT_COMM"
        out.append(raw[["table", "period", "facility_key", "state", "lga", "facility",
                        "dataname", "disagg", "value"]])
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["table", "period", "facility_key", "state", "lga", "facility", "dataname", "disagg", "value"])
    df["state"] = df["state"].map(clean_state)
    return df
