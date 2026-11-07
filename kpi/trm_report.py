"""Technical review meeting (TRM) deck for one state or all SPEED, built when someone asks for it.

Figures come from the dashboard's own cube, so they follow its rule: monthly reports for months past the switch day
(the 13th of the next month), then weekly reports for the time after them. "Index week" is the latest weekly period.
Slides not filled yet are left out of the deck, so none of the template's sample numbers can reach a meeting.
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import re
import threading
import warnings
from pathlib import Path
from xml.sax.saxutils import escape

import openpyxl

from kpi.hts_report import Deck, _cells, num, rate, set_cell

STATES = ["Delta", "Ekiti", "Osun"]
NEED = {"hts_tst", "hts_tst_pos", "hts_tst_neg", "tx_new_q", "hivst_assisted", "hivst_unassisted", "hivst_reactive", "hivst_referred",
        "hivst_confirmed", "hivst_linked", "index_cases", "index_offered", "index_accepted", "index_elicited", "index_tst", "index_pos",
        "index_known_pos", "prep_offered", "prep_accepted", "prep_new", "prep_new_preg", "prep_new_bf", "prep_ct", "prep_ct_preg", "prep_ct_bf",
        "iit_q", "fit_offered", "fit_accepted", "fit_elicited", "fit_tested", "fit_pos", "fit_known_pos", "fit_linked", "pmtct_stat_den", "pmtct_stat_num", "pmtct_stat_pos", "pmtct_pos_known", "pmtct_pos_new", "pmtct_art", "pmtct_eid", "pmtct_eid_lt2", "pmtct_eid_pos", "hei_neg", "hei_art", "ahd_enrolled_n", "ahd_cd4_n", "ahd_cd4lt200_n", "ahd_who34_n", "ahd_confirmed_n", "ahd_lam_test_n", "ahd_lam_pos_n", "ahd_xpert_test_n", "ahd_xpert_pos_n", "ahd_tb_tx_n", "ahd_crag_test_n", "ahd_crag_pos_n", "ahd_csf_test_n", "ahd_csf_pos_n", "ahd_preempt_n", "tbs_screened", "tbs_presumptive", "tbs_gxp", "tbs_gxp_pos", "tbs_tested", "tbs_diagnosed", "tbs_tx_started", "tbs_cxr", "tbs_cxr_pos"}
DATED = {"tx_new_q", "iit_q", "tbs_screened", "tbs_presumptive", "tbs_gxp", "tbs_gxp_pos", "tbs_tested", "tbs_diagnosed", "tbs_tx_started", "tbs_cxr", "tbs_cxr_pos"}          # counted by date (EMR line lists), not by reporting period
# figures as of the line list date (no reporting period): treatment, continuity and biometrics
STOCK = {"tx_curr", "tx_curr_emr", "prev_tx_curr", "mmd_6", "mmd_3", "mmd_45", "mmd_lt3", "wf_prev", "wf_new", "wf_ti", "wf_rtt",
         "wf_dead", "wf_to", "wf_stopped", "wf_iit", "pbs_active", "pbs_baseline", "pbs_enrolled", "pbs_duplicate", "pbs_recapture",
         "pbs_match", "pbs_nomatch", "vl_eligible", "vl_result", "vl_suppressed", "vl_undetectable", "vl_llv", "vl_sample", "vl_gap_emr", "ndr_wf_prev", "ndr_wf_new", "ndr_wf_rtt", "ndr_wf_ti", "ndr_wf_dead", "ndr_wf_to", "ndr_wf_iit", "ndr_wf_curr"}
KEEP = list(range(1, 61))
KEEP.remove(3)                           # FY27 targets: a picture of the template's sample table
KEEP.remove(57)                          # how to edit that table's embedded workbook
BLANK = "[ __ ]"                         # not in the dashboard's data: for the team to fill in
STREAMS = [("Other PITC", "Other PITC"), ("PMTCT_ANC1", "ANC1"), ("INPATIENT", "Inpatient"), ("INDEX", "Index"), ("POST_ANC1", "Post ANC1"),
           ("VCT", "VCT"), ("TB", "TB Clinic"), ("EMERGENCY", "Emergency"), ("MOBILE", "Mobile"), ("PAEDIATRICS", "Paediatrics")]
UNDER15 = {"<1", "1-4", "5-9", "10-14"}
BANDS = [("0-4 years", {"<1", "1-4"}), ("5-9 years", {"5-9"}), ("10-14 years", {"10-14"}), ("15-19 years", {"15-19"}),
         ("20-24 years", {"20-24"}), ("25-29 years", {"25-29"}), ("30-34 years", {"30-34"}), ("35-39 years", {"35-39"}),
         ("40-44 years", {"40-44"}), ("45-49 years", {"45-49"}), ("50+", None)]
PREGNANT = {"ANC1", "Post ANC1"}         # left out where a slide says "excluding pregnant women"

_lock = threading.Lock()                 # one deck at a time


# ---------------------------------------------------------------- figures
def read_part(live: Path, part: str) -> dict:
    """One data file of the build (e.g. vl.json, which holds the EAC list); only aggregates leave this module."""
    page = live / "index.html"
    html = page.read_text(encoding="utf-8") if page.is_file() else gzip.decompress((live / "index.html.gz").read_bytes()).decode("utf-8")
    base = re.search(r'"base":"(data/[^"]+)"', html).group(1)
    plain = live / base / f"{part}.json"
    if not plain.is_file() and not plain.with_name(f"{part}.json.gz").is_file():
        return {}
    raw = plain.read_bytes() if plain.is_file() else gzip.decompress(plain.with_name(f"{part}.json.gz").read_bytes())
    return json.loads(raw)


def eac_cohort(eac: dict, dims: dict, state: str, start: str, end: str, ages: set | None = None) -> dict:
    """The dashboard's EAC cascade for clients whose unsuppressed result (1,000 or more) fell in [start, end]
    and who are still active, as on the EAC page."""
    out = dict(n=0, st=0, s2=0, s3=0, ext=0, fs=0, fu=0, sup=0, llv=0, uns=0, sw=0)
    if not eac:
        return out
    cols, dic = eac["cols"], eac.get("dict", {})
    at = {c: i + 3 for i, c in enumerate(cols)}           # each row starts with facility, sex and age
    val = lambda r, c: dic[c][r[at[c]]] if c in dic and isinstance(r[at[c]], int) and r[at[c]] < len(dic[c]) else r[at[c]]
    for r in eac["rows"]:
        if state and dims["facility"][r[0]][0] != state:
            continue
        if ages is not None and dims["age"][r[2]] not in ages:
            continue
        d_, v_ = str(val(r, "trigger_date") or ""), val(r, "trigger_vl")
        try:
            v_ = float(v_)
        except (TypeError, ValueError):
            continue
        if not (start <= d_ <= end) or v_ < 1000 or val(r, "status") != "Active":
            continue
        has = lambda c: bool(val(r, c))
        fu = str(val(r, "fu_status") or "")
        step = dict(n=True, st=has("s1"), s2=has("s2"), s3=has("s3"), ext=has("s4"), fs=has("fu_sample") or has("fu_date"), fu=has("fu_date"),
                    sup=fu == "Suppressed", llv=fu == "Low Level Viremia", uns=fu == "Unsuppressed", sw=val(r, "switched") == "Yes")
        for k, ok in step.items():
            out[k] += bool(ok)
    return out


def read_core(live: Path) -> dict:
    """The built figures alone (not the client lists), from a build with index.html or only index.html.gz."""
    page = live / "index.html"
    html = page.read_text(encoding="utf-8") if page.is_file() else gzip.decompress((live / "index.html.gz").read_bytes()).decode("utf-8")
    base = re.search(r'"base":"(data/[^"]+)"', html).group(1)
    plain = live / base / "core.json"
    raw = plain.read_bytes() if plain.is_file() else gzip.decompress(plain.with_name("core.json.gz").read_bytes())
    return json.loads(raw)


class Figures:
    def __init__(self, data: dict, state: str = ""):
        self.meta, self.periods = data["meta"], data["periods"]
        c = data["cube"]
        dims, cols = c["dims"], c["cols"]
        names = dims["measure"]
        wanted = {i for i, n in enumerate(names) if n in NEED}
        stock = {i for i, n in enumerate(names) if n in STOCK or n == "tx_curr"}
        self.stock: dict[str, float] = {}
        self.stock_k: dict[tuple, float] = {}       # (measure, sex, age, facility) for the VL tables
        self.fac_names = [f[2] for f in dims["facility"]]
        self.dims = dims
        fac_ok = [not state or f[0] == state for f in dims["facility"]]
        self.t: dict[tuple, float] = {}
        self.sites: dict[tuple, float] = {}      # (measure, period, facility): for testing points and linkage gaps
        for f, s, a, mo, p, m, v in zip(*cols):
            if m in stock and fac_ok[f] and not self.periods[p].get("kind"):
                self.stock[names[m]] = self.stock.get(names[m], 0) + v
                if names[m].startswith(("vl_", "ndr_wf_")) or names[m] == "tx_curr":
                    k = (names[m], dims["sex"][s], dims["age"][a], f)
                    self.stock_k[k] = self.stock_k.get(k, 0) + v
                continue
            if m in wanted and fac_ok[f] and self.periods[p].get("kind") in ("M", "W", "D"):
                k = (names[m], p, dims["sex"][s], dims["age"][a], dims["mod"][mo])
                self.t[k] = self.t.get(k, 0) + v
                if names[m] in ("hts_tst", "hts_tst_pos", "tx_new_q", "iit_q"):
                    s_k = (names[m], p, dims["facility"][f][2])
                    self.sites[s_k] = self.sites.get(s_k, 0) + v
        fy = str(self.meta["fy_start"])[:7]
        self.q_start, self.q_end = str(self.meta["q_start"]), str(self.meta["q_end"])
        self.fy_start = str(self.meta["fy_start"])
        report = [i for i, p in enumerate(self.periods) if p.get("kind") in ("M", "W") and str(p.get("month") or "") >= fy]
        used = {k[1] for k in self.t if k[0] == "hts_tst"}
        self.points = sorted((i for i in report if i in used), key=lambda i: (self.periods[i]["start"], self.periods[i]["kind"]))
        weeks = [i for i in self.points if self.periods[i]["kind"] == "W"]
        self.index_week = weeks[-1] if weeks else None

    def _in_fy(self, p: dict) -> bool:
        if p["kind"] == "D":
            return str(p["start"]) >= self.fy_start
        return str(p.get("month") or "") >= self.fy_start[:7]

    def _in_q(self, p: dict) -> bool:
        if p["kind"] == "D":
            return self.q_start <= str(p["start"]) <= self.q_end
        return self.q_start[:7] <= str(p.get("month") or "") <= self.q_end[:7]

    def testing_points(self, test) -> int:
        return len({f for (m, p, f), v in self.sites.items() if m == "hts_tst" and v and test(self.periods[p])})

    def linkage_gaps(self, top: int = 3) -> list[tuple[str, float]]:
        """Facilities with the most positives not yet on ART in the index week."""
        if self.index_week is None:
            return []
        w = self.periods[self.index_week]
        pos, tx = {}, {}
        for (m, p, f), v in self.sites.items():
            if m == "hts_tst_pos" and p == self.index_week:
                pos[f] = pos.get(f, 0) + v
            elif m == "tx_new_q" and self.periods[p]["kind"] == "D" and str(w["start"]) <= str(self.periods[p]["start"]) <= str(w["end"]):
                tx[f] = tx.get(f, 0) + v
        gaps = sorted(((f, v - tx.get(f, 0)) for f, v in pos.items() if v - tx.get(f, 0) > 0), key=lambda x: (-x[1], x[0]))
        return gaps[:top]

    def st(self, measure, sex=None, ages=None, fac=None) -> float:
        return sum(v for (m, s, a, f), v in self.stock_k.items()
                   if m == measure and (sex is None or s == sex) and (ages is None or a in ages) and (fac is None or f == fac))

    def top_sites(self, measure, test, top: int = 5) -> list[tuple[str, float]]:
        per = {}
        for (m, p, f), v in self.sites.items():
            if m == measure and test(self.periods[p]):
                per[f] = per.get(f, 0) + v
        return sorted(((f, v) for f, v in per.items() if v), key=lambda x: (-x[1], x[0]))[:top]

    def _sum(self, measure, test, sex=None, ages=None, not_mods=None, mod=None) -> float:
        tot = 0.0
        for (m, p, s, a, mo), v in self.t.items():
            if m == measure and (sex is None or s == sex) and (ages is None or a in ages) and (not not_mods or mo not in not_mods) and (mod is None or mo == mod) and test(self.periods[p]):
                tot += v
        return tot

    def fy(self, measure, **kw) -> float:
        return self._sum(measure, self._in_fy, **kw)

    def quarter(self, measure, **kw) -> float:
        return self._sum(measure, self._in_q, **kw)

    def at(self, i, measure, **kw) -> float:
        """One reporting period; a date-based measure counts the dates inside it."""
        if i is None:
            return 0.0
        w = self.periods[i]
        if measure in DATED:
            return self._sum(measure, lambda p: p["kind"] == "D" and str(w["start"]) <= str(p["start"]) <= str(w["end"]), **kw)
        return self._sum(measure, lambda p: p is w, **kw)

    def week(self, measure, **kw) -> float:
        return self.at(self.index_week, measure, **kw)

    def label(self, i) -> str:
        p = self.periods[i]
        if p["kind"] == "M":
            return dt.date.fromisoformat(str(p["start"])).strftime("%b")
        return "W" + p["key"].split("W")[1]

    def week_label(self) -> str:
        if self.index_week is None:
            return "no weekly reports yet"
        p = self.periods[self.index_week]
        return f"Week {p['key'].split('W')[1]} - {p['start']} - {p['end']}"


def pct0(a, b) -> str:
    return f"{a / b:.0%}" if b else "—"


# ---------------------------------------------------------------- template editing
def replace_texts(xml: str, pairs: list[tuple[str, str]], after: str | None = None) -> str:
    """Replace text runs in the order they appear (from the run `after`, if given); a run missing from the template
    is an error, not a silent gap."""
    pos = xml.index(f"<a:t>{escape(after)}</a:t>") if after else 0
    for old, new in pairs:
        needle = f"<a:t>{escape(old)}</a:t>"
        at = xml.find(needle, pos)
        if at < 0:
            raise ValueError(f"template text not found: {old}")
        rep = f"<a:t>{escape(new)}</a:t>"
        xml = xml[:at] + rep + xml[at + len(needle):]
        pos = at + len(rep)
    return xml


def fill_tables(xml: str, tables: list[list[list[str]]], header_rows: int = 2) -> str:
    """Every table in turn: data rows replaced cell by cell, formatting kept."""
    found = list(re.finditer(r"<a:tbl>.*?</a:tbl>", xml, re.S))
    for m, values in reversed(list(zip(found, tables))):
        tbl = m.group(0)
        rows = re.findall(r"<a:tr\b.*?</a:tr>", tbl, re.S)
        for row, vals in zip(rows[header_rows:], values):
            new = row
            for tc, v in zip(_cells(row), vals):
                if v is not None:
                    new = new.replace(tc, set_cell(tc, str(v), 1000), 1)
            tbl = tbl.replace(row, new, 1)
        xml = xml[:m.start()] + tbl + xml[m.end():]
    return xml


def colour_yield_grid(xml: str) -> str:
    """The template's grid is shaded by its sample values: shade the 8 yield cells by the real ones.
    Each cell is a filled rounded box with a text box on top of it."""
    shapes = list(re.finditer(r"<p:sp>.*?</p:sp>", xml, re.S))
    text_of = lambda m: "".join(re.findall(r"<a:t>([^<]*)</a:t>", m.group(0)))
    cells = [i for i, m in enumerate(shapes) if re.fullmatch(r"\d+\.\d%|—", text_of(m))][-8:]
    for i in reversed(cells):
        text = text_of(shapes[i])
        v = float(text[:-1]) if text != "—" else 0
        fill, ink = ("0B6B50", "FFFFFF") if v >= 3 else ("8CC5AE", "10372B") if v >= 1 else ("DCEBE5", "10372B")
        words = re.sub(r"(<a:rPr\b[^>]*><a:solidFill><a:srgbClr val=\")[0-9A-Fa-f]{6}", rf"\g<1>{ink}", shapes[i].group(0))
        xml = xml[:shapes[i].start()] + words + xml[shapes[i].end():]
        box = shapes[i - 1]
        xml = xml[:box.start()] + re.sub(r'<a:srgbClr val="[0-9A-Fa-f]{6}"/>', f'<a:srgbClr val="{fill}"/>', box.group(0)) + xml[box.end():]
    return xml


def clean_notes(deck: Deck) -> None:
    """The template's notes call its figures dummy data; these are real, so that sentence goes."""
    for name in [n for n in deck.parts if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]:
        xml = deck.text(name)
        new = re.sub(r"\s*All figures on this slide are illustrative[^<]*", "", xml)     # the text only: a note must keep its paragraph
        if new != xml:
            deck.put(name, new)


