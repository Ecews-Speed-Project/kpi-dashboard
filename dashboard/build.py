"""Render the dashboard.

Live build (split=True): a small index.html plus data/<build>/*.json(.gz): core.json holds what the first
view needs (current figures); client lists, trends, calculation details and each past month's snapshot are
separate files the page fetches when a page needs them, so the first load stays small however much history
accumulates. Archived copies (and split=False builds) are one self-contained HTML file with the KPI JSON
embedded, so they open offline, can be emailed, or hosted on any web server.

live/index.html is overwritten on every run, so the same file (or the same
http://localhost address from serve_dashboard.py) always shows the latest
build. live/version.json lets an open page notice a new build. Each build is
also copied to archive/ with a timestamp (no client lists) as a .zip holding the .html (about 7x
smaller; double-click the zip, then open the page inside), thinned by thin_archive()."""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEEP_DAYS = 30      # archive: the last build of each day for this many days ...
KEEP_MONTHS = 24    # ... then the last build of each month for this many months


def thin_archive(files: list[Path], today: dt.date) -> list[Path]:
    """Archived builds to delete: keep every build from today, the last build of each day
    for KEEP_DAYS, then the last build of each month for KEEP_MONTHS."""
    def stamp(f):
        try:
            return dt.datetime.strptime(f.stem.split("_", 2)[2][:15], "%Y-%m-%d_%H%M")
        except ValueError:
            return None
    dated = sorted((s, f) for f in files if (s := stamp(f)))
    keep, last_day, last_month = set(), {}, {}
    for s, f in dated:
        last_day[s.date()] = f
        last_month[(s.year, s.month)] = f
    for s, f in dated:
        age = (today - s.date()).days
        if age <= 0:
            keep.add(f)
        elif age <= KEEP_DAYS and last_day[s.date()] == f:
            keep.add(f)
        elif last_month[(s.year, s.month)] == f and (today.year - s.year) * 12 + today.month - s.month < KEEP_MONTHS:
            keep.add(f)
    return [f for _, f in dated if f not in keep]


def _render(data: dict) -> str:
    """The page with its data in a <script type="application/json"> block. Phone browsers ran out of memory
    when the 50 MB of data was part of the script (compiled as code before it ran); JSON.parse reads it
    as data. The cube is stored by column (seven flat lists) instead of 1.3 million small lists."""
    html = (HERE / "template.html").read_text(encoding="utf-8")
    logo = HERE / "assets" / "ecews_logo.png"
    logo_uri = "data:image/png;base64," + base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""
    cube = data.get("cube") or {}
    if "rows" in cube:
        rows = cube["rows"]
        data = dict(data, cube={"dims": cube["dims"], "cols": [[r[i] for r in rows] for i in range(7)]})
    payload = json.dumps(data, default=str, separators=(",", ":")).replace("<", "\\u003c")   # nothing can close the block
    return html.replace("/*__DATA__*/null", payload).replace("__LOGO__", logo_uri)


# parts of a split build: name -> data keys (the cube's past-month snapshots are parts "snap:<period>")
LIST_PARTS = {"conc": ("conc_clients",), "appt": ("appt_clients",), "vl": ("vl_clients", "eac_clients"), "pbs": ("pbs_clients",)}
KEEP_BUILDS = 2       # data folders kept in live/data: the current build and the one before (pages still open on it)


def _cols(rows: list) -> list:
    return [[r[i] for r in rows] for i in range(7)]


def split_parts(data: dict) -> tuple[dict, dict]:
    """(core, parts): core = everything the first view needs, with the cube's current rows; parts = the rest."""
    cube = data["cube"]
    rows = cube["rows"] if "rows" in cube else [list(r) for r in zip(*cube["cols"])]
    per = cube["dims"]["period"]
    snap = [str(x)[:2] in ("SL", "SN") for x in per]
    current, snaps = [], {}
    for row in rows:
        (snaps.setdefault(per[row[4]], []) if snap[row[4]] else current).append(row)
    heavy = {k for ks in LIST_PARTS.values() for k in ks} | {"trend_series", "methods", "cube"}
    core = {k: v for k, v in data.items() if k not in heavy}
    core["cube"] = {"dims": cube["dims"], "cols": _cols(current)}
    parts = {}
    for name, keys in LIST_PARTS.items():
        got = {k: data[k] for k in keys if isinstance(data.get(k), dict) and data[k].get("rows")}
        if got:
            parts[name] = got
    for k, name in (("trend_series", "trends"), ("methods", "methods")):
        if data.get(k):
            parts[name] = {k: data[k]}
    for key, rs in sorted(snaps.items()):
        parts["snap:" + key] = {"cols": _cols(rs)}
    return core, parts