def _ref(f: str):
    m = re.match(r"(?:'?([^'!]+)'?!)?\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?", f)
    return m.group(1) or "Sheet1", m.group(2), int(m.group(3))


def set_chart(deck: Deck, name: str, cats: list[str], series: list[list]) -> None:
    """New categories and values in the chart and in the worksheet behind it, so Edit Data shows the same numbers.
    A value given as (numerator, denominator) is a rate, written on the scale the template's chart uses."""
    path = f"ppt/charts/{name}.xml"
    xml = deck.text(path)
    target = re.search(r'Target="([^"]+\.xlsx)"', deck.text(f"ppt/charts/_rels/{name}.xml.rels")).group(1)
    book_path = "ppt/" + target.replace("../", "")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # the template's small worksheets have no style sheet
        wb = openpyxl.load_workbook(io.BytesIO(deck.parts[book_path]))
    for ws in wb.worksheets:
        ws.tables.clear()
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cell.value = None
    sers = list(re.finditer(r"<c:ser>.*?</c:ser>", xml, re.S))
    n = len(cats)
    for m, vals in reversed(list(zip(sers, series))):
        s = m.group(0)
        tx = re.search(r"<c:tx>.*?</c:tx>", s, re.S)
        tx = tx.group(0) if tx else ""
        tx_f = (re.search(r"<c:f>(.*?)</c:f>", tx) or [None, None])[1]          # a series name may be typed in, not a cell
        tx_v = (re.search(r"<c:v>(.*?)</c:v>", tx) or [None, ""])[1]
        cat_f = (re.search(r"<c:cat>.*?<c:f>(.*?)</c:f>", s, re.S) or [None, None])[1]
        val_f = (re.search(r"<c:val>.*?<c:f>(.*?)</c:f>", s, re.S) or [None, None])[1]
        fmt = (re.search(r"<c:val>.*?<c:formatCode>(.*?)</c:formatCode>", s, re.S) or [None, "General"])[1]
        old = [float(v) for v in re.findall(r"<c:v>([^<]*)</c:v>", re.search(r"<c:val>.*?</c:val>", s, re.S).group(0))]
        scale = 1 if (old and max(old) <= 1.5) or "%" in fmt else 100
        vals = [(round(v[0] / v[1] * scale, 3) if v[1] else None) if isinstance(v, tuple) else v for v in vals]
        ws = wb[_ref(cat_f or val_f or "Sheet1!$A$1")[0]] if _ref(cat_f or val_f or "Sheet1!$A$1")[0] in wb.sheetnames else wb.worksheets[0]
        if tx_f:
            _, t_col, t_row = _ref(tx_f)
            ws[f"{t_col}{t_row}"] = tx_v.replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
        pts_c = f'<c:ptCount val="{n}"/>' + "".join(f'<c:pt idx="{k}"><c:v>{escape(c)}</c:v></c:pt>' for k, c in enumerate(cats))
        pts_v = f'<c:formatCode>{fmt}</c:formatCode><c:ptCount val="{n}"/>' + "".join(f'<c:pt idx="{k}"><c:v>{v}</c:v></c:pt>' for k, v in enumerate(vals) if v is not None)
        if cat_f:                                         # categories read from the sheet
            sheet, c_col, c_row = _ref(cat_f)
            for k in range(n):
                ws[f"{c_col}{c_row + k}"] = cats[k]
            cat = f'<c:cat><c:strRef><c:f>{sheet}!${c_col}${c_row}:${c_col}${c_row + n - 1}</c:f><c:strCache>{pts_c}</c:strCache></c:strRef></c:cat>'
        else:                                             # categories typed into the chart
            cat = f'<c:cat><c:strLit>{pts_c}</c:strLit></c:cat>'
        if val_f:
            sheet, v_col, v_row = _ref(val_f)
            for k in range(n):
                ws[f"{v_col}{v_row + k}"] = vals[k]       # None stays empty: not in the data, so no bar and no made-up zero
            val = f'<c:val><c:numRef><c:f>{sheet}!${v_col}${v_row}:${v_col}${v_row + n - 1}</c:f><c:numCache>{pts_v}</c:numCache></c:numRef></c:val>'
        else:
            val = f'<c:val><c:numLit>{pts_v}</c:numLit></c:val>'
        s = re.sub(r"<c:cat>.*?</c:cat>", lambda _: cat, s, flags=re.S)
        s = re.sub(r"<c:val>.*?</c:val>", lambda _: val, s, flags=re.S)
        s = re.sub(r'<c:dPt><c:idx val="(\d+)"/>.*?</c:dPt>', lambda d: d.group(0) if int(d.group(1)) < n else "", s, flags=re.S)
        s = re.sub(r'<c:dLbl><c:idx val="(\d+)"/>.*?</c:dLbl>', lambda d: d.group(0) if int(d.group(1)) < n else "", s, flags=re.S)
        xml = xml[:m.start()] + s + xml[m.end():]
    deck.put(path, xml)
    out = io.BytesIO()
    wb.save(out)
    deck.parts[book_path] = out.getvalue()


def set_chartex(deck: Deck, name: str, cats: list[str], vals: list[float]) -> None:
    """The newer chart type (funnel): categories and values, in the chart and in the worksheet behind it."""
    path = f"ppt/charts/{name}.xml"
    xml = deck.text(path)
    n = len(cats)
    cat_f = re.search(r"<cx:strDim[^>]*><cx:f[^>]*>(.*?)</cx:f>", xml, re.S).group(1)
    val_f = re.search(r"<cx:numDim[^>]*><cx:f[^>]*>(.*?)</cx:f>", xml, re.S).group(1)
    sheet, c_col, c_row = _ref(cat_f)
    _, v_col, v_row = _ref(val_f)
    fmt = re.search(r'<cx:numDim.*?formatCode="([^"]*)"', xml, re.S).group(1)
    cat = (f'<cx:strDim type="cat"><cx:f>{sheet}!${c_col}${c_row}:${c_col}${c_row + n - 1}</cx:f><cx:lvl ptCount="{n}">'
           + "".join(f'<cx:pt idx="{k}">{escape(c)}</cx:pt>' for k, c in enumerate(cats)) + "</cx:lvl></cx:strDim>")
    val = (f'<cx:numDim type="val"><cx:f>{sheet}!${v_col}${v_row}:${v_col}${v_row + n - 1}</cx:f><cx:lvl ptCount="{n}" formatCode="{fmt}">'
           + "".join(f'<cx:pt idx="{k}">{v}</cx:pt>' for k, v in enumerate(vals) if v is not None) + "</cx:lvl></cx:numDim>")
    xml = re.sub(r"<cx:strDim.*?</cx:strDim>", lambda _: cat, xml, count=1, flags=re.S)
    xml = re.sub(r"<cx:numDim.*?</cx:numDim>", lambda _: val, xml, count=1, flags=re.S)
    xml = re.sub(r'<cx:dataPt idx="(\d+)".*?</cx:dataPt>|<cx:dataPt idx="(\d+)"[^>]*/>', lambda d: d.group(0) if int(d.group(1) or d.group(2)) < n else "", xml, flags=re.S)
    deck.put(path, xml)
    target = re.search(r'Target="([^"]+\.xlsx)"', deck.text(f"ppt/charts/_rels/{name}.xml.rels"))
    if not target:                     # this chart keeps its numbers in itself, with no worksheet inside the deck
        return
    book_path = "ppt/" + target.group(1).replace("../", "")
    if book_path not in deck.parts:     # a link to a workbook on the template author's computer, not inside the deck
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(deck.parts[book_path]))
    for ws in wb.worksheets:
        ws.tables.clear()
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cell.value = None
    ws = wb[sheet] if sheet in wb.sheetnames else wb.worksheets[0]
    for k in range(n):
        ws[f"{c_col}{c_row + k}"] = cats[k]
        ws[f"{v_col}{v_row + k}"] = vals[k]
    out = io.BytesIO()
    wb.save(out)
    deck.parts[book_path] = out.getvalue()


def drop_chartex_extras(xml: str, rels: str, keep: str) -> tuple[str, str]:
    """Keep one chart of the newer type on a slide: the template's empty copies go, and so do the stored pictures of
    these charts (older viewers would show their sample numbers)."""
    ids = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", rels):
        ids[re.search(r'Id="(rId\d+)"', tag).group(1)] = re.search(r'Target="([^"]+)"', tag).group(1)
    def block(m):
        b = m.group(0)
        rid = re.search(r'<cx:chart\b[^>]*r:id="(rId\d+)"', b)
        if not rid:
            return b
        if not ids.get(rid.group(1), "").endswith(f"/{keep}.xml"):
            return ""
        return re.sub(r"<mc:Fallback>.*?</mc:Fallback>", "", b, flags=re.S)
    xml = re.sub(r"<mc:AlternateContent\b.*?</mc:AlternateContent>", block, xml, flags=re.S)
    for tag in re.findall(r"<Relationship\b[^>]*>", rels):
        rid = re.search(r'Id="(rId\d+)"', tag).group(1)
        if ("/image" in tag or "/chartEx" in tag or "/chart\"" in tag) and f'"{rid}"' not in xml:
            rels = rels.replace(tag, "")
    return xml, rels


NUMBER = re.compile(r"[−-]?\d[\d,.]*%?")
LABEL = re.compile(r"days|[Mm]onth|Facility|site \d|FY\d|Wk|Q\d|scaled|top \d|^0\d$|^\[|(?i:score)|1,000|manifest|receipt|&lt;9\d%")


def blank_numbers(xml: str) -> str:
    """Every figure on a slide becomes [ __ ]; labels such as '1–14 days' or 'Facility 1' and the slide number stay."""
    def run(m):
        t = m.group(1)
        if LABEL.search(t) or not NUMBER.search(t):
            return m.group(0)
        return f"<a:t>{NUMBER.sub(BLANK, t)}</a:t>"
    parts = re.split(r"(<a:fld\b.*?</a:fld>)", xml, flags=re.S)        # slide-number fields stay as they are
    return "".join(p if p.startswith("<a:fld") else re.sub(r"<a:t>([^<]*)</a:t>", run, p) for p in parts)


def empty_bars(xml: str) -> str:
    """A progress bar drawn as a track with a fill on top, sized to the template's sample: the fill goes."""
    shapes = list(re.finditer(r"<p:sp>.*?</p:sp>", xml, re.S))
    def box(m):
        if re.search(r"<a:t>[^<]+</a:t>", m.group(0)):
            return None
        g = re.search(r'<a:off x="(\d+)" y="(\d+)"/><a:ext cx="(\d+)" cy="(\d+)"/>', m.group(0))
        return (g, *map(int, g.groups())) if g else None
    boxes = [box(m) for m in shapes]
    near = lambda u, v: abs(u - v) <= 40000
    for m, bx in reversed(list(zip(shapes, boxes))):
        if not bx:
            continue
        g, x, y, cx, cy = bx
        if any(o and o is not bx and near(o[1], x) and near(o[2], y) and near(o[4], cy) and o[3] > cx for o in boxes):
            new_ = m.group(0).replace(g.group(0), f'<a:off x="{x}" y="{y}"/><a:ext cx="0" cy="{cy}"/>', 1)
            xml = xml[:m.start()] + new_ + xml[m.end():]
    return xml


STATUS = ("08684E", "B7791F", "C0392B")      # the template's green, amber and red


def status_colours(xml: str, target: float = .95, gap: float = .05) -> str:
    """Percentages the template coloured by its sample: coloured again by the real value against the dashboard's
    target, as its rate status does (at or above target green, within the warning gap amber, below that red)."""
    def run(m):
        v = float(m.group(4)[:-1]) / 100
        new = STATUS[0] if v >= target else STATUS[1] if v >= target - gap else STATUS[2]
        return m.group(1) + new + m.group(3) + m.group(4) + "</a:t>"
    pat = r'(<a:r><a:rPr[^>]*>(?:(?!</a:rPr>).)*?<a:srgbClr val=")(08684E|B7791F|C0392B)("/>(?:(?!</a:rPr>).)*?</a:rPr><a:t>)(\d+%)</a:t>'
    return re.sub(pat, run, xml, flags=re.S)


def status_cells(xml: str, target: float = .95, gap: float = .05) -> str:
    """Table cells filled green, amber or red by the template's sample: filled again by the real value, or plain when empty."""
    def cell(m):
        tc = m.group(0)
        fill = re.search(r'(<a:solidFill><a:srgbClr val=")(08684E|B7791F|C0392B)("/></a:solidFill>)(\s*</a:tcPr>)', tc)
        if not fill:
            return tc
        text = "".join(re.findall(r"<a:t>([^<]*)</a:t>", tc))
        if re.fullmatch(r"\d+(\.\d+)?%", text):
            v = float(text[:-1]) / 100
            new = STATUS[0] if v >= target else STATUS[1] if v >= target - gap else STATUS[2]
            return tc[:fill.start()] + fill.group(1) + new + fill.group(3) + fill.group(4) + tc[fill.end():]
        tc = tc[:fill.start()] + fill.group(1) + "F6F9F8" + fill.group(3) + fill.group(4) + tc[fill.end():]
        return tc.replace('<a:srgbClr val="FFFFFF"/></a:solidFill></a:rPr>', '<a:srgbClr val="1F2A26"/></a:solidFill></a:rPr>')
    return re.sub(r"<a:tc\b.*?</a:tc>", cell, xml, flags=re.S)