def _write_split(data: dict, out_dir: Path) -> dict:
    """data/<build>/<part>.json and .json.gz (written to a temporary folder, then swapped in); returns the manifest."""
    bid = re.sub(r"\D", "", str(data["meta"].get("generated_at", "")))[:12] or dt.datetime.now().strftime("%Y%m%d%H%M")
    root = out_dir / "data"
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{bid}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    core, parts = split_parts(data)
    files = {}
    for name, obj in [("core", core), *parts.items()]:
        fn = name.replace(":", "-") + ".json"
        raw = json.dumps(obj, default=str, separators=(",", ":")).encode("utf-8")
        (tmp / fn).write_bytes(raw)                                          # plain copy: any static server
        (tmp / (fn + ".gz")).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        files[name] = fn
    final = root / bid
    if final.exists():         # the same build rebuilt: never touch the folder the current page uses - new name
        bid = f"{bid}-{dt.datetime.now():%H%M%S}"
        final = root / bid
    os.replace(tmp, final)
    builds = sorted((d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name)
    for old in builds[:-KEEP_BUILDS] + [d for d in root.iterdir() if d.name.startswith(".") and d != tmp]:
        shutil.rmtree(old, ignore_errors=True)  # older builds, and temporary folders a failed run left behind
    return {"id": bid, "base": f"data/{bid}/", "core": files.pop("core"), "parts": files}


def read_page_data(html: str, base_dir: Path | None = None) -> dict:
    """The dashboard data back out of a built page (any layout), with the cube as rows again.
    A split build needs base_dir, the folder holding index.html and data/."""
    if '"manifest"' in html[:4000] or ('{"manifest"' in html):
        i = html.index('<script id="speed-data" type="application/json">') + len('<script id="speed-data" type="application/json">')
        man = json.loads(html[i:html.index("</script>", i)])["manifest"]
        folder = Path(base_dir or ".") / man["base"]
        load = lambda fn: json.loads((folder / fn).read_bytes()) if (folder / fn).is_file() else json.loads(gzip.decompress((folder / (fn + ".gz")).read_bytes()))
        data = load(man["core"])
        cols = data["cube"]["cols"]
        for name, fn in man["parts"].items():
            part = load(fn)
            if name.startswith("snap:"):
                cols = [a + b for a, b in zip(cols, part["cols"])]
            else:
                data.update(part)
        data["cube"] = {"dims": data["cube"]["dims"], "rows": [list(r) for r in zip(*cols)]}
        return data
    if '<script id="speed-data" type="application/json">' in html:
        i = html.index('<script id="speed-data" type="application/json">') + len('<script id="speed-data" type="application/json">')
        data = json.loads(html[i:html.index("</script>", i)])
    else:                                                       # pages built before 2026-10-21
        i = html.index("const D = ") + len("const D = ")
        data = json.loads(html[i:html.index(";\nconst SPEED", i)])
    cube = data.get("cube") or {}
    if "cols" in cube:
        data["cube"] = {"dims": cube["dims"], "rows": [list(r) for r in zip(*cube["cols"])]}
    return data


CLIENT_LISTS = ("pbs_clients", "conc_clients", "appt_clients", "vl_clients", "eac_clients")


def without_clients(data: dict, **flags) -> dict:
    """The dashboard data with every client-level list emptied (ART / hospital numbers, statuses);
    all figures, charts and tables stay. Used for archived copies and the shared (GitHub) build."""
    out = dict(data, meta=dict(data["meta"], **flags))
    for k in CLIENT_LISTS:
        if k in out:
            out[k] = dict(out[k], rows=[])
    return out


def build_shared(data: dict, out_dir: Path, split: bool = False) -> Path:
    """Figures-only dashboard (no client lists) for the public GitHub 'live' branch."""
    return build(without_clients(data, shared=True), out_dir, split=split)


def build(data: dict, out_dir: Path | None = None, archive_dir: Path | None = None, split: bool = False) -> Path:
    out_dir = Path(out_dir or HERE / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    if split:          # small page + data files fetched on demand (the data folder is swapped in before the page)
        html = _render({"manifest": _write_split(data, out_dir)})
    else:
        html = _render(data)
        shutil.rmtree(out_dir / "data", ignore_errors=True)

    # write to a temp file and swap, so a browser refresh never sees half a file
    path = out_dir / "index.html"
    tmp = out_dir / "index.html.tmp"
    tmp.write_text(html, encoding="utf-8")
    # precompressed copy for the web server (about 7x smaller): serve_dashboard.py, nginx gzip_static, ...
    gz, gz_tmp = out_dir / "index.html.gz", out_dir / "index.html.gz.tmp"
    gz_tmp.write_bytes(gzip.compress(html.encode("utf-8"), compresslevel=9, mtime=0))
    os.replace(gz_tmp, gz)
    os.replace(tmp, path)
    (out_dir / "version.json").write_text(json.dumps({"generated_at": data["meta"]["generated_at"],
                                                      "quarter": data["meta"]["quarter"],
                                                      "demo": data["meta"].get("demo", False),
                                                      "client_lists": not data["meta"].get("shared", False)}))
    if archive_dir:
        archive_dir = Path(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
        tag = "_DEMO" if data["meta"].get("demo") else ""
        # Archived copies keep every figure but no client identifiers: the
        # biometrics follow-up list lives only in live/index.html.
        arch = without_clients(data, archived=True)          # client-level lists stay in live\ only
        name = f"SPEED_dashboard_{stamp}{tag}"
        with zipfile.ZipFile(archive_dir / f"{name}.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.writestr(f"{name}.html", _render(arch))
        olds = list(archive_dir.glob("SPEED_dashboard_*.zip")) + list(archive_dir.glob("SPEED_dashboard_*.html"))
        for f in thin_archive(olds, dt.date.today()):
            f.unlink(missing_ok=True)
    return path