def score_bars(xml: str, rows: list[tuple[float | None, str]]) -> str:
    """The targets slide draws each row's % of target as a bar on a track, a label and a status dot. rows: (share of
    target or None, colour) in row order. The template's sample bars go; each row gets a bar of its own value."""
    shapes = list(re.finditer(r"<p:sp>.*?</p:sp>", xml, re.S))
    geom = lambda s: re.search(r'<a:off x="(\d+)" y="(\d+)"/><a:ext cx="(\d+)" cy="(\d+)"/>', s)
    tracks = [m for m in shapes if 'val="E3EAE7"' in m.group(0) and 'prst="roundRect"' in m.group(0) and not re.search(r"<a:t>[^<]+</a:t>", m.group(0))]
    track_y = {int(geom(m.group(0)).group(2)) for m in tracks}
    fills = [m for m in shapes if m not in tracks and 'prst="roundRect"' in m.group(0) and not re.search(r"<a:t>[^<]+</a:t>", m.group(0))
             and geom(m.group(0)) and int(geom(m.group(0)).group(2)) in track_y]
    labels = [m for m in shapes if re.fullmatch(r"\d+%", "".join(re.findall(r"<a:t>([^<]*)</a:t>", m.group(0)))) and 'prst="rect"' in m.group(0)]
    dots = [m for m in shapes if 'prst="ellipse"' in m.group(0) and geom(m.group(0)) and int(geom(m.group(0)).group(3)) == 182880]
    edits = [(m.start(), m.end(), "") for m in fills]
    for k, (m, (share, colour)) in enumerate(zip(sorted(tracks, key=lambda m: int(geom(m.group(0)).group(2))), rows)):
        g = geom(m.group(0))
        if share:
            width = max(int(int(g.group(3)) * min(share, 1)), 20000)
            bar = (f'<p:sp><p:nvSpPr><p:cNvPr id="{9001 + k}" name="Score bar {k + 1}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm>'
                   f'<a:off x="{g.group(1)}" y="{g.group(2)}"/><a:ext cx="{width}" cy="{g.group(4)}"/></a:xfrm><a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
                   f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')
            edits.append((m.end(), m.end(), bar))
    for m, (share, colour) in zip(sorted(labels, key=lambda m: int(geom(m.group(0)).group(2))), rows):
        s = re.sub(r"<a:t>\d+%</a:t>", f"<a:t>{f'{share:.0%}' if share is not None else '—'}</a:t>", m.group(0))
        edits.append((m.start(), m.end(), re.sub(r'<a:srgbClr val="(08684E|B7791F|C0392B)"/>', f'<a:srgbClr val="{colour}"/>', s)))
    row_dots = sorted([m for m in dots if int(geom(m.group(0)).group(1)) > 10000000], key=lambda m: int(geom(m.group(0)).group(2)))
    for m, (share, colour) in zip(row_dots, rows):
        edits.append((m.start(), m.end(), re.sub(r'<a:srgbClr val="(08684E|B7791F|C0392B)"/>', f'<a:srgbClr val="{colour}"/>', m.group(0))))
    for start, end, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        xml = xml[:start] + text + xml[end:]
    return xml


def dash_target(ind: dict, value, targets: list, meta: dict, scope: str, quarter: dict) -> dict:
    """A KPI against its target exactly as the dashboard's quarter view evaluates it: rates against the quarter
    milestone or annual target; counts against the quarter's target paced to the data date; snapshots against the
    annual target. Returns target, share of target, status key and label."""
    out = dict(target=None, share=None, status="none", label="No target")
    key = ind.get("target")
    row = next((t for t in targets if t.get("target_key") == key and t.get("scope") == scope and t.get("age_band", "All") == "All"
                and str(t.get("fy") or meta["fiscal_year"]) == meta["fiscal_year"]), None)
    if not row:
        return out
    rag = meta.get("rag") or {"red_below": .5, "green_from": .75, "rate_warning_gap": .05}
    qk = f"q{meta['q_index']}"
    has_ms = any(row.get(k) is not None for k in ("q1", "q2", "q3", "q4"))
    annual = row.get("annual")
    def score_status(sc):
        if sc is None or ind.get("neutral"):
            return "none"
        return "exceeds" if sc > 1 else "good" if sc >= rag["green_from"] else "warning" if sc >= rag["red_below"] else "critical"
    if ind.get("kind") == "rate":
        t = row.get(qk) if row.get(qk) is not None else annual
        out.update(target=t, share=(value / t if value is not None and t else None))
        if ind.get("neutral") or value is None or t is None:
            out["status"] = "none"
        else:
            out["status"] = "exceeds" if value > t else "good" if value >= t else "warning" if value >= t - rag.get("rate_warning_gap", .05) else "critical"
    elif ind.get("flow") == "snapshot":
        out.update(target=annual, share=(value / annual if value is not None and annual else None))
        out["status"] = score_status(out["share"])
    else:
        qt = (row.get(qk) or 0) if has_ms else (annual / 4 if annual else None)
        through = meta.get("dhis_through") if ("DHIS" in (ind.get("source") or "DHIS2") or ind.get("group") == "Conventional") else meta.get("as_of")
        qs, qe = dt.date.fromisoformat(quarter["start"]), dt.date.fromisoformat(quarter["end"])
        th = dt.date.fromisoformat(str(through)[:10])
        el = max(0, min(1, ((min(qe, th) - qs).days + 1) / ((qe - qs).days + 1)))
        expected = qt * el if qt else None
        out.update(target=qt, share=(value / qt if value is not None and qt else None))
        out["status"] = score_status(value / expected if value is not None and expected else None)
        if ind.get("milestone_scope") == "programme" and scope != "ECEWS SPEED":
            out.update(target=None, share=None, status="none")
    out["label"] = {"exceeds": "Exceeds", "good": "On track", "warning": "Watch", "critical": "Off track", "none": "No target" if out["target"] is None else "—"}[out["status"]]
    return out


STATUS_COLOUR = {"exceeds": "08684E", "good": "08684E", "warning": "B7791F", "critical": "C0392B", "none": "8A9690"}


def blank_chart(deck: Deck, name: str) -> None:
    """Keep the chart's categories and empty its values."""
    xml = deck.text(f"ppt/charts/{name}.xml")
    first = re.search(r"<c:ser>.*?</c:ser>", xml, re.S).group(0)
    cats = [c.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") for c in re.findall(r"<c:v>([^<]*)</c:v>", re.search(r"<c:cat>.*?</c:cat>", first, re.S).group(0))]
    series = len(re.findall(r"<c:ser>", xml))
    set_chart(deck, name, cats, [[None] * len(cats) for _ in range(series)])


def keep_slides(deck: Deck, positions: list[int]) -> None:
    """Keep only these slides, and remove everything only the others used (their charts hold sample numbers)."""
    keep = {deck.order[p - 1] for p in positions}
    pres, rels = deck.text("ppt/presentation.xml"), deck.text("ppt/_rels/presentation.xml.rels")
    for m in re.finditer(r"<Relationship\b[^>]*/>", rels):
        tgt = re.search(r'Target="slides/([^"]+)"', m.group(0))
        if not tgt or tgt.group(1) in keep:
            continue
        rid = re.search(r'Id="(rId\d+)"', m.group(0)).group(1)
        sid = re.search(rf'<p:sldId id="(\d+)" r:id="{rid}"/>', pres)
        if sid:
            pres = pres.replace(sid.group(0), "")
            pres = re.sub(rf'<p14:sldId id="{sid.group(1)}"/>', "", pres)
        rels = rels.replace(m.group(0), "")
    deck.put("ppt/presentation.xml", pres)
    deck.put("ppt/_rels/presentation.xml.rels", rels)
    deck.order = [s for s in deck.order if s in keep]

    seen, todo = set(), ["_rels/.rels"]
    while todo:                                  # every part still reachable from the package
        rel = todo.pop()
        if rel in seen or rel not in deck.parts:
            continue
        seen.add(rel)
        folder = str(Path(rel).parent.parent).replace("\\", "/")
        for tag in re.findall(r"<Relationship\b[^>]*>", deck.parts[rel].decode("utf-8")):
            if 'TargetMode="External"' in tag:
                continue
            target = re.search(r'Target="([^"]+)"', tag).group(1)
            part = target.lstrip("/") if target.startswith("/") else str(Path(folder, target)).replace("\\", "/")
            part = re.sub(r"[^/]+/\.\./", "", part)
            while "../" in part:
                part = re.sub(r"[^/]+/\.\./", "", part, count=1)
            part = part.removeprefix("./")
            if part not in seen and part in deck.parts:
                seen.add(part)
                todo.append(str(Path(part).parent / "_rels" / (Path(part).name + ".rels")).replace("\\", "/"))
    ct = deck.text("[Content_Types].xml")
    for name in [n for n in deck.parts if n not in seen and n != "[Content_Types].xml"]:
        del deck.parts[name]
        ct = re.sub(rf'<Override PartName="/{re.escape(name)}"[^>]*/>', "", ct)
    deck.put("[Content_Types].xml", ct)
    if "docProps/app.xml" in deck.parts:
        deck.put("docProps/app.xml", re.sub(r"<Slides>\d+</Slides>", f"<Slides>{len(deck.order)}</Slides>", deck.text("docProps/app.xml")))


# ---------------------------------------------------------------- the deck
def build_deck(data: dict, template: Path, state: str, out: Path, eac: dict | None = None) -> None:
    f = Figures(data, state)
    d = Deck(template)
    meta = f.meta
    name = state or "All states"
    q = f"Q{meta['q_index']}"
    pts = f.points
    cats = [f.label(i) for i in pts]
    last12 = pts[-12:]
    cats12 = [f.label(i) for i in last12]

    # cover
    when = dt.date.fromisoformat(str(meta["as_of"])[:10])
    d.set_slide(1, replace_texts(d.slide(1), [
        ("ECEWS-SPEED PROJECT  ·  [ STATE ]", f"ECEWS-SPEED PROJECT  ·  {name}"),
        ("[ FY ] Q[ __ ]  ·  Week [ __ ]", f"{meta['fiscal_year']} {q}  ·  {f.week_label()}"),
        ("[ Day, DD Month YYYY ]", f"{when:%A, %d %B %Y}"),
        ("Non-treatment indicators: weekly DHIS2 instance as of [ date ]   ·   Treatment indicators: NMRS line list as of [ date ], triangulated with the NDR PBS line list",
         f"Non-treatment indicators: DHIS2 monthly reports, then weekly reports after them, through {meta.get('dhis_through', '')}   ·   Treatment indicators: line lists as of {meta.get('linelist_as_of', '')}"),
    ]))

    # case finding
    def y_age(fn, sex, ages):
        return rate(fn("hts_tst_pos", sex=sex, ages=ages), fn("hts_tst", sex=sex, ages=ages))
    over15 = {a for (_, _, _, a, _) in f.t} - UNDER15
    w, fy = f.week, f.fy
    d.set_slide(5, colour_yield_grid(replace_texts(d.slide(5), [
        ("1,646", num(w("hts_tst"))), ("FY to date: 126,030", f"FY to date: {num(fy('hts_tst'))}"),
        ("53", num(w("hts_tst_pos"))), ("FY to date: 3,009", f"FY to date: {num(fy('hts_tst_pos'))}"),
        ("3.2%", rate(w("hts_tst_pos"), w("hts_tst"))), ("FY to date: 2.4%", f"FY to date: {rate(fy('hts_tst_pos'), fy('hts_tst'))}"),
        ("0.5%", y_age(fy, "M", UNDER15)), ("0.4%", y_age(fy, "F", UNDER15)), ("3.2%", y_age(fy, "M", over15)), ("2.9%", y_age(fy, "F", over15)),
        ("0.0%", y_age(w, "M", UNDER15)), ("2.3%", y_age(w, "F", UNDER15)), ("8.0%", y_age(w, "M", over15)), ("2.5%", y_age(w, "F", over15)),
    ])))
    set_chart(d, "chart1", cats, [[f.at(i, "hts_tst") for i in pts], [(f.at(i, "hts_tst_pos"), f.at(i, "hts_tst")) for i in pts]])

    # testing by stream, index week
    week_no = f.week_label().split(" - ")[0].replace("Week ", "") if f.index_week is not None else BLANK
    stream_t = [w("hts_tst", mod=m) for _, m in STREAMS]
    stream_p = [w("hts_tst_pos", mod=m) for _, m in STREAMS]
    set_chart(d, "chart2", [s for s, _ in STREAMS], [stream_t, stream_p, list(zip(stream_p, stream_t))])
    pitc = [w("hts_tst", mod="Other PITC", sex=s) for s in ("F", "M")]
    set_chart(d, "chart3", ["Female", "Male"], [[None, None], pitc, [None, None]])      # OPD attendance is not in the data
    all_pos = sum(stream_p) or 1
    top = sorted(zip([s for s, _ in STREAMS], stream_p), key=lambda x: -x[1])[:5]
    set_chartex(d, "chartEx1", [s for s, _ in top], [round(v / all_pos, 4) for _, v in top])
    s6, r6 = drop_chartex_extras(replace_texts(d.slide(6), [(" 1", f" {week_no}")]), d.text("ppt/slides/_rels/slide6.xml.rels"), "chartEx1")
    d.set_slide(6, s6)
    d.put("ppt/slides/_rels/slide6.xml.rels", r6)

    # index testing, index week
    idx = [("HTS POS", "hts_tst_pos"), ("Index Cases", "index_cases"), ("Index cases offered", "index_offered"), ("Index cases that accepted", "index_accepted"),
           ("Index Contacts Elicited", "index_elicited"), ("Index Contacts Tested", "index_tst"), ("Known Positive", "index_known_pos"), ("New Positives", "index_pos")]
    sex_rows = {s: {m: w(m, sex=s) for _, m in idx} for s in ("F", "M")}
    def cascade(s):
        r = sex_rows[s]
        return [None, r["hts_tst_pos"], r["index_cases"], r["index_offered"], r["index_accepted"], r["index_elicited"], r["index_tst"],
                r["index_tst"] - r["index_pos"], r["index_known_pos"], r["index_pos"], None]      # unsuppressed and linked are not on these forms
    set_chart(d, "chart4", ["Unsuppressed", "HTS POS", "Index Cases", "Index cases offered", "Index cases that accepted", "Index Contacts Elicited",
                            "Index Contacts Tested", "Index contacts tested Negatives", "Known Positive", "New Positives", "Linked"], [cascade("F"), cascade("M")])
    i_cases, i_off, i_acc, i_eli, i_tst, i_pos = (w(m) for m in ("index_cases", "index_offered", "index_accepted", "index_elicited", "index_tst", "index_pos"))
    share_ = lambda a, b: round(a / b, 4) if b else 0
    # six rows as in the template, so its ratio label stays on its row; linkage of contacts is not on these forms
    ratio = f"1:{i_eli / i_acc:.1f}" if i_acc else "—"         # shown in the row's name: the template's floating label does not stay on its row
    set_chartex(d, "chartEx23", ["Offer Rate", "Acceptance Rate", f"Elicitation Ratio {ratio}", "Testing rate", "Yield", "Linkage"],
                [share_(i_off, i_cases), share_(i_acc, i_off), share_(i_eli, i_acc), share_(i_tst, i_eli), share_(i_pos, i_tst), None])
    d.put("ppt/charts/chart4.xml", replace_texts(d.text("ppt/charts/chart4.xml"), [("Week 1", f"Week {week_no}")]))
    kids = {m: w(m, ages=UNDER15) for m in ("index_elicited", "index_tst", "index_pos")}
    s7 = replace_texts(d.slide(7), [("100%", pct0(i_off, i_cases)), ("99%", pct0(i_acc, i_off)), ("1:1", ""),
                                    ("Number of Children Elicited:", f"Number of Children Elicited: {num(kids['index_elicited'])}"),
                                    ("Number of Children Tested:", f"Number of Children Tested: {num(kids['index_tst'])}"),
                                    ("Number of Children Newly Identified Positive:", f"Number of Children Newly Identified Positive: {num(kids['index_pos'])}")])
    s7, r7 = drop_chartex_extras(s7, d.text("ppt/slides/_rels/slide7.xml.rels"), "chartEx23")
    d.set_slide(7, s7)
    d.put("ppt/slides/_rels/slide7.xml.rels", r7)

    # linkage gap: the reasons, GC7 and the tracker are for the team
    wk_pos, wk_tx = w("hts_tst_pos"), w("tx_new_q")
    s12 = replace_texts(d.slide(12), [(BLANK, num(wk_pos)), (BLANK, num(wk_tx)), (BLANK, BLANK), (BLANK, num(max(wk_pos - wk_tx, 0)))])
    gaps = f.linkage_gaps()
    rows = [[name, num(gap), ""] for name, gap in gaps] + [["—", "—", ""]] * (3 - len(gaps))
    d.set_slide(12, fill_tables(s12, [rows], header_rows=1))

    # retesting: only testing points and positives are in the dashboard's data
    points_w = f.testing_points(lambda p: f.index_week is not None and p is f.periods[f.index_week])
    points_fy = f.testing_points(f._in_fy)
    split = f"{BLANK} community · {BLANK} non-lab · {BLANK} lab"
    d.set_slide(13, replace_texts(d.slide(13), [
        ("61", num(points_w)), ("51", num(wk_pos)), ("2 community · 5 non-lab · 44 lab", split), ("51", BLANK), ("100% of positives", "of positives"),
        ("51", BLANK), ("100% concordance", "concordance"), ("0", BLANK), ("0% discordance", "discordance"), ("51", BLANK), ("100% coverage", "coverage"),
        ("61", num(points_fy)), ("2,987", num(fy("hts_tst_pos"))), ("95 community · 248 non-lab · 2,644 lab", split), ("2,987", BLANK), ("100% of positives", "of positives"),
        ("2,987", BLANK), ("100% concordance", "concordance"), ("0", BLANK), ("0% discordance", "discordance"), ("2,887", BLANK), ("97% coverage", "coverage"),
    ]))

    # PrEP
    last6 = pts[-6:]
    set_chart(d, "chart9", [f.label(i) for i in last6], [[f.at(i, "prep_offered") for i in last6], [f.at(i, "prep_accepted") for i in last6],
                                                         [None] * len(last6), [f.at(i, "prep_new") for i in last6]])
    pbfw = [f.at(i, "prep_new_preg") + f.at(i, "prep_new_bf") for i in pts]
    set_chart(d, "chart10", cats, [pbfw])
    set_chart(d, "chart11", cats, [[f.at(i, "prep_new") - b for i, b in zip(pts, pbfw)]])
    d.set_slide(14, replace_texts(d.slide(14), [
        ("15,555", num(fy("hts_tst_neg"))), ("15%", pct0(fy("prep_new"), fy("hts_tst_neg"))),
        ("PW [ __ ]  ·  BFW [ __ ]  ·  Male [ __ ]  ·  Female [ __ ]",
         f"PW {num(w('prep_new_preg'))}  ·  BFW {num(w('prep_new_bf'))}  ·  Male {num(w('prep_new', sex='M'))}  ·  Female {num(w('prep_new', sex='F'))}"),
    ]))
    ct = fy("prep_ct")
    set_chart(d, "chart12", ["Due for retest", "Returned", "Retested", "Negative", "Positive", "PrEP continuation"], [[None, None, None, None, None, ct]])
    set_chart(d, "chart13", cats, [[f.at(i, "prep_ct") for i in pts]])
    d.set_slide(15, replace_texts(d.slide(15), [
        ("24%", BLANK), ("212 of 868 due", f"{BLANK} of {BLANK} due"), ("0", BLANK), ("among 212 retested", f"among {BLANK} retested"),
        (BLANK, BLANK), (BLANK, num(fy("prep_ct_preg"))), (BLANK, BLANK), (BLANK, num(fy("prep_ct_bf"))),
        (BLANK, BLANK), (BLANK, num(max(ct - fy("prep_ct_preg") - fy("prep_ct_bf"), 0))),
    ]))

    # ---------------- treatment (2nd 95): figures as of the line list date
    st = f.stock
    qn = meta["q_index"]
    fy_n = int(re.sub(r"\D", "", meta["fiscal_year"]))
    prev_q = f"FY{fy_n - 1:02d} Q4" if qn == 1 else f"FY{fy_n:02d} Q{qn - 1}"
    this_q = f"{meta['fiscal_year']} {q}"
    curr, prev = st.get("tx_curr_emr", 0), st.get("prev_tx_curr", 0)
    net = curr - prev
    mmd = [st.get(k, 0) for k in ("mmd_6", "mmd_3", "mmd_45", "mmd_lt3")]
    mt = sum(mmd)
    sgn = lambda v: f"−{num(-v)}" if v < 0 else f"+{num(v)}" if v > 0 else "0"
    p1 = lambda a, b: f"{a / b:.1%}" if b else "—"
    d.set_slide(17, empty_bars(replace_texts(d.slide(17), [
        ("49,588", num(st.get("tx_curr", 0))), ("75", num(w("tx_new_q"))), ("Net change in clients on ART (FY)", "Net change in clients on ART (quarter)"), ("−117", sgn(net)),
        ("Clients currently on ART · weekly trend", f"Clients currently on ART · end of {prev_q} vs now"),
        ("77%", pct0(mmd[0], mt)), ("40,315", num(mmd[0])), ("77.2%", p1(mmd[0], mt)), ("7,525", num(mmd[1])), ("14.4%", p1(mmd[1], mt)),
        ("3,955", num(mmd[2])), ("7.6%", p1(mmd[2], mt)), ("409", num(mmd[3])), ("0.8%", p1(mmd[3], mt)),
    ])))
    set_chart(d, "chart14", [f"End {prev_q}", "Now"], [[prev, curr]])
    d.put("ppt/charts/chart14.xml", re.sub(r"<c:(?:min|max) val=\"[^\"]*\"/>", "", d.text("ppt/charts/chart14.xml")))   # the template's axis fitted its sample
    set_chart(d, "chart15", cats, [[f.at(i, "tx_new_q") for i in pts]])
    set_chart(d, "chart16", ["6 months", "3 months", "4–5 months", "<3 months"], [mmd])

    # refills, on-time pick-up, VL synchronisation, pending refills, verification: these need pick-up history the
    # dashboard does not keep, so the slides stay for the team with every figure as [ __ ]
    for n_, charts in ((18, ["chart17", "chart18", "chart19", "chart20"]), (19, ["chart21", "chart22"]), (20, []), (21, []), (25, ["chart23", "chart24"])):
        d.set_slide(n_, empty_bars(blank_numbers(d.slide(n_))))
        for c in charts:
            blank_chart(d, c)

    # continuity on treatment (EMR line list, as the dashboard's waterfall)
    gain = st.get("wf_new", 0) + st.get("wf_ti", 0) + st.get("wf_rtt", 0)
    loss = st.get("wf_dead", 0) + st.get("wf_to", 0) + st.get("wf_stopped", 0) + st.get("wf_iit", 0)
    other = st.get("tx_curr_emr", 0) - (st.get("wf_prev", 0) + gain - loss)      # as the dashboard's waterfall: status changes not in the steps above
    set_chartex(d, "chartEx24", [f"{prev_q} TX CURR", f"{this_q} TX_NEW", "TI", "RTT", "POTENTIAL TX CURR", "DEAD", "TO", "STOPPED", "IIT", "GAIN/LOSS", f"{this_q} TX_CURR"],
                [st.get("wf_prev", 0), st.get("wf_new", 0), st.get("wf_ti", 0), st.get("wf_rtt", 0), st.get("wf_prev", 0) + gain,
                 -st.get("wf_dead", 0), -st.get("wf_to", 0), -st.get("wf_stopped", 0), -st.get("wf_iit", 0), other, st.get("tx_curr_emr", 0)])
    d.put("ppt/charts/chartEx24.xml", re.sub(r'(<cx:subtotals>.*?)<cx:idx val="9"\s*/>', r'\1<cx:idx val="10"/>', d.text("ppt/charts/chartEx24.xml"), flags=re.S))
    s22 = replace_texts(d.slide(22), [
        ("FY27 Q1 Continuity on Treatment Cascade ", f"{this_q} Continuity on Treatment Cascade "),
        ("TX_NETNEW: -117", f"TX_NETNEW: {sgn(net)}"),
        ("% IIT: 0.55%", f"% IIT: {p1(st.get('wf_iit', 0), st.get('wf_prev', 0) + st.get('wf_new', 0))}"),
        ("TOTAL GAIN: 170", f"TOTAL GAIN: {num(gain)}"), ("TOTAL LOSS: 287", f"TOTAL LOSS: {num(loss)}"),
    ])
    s22, r22 = drop_chartex_extras(s22, d.text("ppt/slides/_rels/slide22.xml.rels"), "chartEx24")
    d.set_slide(22, s22)
    d.put("ppt/slides/_rels/slide22.xml.rels", r22)

    # interruption: the count and where it happens; tracking and outcomes are for the team
    iit = f.quarter("iit_q")
    s23 = empty_bars(blank_numbers(d.slide(23)))
    s23 = s23.replace(f"<a:t>{BLANK}</a:t>", f"<a:t>{num(iit)}</a:t>", 1)
    rows = [[name, num(v)] for name, v in f.top_sites("iit_q", f._in_q)] + [["—", "—"]] * 5
    d.set_slide(23, fill_tables(s23, [rows[:5]], header_rows=1))

    # biometrics (NDR line list): all clients on treatment; the newly initiated half is for the team
    act, cap, enr, dup = (st.get(k, 0) for k in ("pbs_active", "pbs_baseline", "pbs_enrolled", "pbs_duplicate"))
    uniq, rec, mat = enr - dup, st.get("pbs_recapture", 0), st.get("pbs_match", 0)
    s24 = replace_texts(d.slide(24), [
        ("89,674", num(act)), ("82,296", num(cap)), ("91.8% of previous", f"{p1(cap, act)} of previous"),
        ("82,058", num(enr)), ("99.7% of previous", f"{p1(enr, cap)} of previous"),
        ("81,856", num(uniq)), ("99.8% of previous", f"{p1(uniq, enr)} of previous"),
        ("76,223", num(mat)), ("93.1% of previous", f"{p1(mat, rec)} of recaptured"),
        ("−7,378", sgn(cap - act)), ("−238", sgn(enr - cap)), ("−202", sgn(-dup)), ("−5,633", sgn(-st.get("pbs_nomatch", 0))),
    ])
    at = s24.index("Newly initiated on ART · index week")
    s24 = s24[:at] + blank_numbers(s24[at:])
    d.set_slide(24, empty_bars(s24))

    # ---------------- advanced HIV disease (DHIS2 AHD form, new on ART, FY to date)
    a = {m: {s: fy(m, sex=s) for s in ("F", "M")} for m in ['ahd_enrolled_n', 'ahd_cd4_n', 'ahd_cd4lt200_n', 'ahd_who34_n', 'ahd_confirmed_n', 'ahd_lam_test_n', 'ahd_lam_pos_n', 'ahd_xpert_test_n', 'ahd_xpert_pos_n', 'ahd_tb_tx_n', 'ahd_crag_test_n', 'ahd_crag_pos_n', 'ahd_csf_test_n', 'ahd_csf_pos_n', 'ahd_preempt_n']}
    under5 = {s: fy("ahd_enrolled_n", sex=s, ages={"<1", "1-4"}) for s in ("F", "M")}
    tot = lambda m: a[m]["F"] + a[m]["M"]
    both = lambda n_, d_: f"F {pct0(a[n_]['F'], a[d_]['F'])} · M {pct0(a[n_]['M'], a[d_]['M'])}"
    set_chart(d, "chart25", ["Newly initiated on ART", "Eligible for CD4", "CD4 done", "CD4 <200", "WHO stage 3 & 4", "Children <5 yrs", "AHD diagnosed",
                             "TB LF-LAM done", "LF-LAM TB +", "GeneXpert done", "GeneXpert TB +", "Started TB Rx"],
              [[a["ahd_enrolled_n"][s], None, a["ahd_cd4_n"][s], a["ahd_cd4lt200_n"][s], a["ahd_who34_n"][s], under5[s], a["ahd_confirmed_n"][s],
                a["ahd_lam_test_n"][s], a["ahd_lam_pos_n"][s], a["ahd_xpert_test_n"][s], a["ahd_xpert_pos_n"][s], a["ahd_tb_tx_n"][s]] for s in ("F", "M")])
    set_chart(d, "chart26", ["Eligible for CrAg", "Serum CrAg done", "Serum CrAg +", "CSF CrAg done", "CSF CrAg +", "CM on treatment", "Pre-emptive Rx", "Referred to hub"],
              [[None, a["ahd_crag_test_n"][s], a["ahd_crag_pos_n"][s], a["ahd_csf_test_n"][s], a["ahd_csf_pos_n"][s], None, a["ahd_preempt_n"][s], None] for s in ("F", "M")])
    d.set_slide(28, replace_texts(d.slide(28), [
        ("100%", pct0(tot("ahd_cd4_n"), tot("ahd_enrolled_n"))), ("F 100% · M 100%", both("ahd_cd4_n", "ahd_enrolled_n")),
        ("30%", pct0(tot("ahd_confirmed_n"), tot("ahd_enrolled_n"))), ("F 24% · M 39%", both("ahd_confirmed_n", "ahd_enrolled_n")),
        ("78%", pct0(tot("ahd_lam_test_n"), tot("ahd_confirmed_n"))), ("F 82% · M 75%", both("ahd_lam_test_n", "ahd_confirmed_n")),
        ("78%", pct0(tot("ahd_crag_test_n"), tot("ahd_confirmed_n"))), ("F 82% · M 75%", both("ahd_crag_test_n", "ahd_confirmed_n")),
    ]))
    # returning clients (back after 3+ months): the agreed way to count them is still open, so this slide is for the team
    d.set_slide(29, empty_bars(blank_numbers(d.slide(29))))
    blank_chart(d, "chart27")
    blank_chart(d, "chart28")

    # ---------------- TB. The DHIS2 TB forms do not split new from already on ART here, so the screening cascade
    # is the EMR's for everyone on ART in the index week (slide 31); the newly initiated slide keeps only its count
    s30 = blank_numbers(d.slide(30)).replace(f"<a:t>{BLANK}</a:t>", f"<a:t>{num(w('tx_new_q'))}</a:t>", 1)
    d.set_slide(30, empty_bars(s30))
    t_ = {m: w(m) for m in ['tbs_screened', 'tbs_presumptive', 'tbs_gxp', 'tbs_gxp_pos', 'tbs_tested', 'tbs_diagnosed', 'tbs_tx_started', 'tbs_cxr', 'tbs_cxr_pos']}
    d.set_slide(31, empty_bars(replace_texts(d.slide(31), [
        ("TB screening, diagnosis and preventive therapy: clients already on ART", "TB screening, diagnosis and preventive therapy: all clients on ART (EMR line list)"),
        ("2,664", BLANK), ("2,664", num(t_["tbs_screened"])), ("100%", BLANK),
        ("24", num(t_["tbs_presumptive"])), ("1% yield", f"{pct0(t_['tbs_presumptive'], t_['tbs_screened'])} yield"),
        ("21", num(t_["tbs_gxp"])), ("88% evaluated", f"{pct0(t_['tbs_gxp'], t_['tbs_presumptive'])} evaluated"),
        ("21", BLANK), ("100%", BLANK),
        ("1", num(t_["tbs_diagnosed"])), ("5% of tested", f"{pct0(t_['tbs_diagnosed'], t_['tbs_tested'])} of tested"),
        ("1", num(t_["tbs_tx_started"])), ("100%", pct0(t_["tbs_tx_started"], t_["tbs_diagnosed"])),
        ("2,600", BLANK), ("64", BLANK), ("40", BLANK), ("12", BLANK),
        ("CXR evaluated [ __ ] · returned [ __ ] · CXR TB [ __ ] · TB Rx after CXR [ __ ] · TB Rx after LF-LAM [ __ ] · Total started on TB treatment [ __ ]",
         f"CXR evaluated {num(t_['tbs_cxr'])} · returned [ __ ] · CXR TB {num(t_['tbs_cxr_pos'])} · TB Rx after CXR [ __ ] · TB Rx after LF-LAM [ __ ] · Total started on TB treatment {num(t_['tbs_tx_started'])}"),
    ])))
    # portable digital X-ray: not in the dashboard's data
    d.set_slide(32, empty_bars(replace_texts(blank_numbers(d.slide(32)), [("138 of 156 with score ≥34", f"{BLANK} of {BLANK} with score ≥34")])))
    blank_chart(d, "chart29")

    # ---------------- viral load (NDR line list, last 12 months), as on the dashboard's VL page
    _vls = next((t_.get("annual") for t_ in data.get("targets", []) if t_.get("target_key") == "NIPI_VLS_WITH_RESULT" and t_.get("scope") == (state or "ECEWS SPEED")
                 and t_.get("age_band", "All") == "All" and str(t_.get("fy") or meta["fiscal_year"]) == meta["fiscal_year"]), None)
    vls_t = _vls if _vls is not None else .95
    vls_gap = (meta.get("rag") or {}).get("rate_warning_gap", .05)
    v = lambda m, **k: f.st(m, **k)
    quarter_note = f"Quarter-to-date · {f.week_label().split(' - ')[0].lower()}" if f.index_week is not None else "Quarter-to-date"
    s34 = d.slide(34)
    s34 = blank_numbers(s34[:s34.index("<a:t>12-month viral load cascade")]) + s34[s34.index("<a:t>12-month viral load cascade"):]
    s34 = replace_texts(s34, [("Quarter-to-date · week [ __ ]", quarter_note)])
    s34 = replace_texts(s34, [
        ("94%", BLANK), ("97%", pct0(v("vl_result"), v("vl_eligible"))), ("97%", pct0(v("vl_suppressed"), v("vl_result"))),
        ("92%", pct0(v("vl_undetectable"), v("vl_result"))), ("1,571", num(st.get("vl_gap_emr", 0))),
        ("incl. 1,474 suppressed — sync before reporting", "sync before reporting"),
    ], after="12-month viral load cascade · EMR vs NDR")
    d.set_slide(34, empty_bars(s34))
    set_chart(d, "chart30", ["Clients on ART", "VL eligible", "Valid result", "Suppressed (<1,000)", "Undetectable (<50)"],
              [[st.get("tx_curr_emr", 0), None, None, None, None], [v("tx_curr"), v("vl_eligible"), v("vl_result"), v("vl_suppressed"), v("vl_undetectable")]])

    bands = [("0–4", {"<1", "1-4"}), ("5–9", {"5-9"}), ("10–14", {"10-14"}), ("15–19", {"15-19"}), ("20–24", {"20-24"})]
    named = {a for _, b in bands for a in b}
    bands.append(("25+", {a for a in f.dims["age"] if a not in named and a not in ("", "Unknown")}))
    old35 = [["161", "130", "123", "95%", "112", "91%", "91", "21", "74%"], ["357", "346", "331", "96%", "302", "91%", "255", "47", "77%"],
             ["625", "618", "597", "97%", "530", "89%", "476", "54", "80%"], ["775", "725", "683", "94%", "603", "88%", "550", "53", "81%"],
             ["2,242", "2,034", "1,909", "94%", "1,833", "96%", "1,758", "75", "92%"], ["45,376", "44,365", "42,659", "96%", "41,682", "98%", "39,943", "1,739", "94%"],
             ["49,585", "48,218", "46,302", "96%", "45,062", "97%", "43,073", "1,989", "93%"]]
    def vl_row(ages):
        on, el, rs, sp, ud, ll = (v(m, ages=ages) for m in ("tx_curr", "vl_eligible", "vl_result", "vl_suppressed", "vl_undetectable", "vl_llv"))
        return [num(on), num(el), num(rs), pct0(rs, el), num(sp), pct0(sp, rs), num(ud), num(ll), pct0(ud, rs)]
    new35 = [vl_row(b) for _, b in bands] + [vl_row(None)]
    d.set_slide(35, status_colours(replace_texts(d.slide(35), [p for o, n_ in zip(old35, new35) for p in zip(o, n_)], after="% undet."), vls_t, vls_gap))
    set_chart(d, "chart31", [l for l, _ in bands], [[(v("vl_suppressed", sex=s, ages=b), v("vl_result", sex=s, ages=b)) for _, b in bands] for s in ("F", "M")])

    # MoU reporting: coverage and suppression over everyone on ART
    kids = UNDER15
    adults = {a for a in f.dims["age"] if a not in kids and a not in ("", "Unknown")}
    def mou(ages, s):
        on, rs, sp = (v(m, sex=s, ages=ages) for m in ("tx_curr", "vl_result", "vl_suppressed"))
        return on, rs, sp
    old36 = ["643", "647", "591", "598", "92%", "93%", "535", "529", "83%", "83%", "30,055", "18,240", "27,956", "17,156", "93%", "94%", "27,181", "16,816", "90%", "92%",
             "30,698", "18,887", "28,547", "17,754", "93%", "94%", "27,716", "17,345", "90%", "92%", "49,585", "46,301", "93%", "45,061", "91%"]
    new36 = []
    for ages in (kids, adults, None):
        (of_, rf, sf), (om, rm, sm) = mou(ages, "F"), mou(ages, "M")
        new36 += [num(of_), num(om), num(rf), num(rm), pct0(rf, of_), pct0(rm, om), num(sf), num(sm), pct0(sf, rf), pct0(sm, rm)]
    on, rs, sp = v("tx_curr"), v("vl_result"), v("vl_suppressed")
    new36 += [num(on), num(rs), pct0(rs, on), num(sp), pct0(sp, on)]
    k_r, k_s = v("vl_result", ages=kids), v("vl_suppressed", ages=kids)
    s36 = replace_texts(d.slide(36), [("93%", pct0(rs, on)), ("46,301 of 49,585", f"{num(rs)} of {num(on)}"), ("91%", pct0(sp, on)), ("45,061 suppressed", f"{num(sp)} suppressed"),
                                      ("Suppression among children 0–15 (girls and boys)", "Suppression among children 0–14 (girls and boys)"),
                                      ("83%", pct0(k_s, k_r)), ("1,064 of 1,189 with results", f"{num(k_s)} of {num(k_r)} with results")])
    s36 = replace_texts(s36, [("0–15", "0–14"), ("&gt;15", "15+")] if False else [("0–15", "0–14")], after="% suppressed")
    s36 = s36.replace("<a:t>&gt;15</a:t>", "<a:t>15+</a:t>")
    s36 = replace_texts(s36, list(zip(old36, new36)), after="0–14")
    d.set_slide(36, status_colours(s36, vls_t, vls_gap))
    set_chart(d, "chart32", ["F 0–14", "M 0–14", "F 15+", "M 15+"],
              [[(mou(a, s)[1], mou(a, s)[0]) for a, s in ((kids, "F"), (kids, "M"), (adults, "F"), (adults, "M"))],
               [(mou(a, s)[2], mou(a, s)[1]) for a, s in ((kids, "F"), (kids, "M"), (adults, "F"), (adults, "M"))]])

    # facilities with the largest VL gaps: the gap columns from the data, the plan for the team
    sites = sorted({f_ for (m, _, _, f_) in f.stock_k if m == "vl_eligible"}, key=lambda x: -(f.st("vl_eligible", fac=x) - f.st("vl_result", fac=x)))[:8]
    old37 = [["448", "73%", "72%", "93%", "23", "Bleeding"], ["832", "90%", "69%", "97%", "20", "Results"], ["398", "82%", "63%", "94%", "14", "Bleeding"],
             ["191", "84%", "38%", "90%", "7", "Results"], ["313", "91%", "55%", "94%", "11", "Results"], ["90", "69%", "51%", "87%", "6", "Bleeding"],
             ["322", "91%", "67%", "95%", "11", "Results"], ["642", "96%", "93%", "93%", "40", "Suppression"]]
    pairs37 = []
    for k, (old) in enumerate(old37):
        if k < len(sites):
            x = sites[k]
            el, sm, rs, sp = (f.st(m, fac=x) for m in ("vl_eligible", "vl_sample", "vl_result", "vl_suppressed"))
            col_, cov_, sup_ = (sm / el if el else 0), (rs / el if el else 0), (sp / rs if rs else 0)
            same = sm == rs                     # no separate sample dates for this site: collection cannot be told from results
            gap = "Coverage" if same and cov_ < .9 else "Bleeding" if col_ < .9 else "Results" if cov_ < .9 else "Suppression" if sup_ < vls_t else "—"
            new = [num(el), BLANK if same else pct0(sm, el), pct0(rs, el), pct0(sp, rs), num(rs - sp), gap]
            pairs37 += [(f"Facility {k + 1}", f.fac_names[x])] + list(zip(old, new))
        else:
            pairs37 += [(f"Facility {k + 1}", "—")] + [(o, "") for o in old]
    s37 = replace_texts(d.slide(37), pairs37, after="Change vs last wk")
    s37 = s37.replace("<a:t>Suppression: &lt;95%</a:t>", f"<a:t>Suppression: &lt;{vls_t:.0%} (dashboard target)</a:t>")
    d.set_slide(37, status_colours(empty_bars(s37), vls_t, vls_gap))

    # LIMS: not in the dashboard's data
    d.set_slide(38, empty_bars(blank_numbers(d.slide(38))))

    # EAC: clients with an unsuppressed result in the quarter a year back and the one after it, as on the dashboard's EAC page
    q0 = dt.date.fromisoformat(f.q_start)
    def back(months):
        y, m_ = divmod(q0.year * 12 + q0.month - 1 - months, 12)
        return dt.date(y, m_ + 1, 1)
    def q_label(start):
        fy_ = start.year + 1 if start.month >= 10 else start.year
        return f"FY{fy_ % 100:02d} Q{((start.month - 10) % 12) // 3 + 1}"
    teen = {"<1", "1-4", "5-9", "10-14", "15-19"}
    cats_eac = ["Total unsuppressed", "EAC commenced", "EAC session 2", "EAC session 3", "Extended EAC (4+)", "EAC completed", "Post-EAC VL sample",
                "Follow-up VL sample", "Follow-up VL result", "Re-suppressed (<1,000)", "   Undetectable (<50)", "   LLV (50–999)", "Still unsuppressed", "Switched to 2nd/3rd line"]
    for slide_no, chart, months, ages, title_old in ((39, "chart33", 12, None, "EAC cascade: FY26 Q1 unsuppressed results"),
                                                     (40, "chart34", 12, teen, "EAC cascade: FY26 Q1 unsuppressed results (0–19 years)"),
                                                     (41, "chart35", 9, None, "EAC cascade: FY26 Q2 unsuppressed results"),
                                                     (42, "chart36", 9, teen, "EAC cascade: FY26 Q2 unsuppressed results (0–19 years)")):
        s_ = back(months)
        e_ = (back(months - 3) - dt.timedelta(days=1)).isoformat()
        c_ = eac_cohort(eac or {}, f.dims, state, s_.isoformat(), e_, ages)
        res = c_["sup"] + c_["llv"]
        title = title_old.replace("FY26 Q1", q_label(s_)).replace("FY26 Q2", q_label(s_))
        nm = c_["n"]
        d.set_slide(slide_no, replace_texts(d.slide(slide_no), [(title_old, title)] + [
            (a_, b_) for a_, b_ in zip(re.findall(r"<a:t>([^<]*)</a:t>", d.slide(slide_no))[
                [t for t in re.findall(r"<a:t>([^<]*)</a:t>", d.slide(slide_no))].index("EAC commenced") + 1:][:0], [])] + [
            (o, n_) for o, n_ in zip(
                [t for t in re.findall(r"<a:t>([^<]*)</a:t>", d.slide(slide_no)) if re.search(r"\d", t) and not t.startswith(("EAC cascade", "Switched"))][1:13],
                [pct0(c_["st"], nm), f"{num(c_['st'])} of {num(nm)}", pct0(c_["s3"], nm), f"{num(c_['s3'])} of {num(nm)}",
                 pct0(c_["fu"], nm), f"{num(c_['fu'])} of {num(nm)}", pct0(res, c_["fu"]), f"{num(res)} of {num(c_['fu'])} with results",
                 num(c_["uns"]), f"{pct0(c_['uns'], c_['fu'])} of results", num(c_["sw"]), f"{pct0(c_['sw'], c_['uns'])} of still unsuppressed"])]))
        n_cats = len(re.findall(r"<c:pt ", re.search(r"<c:cat>.*?</c:cat>", d.text(f"ppt/charts/{chart}.xml"), re.S).group(0)))
        main = [nm, c_["st"], c_["s2"], c_["s3"], c_["ext"], c_["s3"], None, c_["fs"], c_["fu"], res, c_["sup"], c_["llv"], None, c_["sw"]]
        side = [None] * 12 + [c_["uns"], None]
        set_chart(d, chart, cats_eac[:max(n_cats, 13)], [main[:max(n_cats, 13)], side[:max(n_cats, 13)]])

    # ---------------- PMTCT (DHIS2 PMTCT forms, facility and community)
    scope = state or "ECEWS SPEED"
    targets_ = data.get("targets", [])
    reg = {r["code"]: r for r in data.get("registry", [])}
    quarter_ = next((q_ for q_ in data.get("quarters", []) if q_["fy"] == meta["fiscal_year"] and q_["q"] == meta["q_index"]),
                    {"start": f.q_start, "end": f.q_end})
    judge = lambda code, value: dash_target(reg.get(code, {"target": code, "kind": "count", "flow": "cumulative"}), value, targets_, meta, scope, quarter_)
    tgt = {t["target_key"]: t.get("annual") for t in targets_ if t.get("scope") == scope and t.get("age_band", "All") == "All"
           and str(t.get("fy") or meta["fiscal_year"]) == meta["fiscal_year"]}
    pc = lambda x: f"{x:.0%}" if x is not None else "—"
    qv = f.quarter
    def count_row(code, measure):
        wk_a, q_a = w(measure), qv(measure)
        r = judge(code, q_a) if code else dict(target=None, share=None, label="No target")
        annual = tgt.get(reg.get(code, {}).get("target", code)) if code else None
        return [None, None, num(annual) if annual else "—", num(r["target"]) if r["target"] else "—", num(wk_a), num(q_a),
                pc(r["share"]), pc(q_a / annual) if annual else "—", r["label"]]
    cov_q = qv("pmtct_art") / qv("pmtct_stat_pos") if qv("pmtct_stat_pos") else None
    cov_w = w("pmtct_art") / w("pmtct_stat_pos") if w("pmtct_stat_pos") else None
    cov = judge("IPI_PMTCT_ART_COV", cov_q)
    cov_t = cov["target"]
    cov_row = [None, None, pc(tgt.get("IPI_PMTCT_ART_COV")), pc(cov_t), pc(cov_w), pc(cov_q), pc(cov["share"]), pc(cov["share"]), cov["label"]]
    cov_fy = cov_q
    custom = [None, None, BLANK, BLANK, BLANK, BLANK, BLANK, BLANK, BLANK]
    rows44 = [cov_row, custom, custom, custom, count_row("PMTCT_STAT", "pmtct_stat_num"), count_row("", "pmtct_pos_new"),
              count_row("", "pmtct_eid_lt2"), count_row("", "pmtct_eid"), count_row("NIPI_INFANT_DX", "pmtct_eid_pos"), count_row("", "hei_art"), custom, custom]
    s44 = replace_texts(d.slide(44), [("Weekly target", "Quarter target"), ("Cumulative achieved", "Quarter achieved"),
                                      ("Weekly %", "% of quarter target"), ("Cumulative %", "% of annual target")])
    d.set_slide(44, fill_tables(s44, [rows44], header_rows=1))

    pos_w, art_w = w("pmtct_stat_pos"), w("pmtct_art")
    results = w("hei_neg") + w("pmtct_eid_pos")
    d.set_slide(45, empty_bars(replace_texts(d.slide(45), [
        ("96%", pct0(art_w, pos_w)), ("Target 98%  ·  FY cumulative 96%", f"Target {pc(cov_t) if cov_t is not None else '(none on the dashboard)'}  ·  Quarter {pc(cov_fy)}"),
        ("48 of 50 HIV-positive pregnant women on ART this week", f"{num(art_w)} of {num(pos_w)} HIV-positive pregnant women on ART this week"),
        ("1,240", num(w("pmtct_stat_den"))), ("FY 14,860", f"FY {num(fy('pmtct_stat_den'))}"),
        ("1,228", num(w("pmtct_stat_num"))), ("99% · FY 14,702", f"{pct0(w('pmtct_stat_num'), w('pmtct_stat_den'))} · FY {num(fy('pmtct_stat_num'))}"),
        ("50", num(pos_w)), ("38 known · 12 new", f"{num(w('pmtct_pos_known'))} known · {num(w('pmtct_pos_new'))} new"),
        ("48", num(art_w)), ("38 already · 10 new", f"{BLANK} already · {BLANK} new"),
        ("2 HIV-positive pregnant women not yet on ART", f"{num(max(pos_w - art_w, 0))} HIV-positive pregnant women not yet on ART"),
        ("46", BLANK), ("41", num(w("pmtct_eid_lt2"))), ("89%", BLANK),
        ("39", num(results)), ("95% of tested", f"{pct0(results, w('pmtct_eid'))} of tested"),
        ("1", num(w("pmtct_eid_pos"))), ("1", num(w("hei_art"))), ("100%", pct0(w("hei_art"), w("pmtct_eid_pos"))),
        ("91%", BLANK), ("94%", BLANK),
    ])))
    if pos_w:                            # the coverage bar, drawn to this week's value (track 3,270,482 EMU wide)
        width = int(3270482 * min(art_w / pos_w, 1))
        d.set_slide(45, d.slide(45).replace('<a:off x="664850" y="3154680"/><a:ext cx="0" cy="128016"/>',
                                            f'<a:off x="664850" y="3154680"/><a:ext cx="{width}" cy="128016"/>', 1))
    # GeneXpert EID point-of-care testing: not in the dashboard's data
    d.set_slide(46, empty_bars(blank_numbers(d.slide(46))))

    # ---------------- AP3: children and adolescents under 20
    u20 = {"<1", "1-4", "5-9", "10-14", "15-19"}
    k20 = lambda m: f.st(m, ages=u20)
    t_w, t_fy = w("hts_tst", ages=u20), fy("hts_tst", ages=u20)
    p_w, p_fy = w("hts_tst_pos", ages=u20), fy("hts_tst_pos", ages=u20)
    n_w, n_fy = w("tx_new_q", ages=u20), fy("tx_new_q", ages=u20)
    on20 = k20("tx_curr")
    iit20 = k20("ndr_wf_iit") / (k20("ndr_wf_prev") + k20("ndr_wf_new")) if (k20("ndr_wf_prev") + k20("ndr_wf_new")) else None
    el20, sm20, rs20, sp20 = (k20(m) for m in ("vl_eligible", "vl_sample", "vl_result", "vl_suppressed"))
    p1 = lambda a, b: f"{a / b:.1%}" if b else "—"
    dash3 = ["—", "—", "—"]
    rows48 = [
        (["1,500", "1,230", "82%", "25,962", "20,976", "81%"], ["—", num(t_w), "—", "—", num(t_fy), "—"]),
        (["17", "15", "88%", "266", "245", "92%"], ["—", num(p_w), "—", "—", num(p_fy), "—"]),
        (["17", "15", "88%", "260", "242", "93%"], ["—", num(n_w), "—", "—", num(n_fy), "—"]),
        (["95%", "100%", "105%", "95%", "99%", "104%"], ["—", pct0(n_w, p_w), "—", "—", pct0(n_fy, p_fy), "—"]),
        (["2,223", "1,919", "86%", "2,223", "1,919", "86%"], ["—", "—", "—", "—", num(on20), "—"]),
        (["&lt;1%", "1.8%", "1.8%", "&lt;1%", "2.1%", "2.1%"], ["—", "—", "—", "—", f"{iit20:.1%}" if iit20 is not None else "—", "—"]),
        (["107", "70", "65%", "556", "449", "81%"], ["—", "—", "—", "—", num(sm20), "—"]),
        (["449", "369", "82%", "449", "369", "82%"], ["—", "—", "—", "—", num(rs20), "—"]),
        (["95%", "94%", "99%", "95%", "94%", "99%"], ["—", "—", "—", "—", pct0(rs20, el20), "—"]),
        (["95%", "89%", "94%", "95%", "89%", "94%"], ["—", "—", "—", "—", pct0(sp20, rs20), "—"]),
    ]
    pairs48 = [(o.replace("&lt;", "<"), n_) for old, new in rows48 for o, n_ in zip(old, new)]
    pairs48 += [("93%", num(n_fy)), ("2.1%", f"{iit20:.1%}" if iit20 is not None else "—"), ("89%", pct0(sp20, rs20)),
                ("Target 95%", "No under-20 target on the dashboard")]
    pairs48 = [("Interruption in treatment (target &lt;1%)".replace("&lt;", "<"), "Interruption in treatment")] + pairs48
    s48 = replace_texts(d.slide(48), [("Interruption &lt;20 (target &lt;1%)".replace("&lt;", "<"), "Interruption <20")], after="VL suppression (<20)")
    pairs48 = pairs48[1:]
    s48 = replace_texts(s48, [("Interruption in treatment (target <1%)", "Interruption in treatment (<20)")])
    d.set_slide(48, status_cells(status_colours(replace_texts(s48, pairs48, after="Perf."), vls_t, vls_gap), vls_t, vls_gap))

    # weekly testing, 0-19
    neg20 = [f.at(i, "hts_tst", ages=u20) - f.at(i, "hts_tst_pos", ages=u20) for i in pts]
    pos20 = [f.at(i, "hts_tst_pos", ages=u20) for i in pts]
    set_chart(d, "chart37", cats, [neg20, pos20, [(p_, t_) for p_, t_ in zip(pos20, [f.at(i, "hts_tst", ages=u20) for i in pts])]])
    d.set_slide(49, replace_texts(d.slide(49), [("2,402", num(t_fy)), ("30", num(p_fy)), ("1.2%", rate(p_fy, t_fy)), ("97%", pct0(n_fy, p_fy))]))

    # family index testing: monthly batches from the DHIS2 monthly form
    batches = sorted({i for (m, i, *_ ) in f.t if m == "fit_elicited" and f.periods[i]["kind"] == "M" and f.t.get((m, i, *_), 0)}, key=lambda i: f.periods[i]["start"], reverse=True)[:4]
    def fit(i, m, **k):
        return f.at(i, m, **k) if i is not None else 0
    lat = batches[0] if batches else None
    el, ts, ps, kn = (fit(lat, m) for m in ("fit_elicited", "fit_tested", "fit_pos", "fit_known_pos"))
    s50 = replace_texts(d.slide(50), [("714", num(el)), ("98%", pct0(ts, el)), ("701 of 714", f"{num(ts)} of {num(el)}"), ("3.6%", rate(ps, ts)),
                                      ("27 new positives", f"{num(ps)} new positive{'s' if ps != 1 else ''}"), ("13", num(max(el - ts - kn, 0)))])
    old50 = [["304", "304", "714", "701", "27", "13", "3.6%", "100%"], ["285", "285", "844", "833", "29", "11", "3.5%", "100%"],
             ["249", "249", "761", "760", "27", "1", "3.5%", "100%"], ["249", "249", "570", "568", "23", "2", "4.1%", "100%"]]
    pairs50 = []
    for k, old in enumerate(old50):
        lab = ["FIT [ n ]", "FIT [ n−1 ]", "FIT [ n−2 ]", "FIT [ n−3 ]"][k]
        if k < len(batches):
            i = batches[k]
            o_, a_, e_, t_, p_, kp, l_ = (fit(i, m) for m in ("fit_offered", "fit_accepted", "fit_elicited", "fit_tested", "fit_pos", "fit_known_pos", "fit_linked"))
            name_ = dt.date.fromisoformat(str(f.periods[i]["start"])).strftime("%b %Y")
            pairs50 += [(lab, f"FIT {name_}"), ("[ Q ]", name_), ("[ Q ]", BLANK)] + list(zip(old, [num(o_), num(a_), num(e_), num(t_), num(p_), num(max(e_ - t_ - kp, 0)), rate(p_, t_), pct0(l_, p_)]))
        else:
            pairs50 += [(lab, "—"), ("[ Q ]", ""), ("[ Q ]", "")] + [(o, "") for o in old]
    d.set_slide(50, replace_texts(s50, pairs50, after="Linkage"))
    fbands = [("0–4", {"<1", "1-4"}), ("5–9", {"5-9"}), ("10–14", {"10-14"}), ("15–19", {"15-19"})]
    set_chart(d, "chart38", [b for b, _ in fbands], [[(fit(lat, "fit_pos", ages=a_), fit(lat, "fit_tested", ages=a_)) for _, a_ in fbands]])

    # continuity on treatment, 0-19 (NDR, as the dashboard's AP3 waterfalls)
    wv = {m: k20(m) for m in ['ndr_wf_prev', 'ndr_wf_new', 'ndr_wf_rtt', 'ndr_wf_ti', 'ndr_wf_dead', 'ndr_wf_to', 'ndr_wf_iit', 'ndr_wf_curr']}
    gain20 = wv["ndr_wf_new"] + wv["ndr_wf_rtt"] + wv["ndr_wf_ti"]
    loss20 = wv["ndr_wf_dead"] + wv["ndr_wf_to"] + wv["ndr_wf_iit"]
    pot20 = wv["ndr_wf_prev"] + gain20
    other20 = wv["ndr_wf_curr"] - (pot20 - loss20)       # includes clients ageing out: the line list has no separate count
    set_chartex(d, "chartEx25", [f"{prev_q} TX_CURR", f"{this_q} TX_NEW", "RTT", "T.I", "Potential TX_CURR", "Dead", "T.O", "Stopped", "Other (incl. aged out)", "IIT", f"{this_q} TX_CURR"],
                [wv["ndr_wf_prev"], wv["ndr_wf_new"], wv["ndr_wf_rtt"], wv["ndr_wf_ti"], pot20, -wv["ndr_wf_dead"], -wv["ndr_wf_to"], None, other20, -wv["ndr_wf_iit"], wv["ndr_wf_curr"]])
    net20 = wv["ndr_wf_curr"] - wv["ndr_wf_prev"]
    s51 = replace_texts(d.slide(51), [
        ("FY27 Q1 Continuity on Treatment Cascade 0-19yrs", f"{this_q} Continuity on Treatment Cascade 0-19yrs"),
        ("TX_NET_NEW: 0", f"TX_NET_NEW: {sgn(net20)}"), ("TX_NET_NEW excluding Aged Out: 0", f"TX_NET_NEW excluding Aged Out: {BLANK}"),
        ("% IIT: 0.00%", f"% IIT: {p1(wv['ndr_wf_iit'], wv['ndr_wf_prev'] + wv['ndr_wf_new'])}"),
        ("Program Gain : 0", f"Program Gain : {num(gain20)}"), ("Program Loss : 0", f"Program Loss : {num(loss20)}"),
        ("Program loss excluding aged out: 0", f"Program loss excluding aged out: {num(loss20)}"),
    ])
    s51, r51 = drop_chartex_extras(s51, d.text("ppt/slides/_rels/slide51.xml.rels"), "chartEx25")
    d.set_slide(51, s51)
    d.put("ppt/slides/_rels/slide51.xml.rels", r51)

    # ---------------- CCMP & PVC (slides 53-55): community programme data, not in the dashboard; the template's
    # figures there are already [ __ ], so the slides stay for the team as they are

    # ---------------- indicator dictionary and IPI / Non-IPI scorecard, from the FY27 targets file
    pc = lambda x: f"{x:.0%}" if x is not None else BLANK
    tg = lambda k: tgt.get(k)
    d.set_slide(58, replace_texts(d.slide(58), [
        ("98%", pc(tg("IPI_PMTCT_ART_COV"))), ("98%", pc(tg("IPI_ART_COV"))), ("95%", pc(tg("IPI_VLS_ON_ART"))),
        ("2 visits", f"{num(tg('IPI_JOINT_SSV'))} visits" if tg("IPI_JOINT_SSV") else BLANK),
        ("10% of HIV-negative tests", f"{pc(tg('NIPI_PREP_AMONG_NEG'))} of HIV-negative tests"), ("83%", pc(tg("NIPI_KNOW_STATUS"))),
        ("49", num(tg("NIPI_INFANT_DX")) if tg("NIPI_INFANT_DX") else BLANK), ("4,545", num(tg("NIPI_CASE_FINDING")) if tg("NIPI_CASE_FINDING") else BLANK),
        ("77%", pc(tg("NIPI_POP_VLS"))), ("84,275", num(tg("NIPI_TX_CURR")) if tg("NIPI_TX_CURR") else BLANK), ("98%", pc(tg("NIPI_VLS_WITH_RESULT"))),
    ], after="Denominator"))

    plhiv = sum(r.get("plhiv") or 0 for r in data.get("naomi", []) if not r.get("lga") and (not state or r.get("state") == state))
    def total(measure):
        """A measure for the quarter view: stock figures as of the line list, flows for the quarter to date."""
        if measure is None:
            return None
        if measure == "plhiv_est":
            return plhiv or None
        if measure in st:
            return st.get(measure, 0)
        if measure in {k[0] for k in f.t}:
            return qv(measure)
        return None                       # entered by hand on the dashboard (visits, DQA), not in this build
    def kpi_value(code):
        ind = reg.get(code, {})
        nu, de = total(ind.get("num")), total(ind.get("den")) if ind.get("den") else None
        if ind.get("kind") == "rate":
            return nu / de if nu is not None and de else None
        return nu
    def line(code):
        ind, val = reg.get(code, {}), kpi_value(code)
        r = judge(code, val)
        annual = tgt.get(ind.get("target", code))
        rate_ = ind.get("kind") == "rate"
        fmt = (lambda x: pc(x)) if rate_ else (lambda x: num(x) if x is not None else BLANK)
        ann_txt = (pc(annual) if rate_ else num(annual)) if annual is not None else "—"
        q_txt = (pc(r["target"]) if rate_ else num(r["target"])) if r["target"] is not None else "—"
        if code == "IPI_JOINT_SSV" and annual is not None:
            ann_txt = f"{num(annual)} visits"
        return [ann_txt, q_txt, "—", fmt(val) if val is not None else BLANK, "—"], (r["share"], STATUS_COLOUR[r["status"]])
    codes59 = ["IPI_ART_COV", "IPI_PMTCT_ART_COV", "IPI_VLS_ON_ART", "IPI_JOINT_SSV", "NIPI_KNOW_STATUS", "NIPI_CASE_FINDING",
               "NIPI_PREP_AMONG_NEG", "NIPI_INFANT_DX", "NIPI_TX_CURR", "NIPI_VLS_WITH_RESULT", "NIPI_POP_VLS", "NIPI_DQA_COVERAGE"]
    lines = [line(c_) for c_ in codes59]
    old59 = [["98%", "98%", "—", "99%", "—"], ["98%", "98%", "—", "96%", "—"], ["95%", "95%", "—", "93%", "—"], ["2 visits", "1", "—", "0", "—"],
             ["83%", "83%", "—", "80%", "—"], ["4,002", "1,001", "83", "183", "54"], ["10% of HIV-neg.", "610", "202", "424", "131"],
             ["36", "9", "—", "2", "—"], ["49,525", "49,525", "—", "50,303", "—"], ["95%", "95%", "—", "97%", "—"], ["77%", "77%", "—", "70%", "—"],
             ["10%", "6 sites", "—", "0", "—"]]
    pairs59 = [p_ for old, (new, _) in zip(old59, lines) for p_ in zip(old, new)]
    s59 = replace_texts(d.slide(59), pairs59, after="Status")
    s59 = replace_texts(s59, [("On track (≥100%)", "On track or exceeds"), ("Watch (90–99%)", "Watch"), ("Behind (&lt;90%)".replace("&lt;", "<"), "Off track")])
    d.set_slide(59, score_bars(s59, [r_ for _, r_ in lines]))

    # HIV self-testing: the DHIS2 forms split kits by assisted and unassisted, but not the steps after them
    kits = fy("hivst_assisted") + fy("hivst_unassisted")
    wk_a, wk_u = w("hivst_assisted"), w("hivst_unassisted")
    rx, ref, conf, link = fy("hivst_reactive"), fy("hivst_referred"), fy("hivst_confirmed"), fy("hivst_linked")
    def share(sex, *measures):
        tot = sum(w(m) for m in measures)
        return pct0(sum(w(m, sex=sex) for m in measures), tot)
    d.set_slide(8, replace_texts(d.slide(8), [
        ("1,629", num(fy("hivst_assisted"))), ("99", "—"), ("97", "—"), ("96", "—"), ("96", "—"), ("5.8%", "—"),
        ("1,265", num(fy("hivst_unassisted"))), ("78", "—"), ("77", "—"), ("77", "—"), ("77", "—"), ("6.0%", "—"),
        ("2,894", num(kits)), ("175", num(rx)), ("174", num(ref)), ("173", num(conf)), ("173", num(link)), ("5.9%", rate(rx, kits)),
        ("212", num(wk_a + wk_u)), ("164 assisted · 48 unassisted", f"{num(wk_a)} assisted · {num(wk_u)} unassisted"),
        ("5.9%", rate(rx, kits)), ("100%", pct0(link, conf)), ("173 of 173", f"{num(link)} of {num(conf)}"),
        ("M 60%", f"M {share('M', 'hivst_assisted', 'hivst_unassisted')}"), ("F 40%", f"F {share('F', 'hivst_assisted', 'hivst_unassisted')}"),
        ("M 60%", f"M {share('M', 'hivst_reactive')}"), ("F 40%", f"F {share('F', 'hivst_reactive')}"),
    ]))
    set_chart(d, "chart5", cats, [[f.at(i, "hivst_assisted") for i in pts], [f.at(i, "hivst_unassisted") for i in pts]])

    # testing by sex, leaving out pregnant women (ANC1 and post ANC1 testing)
    def by_sex(measure, sex):
        return [f.at(i, measure, sex=sex, not_mods=PREGNANT) for i in last12]
    tf, tm, pf, pm = by_sex("hts_tst", "F"), by_sex("hts_tst", "M"), by_sex("hts_tst_pos", "F"), by_sex("hts_tst_pos", "M")
    span = f"{len(last12)} periods" if len(last12) != 12 else "12 weeks"
    d.set_slide(9, replace_texts(d.slide(9), [
        ("Women tested · 12 weeks", f"Women tested · {span}"), ("7,379", num(sum(tf))), ("59% of all tests", f"{pct0(sum(tf), sum(tf) + sum(tm))} of all tests"),
        ("Men tested · 12 weeks", f"Men tested · {span}"), ("5,234", num(sum(tm))), ("41% of all tests", f"{pct0(sum(tm), sum(tf) + sum(tm))} of all tests"),
        ("Yield · women · 12 weeks", f"Yield · women · {span}"), ("5.7%", rate(sum(pf), sum(tf))), ("423 positives", f"{num(sum(pf))} positives"),
        ("Yield · men · 12 weeks", f"Yield · men · {span}"), ("5.0%", rate(sum(pm), sum(tm))), ("263 positives", f"{num(sum(pm))} positives"),
    ]))
    set_chart(d, "chart6", cats12, [tf, tm])
    set_chart(d, "chart7", cats12, [pf, pm])

    # linkage. GC7 is not in the dashboard's data, so that line stays for the team to fill in
    qt = f.quarter
    d.set_slide(10, replace_texts(d.slide(10), [
        ("FY27 Q1 ", f"{meta['fiscal_year']} {q} "),
        ("NEW POSITIVES: 3,009", f"NEW POSITIVES: {num(fy('hts_tst_pos'))}"), ("NEW ON ART: 2,890", f"NEW ON ART: {num(fy('tx_new_q'))}"),
        ("Linkage (GC7): 96%", f"Linkage: {pct0(fy('tx_new_q'), fy('hts_tst_pos'))}"), ("Linkage (Without GC7): 93%", "Linkage (Without GC7): [ __ ]"),
        ("NEW POSITIVES : 745", f"NEW POSITIVES : {num(qt('hts_tst_pos'))}"), ("NEW ON ART: 697", f"NEW ON ART: {num(qt('tx_new_q'))}"),
        ("Linkage (GC7): 94%", f"Linkage: {pct0(qt('tx_new_q'), qt('hts_tst_pos'))}"), ("Linkage (Without GC7): 92%", "Linkage (Without GC7): [ __ ]"),
        ("FY27", meta["fiscal_year"]), ("FY27 Q1", f"{meta['fiscal_year']} {q}"),
    ]))
    set_chart(d, "chart8", cats, [[f.at(i, "hts_tst_pos") for i in pts], [f.at(i, "tx_new_q") for i in pts],
                                  [(f.at(i, "tx_new_q"), f.at(i, "hts_tst_pos")) for i in pts]])

    # linkage by age and sex: FY to date, then the index week
    known = {a for a in over15 | UNDER15}
    def band_rows(fn):
        rows, named = [], set()
        for label, ages in BANDS:
            ages = ages or known - named - {"", "Unknown"} - {a for b in BANDS if b[1] for a in b[1]}
            named |= ages
            p_m, p_f = fn("hts_tst_pos", sex="M", ages=ages), fn("hts_tst_pos", sex="F", ages=ages)
            t_m, t_f = fn("tx_new_q", sex="M", ages=ages), fn("tx_new_q", sex="F", ages=ages)
            rows.append([label, num(p_m), num(p_f), num(t_m), num(t_f), pct0(t_m, p_m), pct0(t_f, p_f)])
        tot = [fn(m, sex=s) for m, s in (("hts_tst_pos", "M"), ("hts_tst_pos", "F"), ("tx_new_q", "M"), ("tx_new_q", "F"))]
        rows.append(["TOTAL", num(tot[0]), num(tot[1]), num(tot[2]), num(tot[3]), pct0(tot[2], tot[0]), pct0(tot[3], tot[1])])
        return rows
    s11 = fill_tables(d.slide(11), [band_rows(fy), band_rows(w)])
    s11 = replace_texts(s11, [("FY27", meta["fiscal_year"]), ("Week 1", f.week_label().split(" - ")[0])])
    d.set_slide(11, s11)

    for part in [p_ for p_ in d.parts if re.match(r"ppt/(slides/slide|slideLayouts/slideLayout|slideMasters/slideMaster)\d+\.xml$", p_)]:
        x = d.text(part)                 # footers (some in the layouts) still carrying the template's placeholders
        y = x.replace("[ State ]", escape(name)).replace("[ FY ] Q[ __ ]", this_q).replace("[ FY ] Q[__]", this_q)
        if y != x:
            d.put(part, y)
    keep_slides(d, KEEP)
    clean_notes(d)
    d.save(out)


def get(live: Path, template: Path, cache: Path, state: str) -> tuple[Path, str]:
    """The deck's file and its download name: from the cache if this build already made it, else built now."""
    if state not in STATES + [""]:
        raise ValueError("unknown state")
    with _lock:                  # those waiting then find the finished deck
        version = json.loads((live / "version.json").read_text(encoding="utf-8")).get("generated_at", "")
        tag = re.sub(r"\D", "", str(version))[:12]
        path = cache / f"{tag}_{state or 'All'}.pptx"
        if not path.is_file():
            data = read_core(live)
            eac = read_part(live, "vl").get("eac_clients", {})
            cache.mkdir(parents=True, exist_ok=True)
            for old in cache.glob("*.pptx"):
                if not old.name.startswith(tag):
                    old.unlink(missing_ok=True)
            build_deck(data, template, state, path, eac)
        return path, f"TRM_{state or 'All_states'}_{tag[:8]}.pptx"
