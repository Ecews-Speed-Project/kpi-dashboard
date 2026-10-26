"""Monthly case finding report (HTS team, CDC template), filled from the dashboard's data.

One PowerPoint per month of the fiscal year, from the HTS team's template: the DHIS2 *monthly* instance
only (no weekly reports), plus TX_NEW (new on ART by ART start date) from the treatment line list as
"Number linked to ART". "Reporting period" slides show the month; "Cumulative" slides show 1 October
to the end of that month. Persons at high risk = key populations = testing at the CSCC sites; Gen Pop =
all other sites (the two add up to the total).

Filled: title, Gen Pop and KP testing (month and cumulative), yield by modality (one slide per state),
sex and age disaggregation, HIVST. Left for the HTS team: activities, RTK stock, retesting for
verification, trainings, challenges (not in the monthly instance).

The template is edited as XML inside the .pptx, so its design (fonts, borders, merged cells) stays as made.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

STATES = ["Delta", "Ekiti", "Osun"]
NEED = {"hts_tst", "hts_tst_neg", "hts_tst_pos", "tx_new_q", "hivst_assisted", "hivst_unassisted",
        "hivst_reactive", "hivst_confirmed", "hivst_linked"}
AGE_BANDS = [("0-4", {"<1", "1-4"}), ("5-9", {"5-9"}), ("10-14", {"10-14"}), ("15-19", {"15-19"})]


# ---------------------------------------------------------------- figures
class Figures:
    """Sums over the cube for a set of months: monthly-instance rows (kind M) for DHIS2 measures,
    ART start dates (kind D) for TX_NEW."""

    def __init__(self, data: dict):
        dims, per = data["cube"]["dims"], data["periods"]
        rows = data["cube"]["rows"] if "rows" in data["cube"] else [list(r) for r in zip(*data["cube"]["cols"])]
        self.state = [f[0] for f in dims["facility"]]
        self.kp = [bool(re.search(r"cscc", str(f[2]), re.I)) for f in dims["facility"]]
        self.sex, self.age, self.mod = dims["sex"], dims["age"], dims["mod"]
        kind = [p.get("kind") for p in per]
        month = [p.get("month") for p in per]
        names = dims["measure"]
        self.rows = [(names[m], f, s, a, mo, month[p], v) for f, s, a, mo, p, m, v in rows
                     if names[m] in NEED and kind[p] == ("D" if names[m] == "tx_new_q" else "M")]
        self.idx: dict[tuple, list] = {}                 # (measure, month) -> rows
        for row in self.rows:
            self.idx.setdefault((row[0], row[5]), []).append(row)

    def sum(self, measure, months, state=None, kp=None, sex=None, ages=None, mod=None) -> float:
        t = 0.0
        for m, f, s, a, mo, mon, v in (row for mon_ in months for row in self.idx.get((measure, mon_), ())):
            if state and self.state[f] != state:
                continue
            if kp is not None and self.kp[f] != kp:
                continue
            if sex and self.sex[s] != sex:
                continue
            if ages is not None and self.age[a] not in ages:
                continue
            if mod is not None and self.mod[mo] != mod:
                continue
            t += v
        return t

    def mods(self, months, state) -> list[str]:
        found = {self.mod[mo] for m, f, s, a, mo, mon, v in self.rows
                 if m == "hts_tst" and mon in months and self.state[f] == state and v}
        return sorted(found, key=lambda x: -self.sum("hts_tst", months, state=state, mod=x))


def num(v) -> str:
    return f"{v:,.0f}"


def rate(a, b) -> str:
    return f"{a / b:.1%}" if b else "—"


# ---------------------------------------------------------------- template editing (XML)
def _cells(row: str) -> list[str]:
    return re.findall(r"<a:tc\b.*?</a:tc>", row, re.S)


def set_cell(tc: str, text: str | None, sz: int | None = None) -> str:
    """The cell with its text replaced (formatting kept); None leaves it; merged continuation cells stay."""
    if text is None or re.match(r'<a:tc[^>]*\b(vMerge|hMerge)="1"', tc):
        return tc
    m = re.search(r"<a:p>(.*?)</a:p>|<a:p/>", tc, re.S)
    inner = m.group(1) or "" if m else ""
    ppr = (re.search(r"<a:pPr\b[^>]*/>|<a:pPr\b[^>]*>.*?</a:pPr>", inner, re.S) or [None])[0] or ""
    rpr = re.search(r"<a:rPr\b[^>]*/>|<a:rPr\b.*?</a:rPr>", inner, re.S)
    end = re.search(r"<a:endParaRPr\b[^>]*/>|<a:endParaRPr\b.*?</a:endParaRPr>", inner, re.S)
    if rpr:
        rpr = rpr.group(0)
    elif end:
        rpr = end.group(0).replace("a:endParaRPr", "a:rPr")
    else:
        rpr = '<a:rPr lang="en-US"/>'
    end = end.group(0) if end else ""
    if sz:             # the text and the paragraph mark (which also sets the row's minimum height)
        rpr = re.sub(r'\ssz="\d+"', "", rpr, count=1).replace("<a:rPr", f'<a:rPr sz="{sz}"', 1)
        end = re.sub(r'\ssz="\d+"', "", end, count=1).replace("<a:endParaRPr", f'<a:endParaRPr sz="{sz}"', 1)
    para = f"<a:p>{ppr}<a:r>{rpr}<a:t>{escape(text)}</a:t></a:r>{end}</a:p>"
    body = re.search(r"(<a:txBody>.*?<a:lstStyle/>|<a:txBody>.*?</a:lstStyle>|<a:txBody>\s*<a:bodyPr[^>]*/>)(.*?)(</a:txBody>)", tc, re.S)
    if not body:
        return tc
    return tc[:body.start(2)] + para + tc[body.end(2):]


BOTTOM = 6400000      # lowest point a table may reach (EMU), above the template's footer bar


def fill_table(slide: str, values: list[list], header_rows: int = 1, block: int = 1, sz: int | None = None,
               top: int | None = None, header_height: int | None = None) -> str:
    """Fill the first table's data rows with `values` (one list per row, None = keep the cell). Rows are
    added by copying the last block of `block` rows (keeps merged state cells) or dropped from the end,
    and shrunk when needed so the table ends above the footer. top moves the table up (EMU from the slide's
    top); header_height is the header's real height when its text wraps beyond the row height."""
    tbl = re.search(r"<a:tbl>.*?</a:tbl>", slide, re.S)
    rows = re.findall(r"<a:tr\b.*?</a:tr>", tbl.group(0), re.S)
    head, data = rows[:header_rows], rows[header_rows:]
    while len(data) < len(values):
        data += data[-block:]
    data = data[:len(values)] if len(values) % block == 0 or block == 1 else data[:len(values)]
    out = []
    for row, vals in zip(data, values):
        cells = _cells(row)
        new = row
        for tc, v in zip(cells, vals + [None] * (len(cells) - len(vals))):
            new = new.replace(tc, set_cell(tc, None if v is None else str(v), sz), 1)
        out.append(new)
    all_rows = head + out
    if top is not None:
        slide = re.sub(r'(<p:graphicFrame>.*?<a:off x="\d+" y=")\d+(")', lambda m: f"{m.group(1)}{top}{m.group(2)}", slide, count=1, flags=re.S)
    y = re.search(r'<p:graphicFrame>.*?<a:off x="\d+" y="(\d+)"', slide, re.S)
    if y:
        hs = [int(re.search(r'<a:tr h="(\d+)"', r).group(1)) for r in all_rows]
        head_h = max(sum(hs[:header_rows]), header_height or 0)
        room = BOTTOM - int(y.group(1)) - head_h          # for the data rows
        if sum(hs[header_rows:]) > room > 0:
            k = room / sum(hs[header_rows:])
            all_rows = all_rows[:header_rows] + [re.sub(r'<a:tr h="\d+"', f'<a:tr h="{int(h * k)}"', r, count=1)
                                                 for r, h in zip(all_rows[header_rows:], hs[header_rows:])]
    total_h = sum(int(re.search(r'<a:tr h="(\d+)"', r).group(1)) for r in all_rows)
    new_tbl = tbl.group(0)
    first, last = re.search(r"<a:tr\b", new_tbl).start(), new_tbl.rfind("</a:tr>") + len("</a:tr>")
    new_tbl = new_tbl[:first] + "".join(all_rows) + new_tbl[last:]
    slide = slide[:tbl.start()] + new_tbl + slide[tbl.end():]
    # the table frame's height follows its rows
    return re.sub(r'(<p:graphicFrame>.*?<a:ext cx="\d+" cy=")\d+(")', lambda m: f"{m.group(1)}{total_h}{m.group(2)}", slide, count=1, flags=re.S)


def add_to_title(slide: str, text: str) -> str:
    """Append text to the slide title (a new run with the last run's formatting)."""
    sp = next((s for s in re.findall(r"<p:sp>.*?</p:sp>", slide, re.S) if '<p:ph type="title"' in s), None)
    if not sp:
        return slide
    runs = list(re.finditer(r"<a:r>(.*?)</a:r>", sp, re.S))
    if not runs:
        return slide
    rpr = (re.search(r"<a:rPr\b[^>]*/>|<a:rPr\b.*?</a:rPr>", runs[-1].group(1), re.S) or [None])[0] or ""
    new_sp = sp[:runs[-1].end()] + f"<a:r>{rpr}<a:t>{escape(text)}</a:t></a:r>" + sp[runs[-1].end():]
    return slide.replace(sp, new_sp, 1)


def set_title(slide: str, text: str) -> str:
    """Replace the whole title text (first run keeps its formatting)."""
    sp = next((s for s in re.findall(r"<p:sp>.*?</p:sp>", slide, re.S) if '<p:ph type="title"' in s), None)
    if not sp:
        return slide
    runs = list(re.finditer(r"<a:r>.*?</a:r>", sp, re.S))
    first = re.sub(r"<a:t>[^<]*</a:t>", f"<a:t>{escape(text)}</a:t>", runs[0].group(0), count=1)
    new_sp = sp[:runs[0].start()] + first + sp[runs[-1].end():]
    return slide.replace(sp, new_sp, 1)


class Deck:
    """The template's parts in memory, with slide duplication."""

    def __init__(self, path: Path):
        with zipfile.ZipFile(path) as z:
            self.parts = {n: z.read(n) for n in z.namelist()}
        pres = self.text("ppt/presentation.xml")
        rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', self.text("ppt/_rels/presentation.xml.rels")))
        self.order = [rels[r].split("/")[-1] for r in re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres)]

    def text(self, name: str) -> str:
        return self.parts[name].decode("utf-8")

    def put(self, name: str, text: str) -> None:
        self.parts[name] = text.encode("utf-8")

    def slide(self, n: int) -> str:                 # n = position in the deck, from 1
        return self.text(f"ppt/slides/{self.order[n - 1]}")

    def set_slide(self, n: int, xml: str) -> None:
        self.put(f"ppt/slides/{self.order[n - 1]}", xml)

    def duplicate(self, n: int, times: int) -> None:
        """Copies of slide n placed right after it (notes are not copied)."""
        src = self.order[n - 1]
        used = [int(re.search(r"\d+", s).group()) for s in self.order]
        pres, prels = self.text("ppt/presentation.xml"), self.text("ppt/_rels/presentation.xml.rels")
        ct = self.text("[Content_Types].xml")
        rid_src = re.search(rf'Id="(rId\d+)"[^>]*Target="slides/{re.escape(src)}"', prels).group(1)
        new_names = []
        for i in range(times):
            k = max(used) + 1 + i
            name = f"slide{k}.xml"
            self.put(f"ppt/slides/{name}", re.sub(r'<p:ext uri="[^"]*"><p188:commentRel\b[^>]*/></p:ext>', "",
                                                  self.text(f"ppt/slides/{src}")))
            rels = self.text(f"ppt/slides/_rels/{src}.rels")
            # a copy keeps the layout and pictures; notes and comments belong to the original slide only
            rels = re.sub(r'<Relationship [^>]*Type="[^"]*/(?:notesSlide|comments)"[^>]*/>', "", rels)
            for old in re.findall(r'Target="\.\./theme/(themeOverride\d+\.xml)"', rels):   # its own theme override
                n_ = 1 + max(int(x) for x in re.findall(r"themeOverride(\d+)\.xml", " ".join(self.parts)))
                new = f"themeOverride{n_}.xml"
                self.parts[f"ppt/theme/{new}"] = self.parts[f"ppt/theme/{old}"]
                rels = rels.replace(f"../theme/{old}", f"../theme/{new}")
                ct = ct.replace("</Types>", f'<Override PartName="/ppt/theme/{new}" ContentType="application/vnd.openxmlformats-officedocument.themeOverride+xml"/></Types>')
            self.put(f"ppt/slides/_rels/{name}.rels", rels)
            rid = "rId%d" % (max(int(x) for x in re.findall(r'Id="rId(\d+)"', prels)) + 1)
            prels = prels.replace("</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/{name}"/></Relationships>')
            sid = max(int(x) for x in re.findall(r'<p:sldId id="(\d+)"', pres)) + 1
            new_names.append((name, f'<p:sldId id="{sid}" r:id="{rid}"/>'))
            ct = ct.replace("</Types>", f'<Override PartName="/ppt/slides/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>')
            pres = pres.replace(f'r:id="{rid_src}"/>', f'r:id="{rid_src}"/>' + "".join(x[1] for x in new_names[-1:]), 1) if i == 0 else \
                pres.replace(new_names[-2][1], new_names[-2][1] + new_names[-1][1], 1)
        self.put("ppt/presentation.xml", pres)
        self.put("ppt/_rels/presentation.xml.rels", prels)
        self.put("[Content_Types].xml", ct)
        self.order[n:n] = [x[0] for x in new_names]
        if "docProps/app.xml" in self.parts:
            self.put("docProps/app.xml", re.sub(r"<Slides>\d+</Slides>", f"<Slides>{len(self.order)}</Slides>", self.text("docProps/app.xml")))

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ["[Content_Types].xml"] + [n for n in self.parts if n != "[Content_Types].xml"]:
                z.writestr(name, self.parts[name])
        os.replace(tmp, path)


# ---------------------------------------------------------------- one report
def month_label(m: str) -> str:
    return dt.date.fromisoformat(m + "-01").strftime("%B %Y")


def build_report(fig: Figures, template: Path, month: str, fy_months: list[str], out: Path, provisional: bool) -> None:
    cur, cum = {month}, set(fy_months)
    d = Deck(template)
    label = month_label(month)
    first = dt.date.fromisoformat(fy_months[0] + "-01")
    span = label if len(fy_months) == 1 else \
        f"{first:%B} – {label}" if fy_months[0][:4] == month[:4] else f"{month_label(fy_months[0])} – {label}"

    def hts_rows(months, kp):
        rows, tot = [], [0.0] * 4
        for s in STATES:
            t, n, p, l = (fig.sum(m, months, state=s, kp=kp) for m in ("hts_tst", "hts_tst_neg", "hts_tst_pos", "tx_new_q"))
            rows.append([s, num(t), num(n), num(p), rate(p, t), num(l), rate(l, p)])
            tot = [a + b for a, b in zip(tot, (t, n, p, l))]
        t, n, p, l = tot
        return rows + [["Total", num(t), num(n), num(p), rate(p, t), num(l), rate(l, p)]]

    # 1 title
    s1 = set_title(d.slide(1), f"Monthly Case Finding Report – {label}{' (provisional)' if provisional else ''}")
    d.set_slide(1, s1)
    # 3-6 Gen Pop / KP, month and cumulative
    for n, months, kp, tag in ((3, cur, False, label), (4, cum, False, span), (5, cur, True, label), (6, cum, True, span)):
        d.set_slide(n, add_to_title(fill_table(d.slide(n), hts_rows(months, kp), sz=1600), f" ({tag})"))
    # 8 sex, 9 age (reporting month, all sites)
    sex_rows = []
    for s in STATES:
        for lab, sx in (("Male", "M"), ("Female", "F"), ("Total", None)):
            t, p = fig.sum("hts_tst", cur, state=s, sex=sx), fig.sum("hts_tst_pos", cur, state=s, sex=sx)
            sex_rows.append([s if lab == "Male" else None, lab, num(t), num(p), rate(p, t)])
    d.set_slide(8, add_to_title(fill_table(d.slide(8), sex_rows, block=3), f" ({label})"))
    older = set(fig.age) - set().union(*(b for _, b in AGE_BANDS)) - {"Unknown"}
    age_rows = []
    for s in STATES:
        for i, (lab, band) in enumerate(AGE_BANDS + [("≥20", older), ("Total", None)]):
            t, p = fig.sum("hts_tst", cur, state=s, ages=band), fig.sum("hts_tst_pos", cur, state=s, ages=band)
            age_rows.append([s if i == 0 else None, lab, num(t), num(p), rate(p, t)])
    d.set_slide(9, add_to_title(fill_table(d.slide(9), age_rows, block=6, sz=1100), f" ({label})"))
    # 10 HIVST: kits by sex and method; results on each state's Total row (not split by method in DHIS2)
    hv = []
    for s in STATES:
        am, af = fig.sum("hivst_assisted", cur, state=s, sex="M"), fig.sum("hivst_assisted", cur, state=s, sex="F")
        um, uf = fig.sum("hivst_unassisted", cur, state=s, sex="M"), fig.sum("hivst_unassisted", cur, state=s, sex="F")
        rx, cf, ln = (fig.sum(m, cur, state=s) for m in ("hivst_reactive", "hivst_confirmed", "hivst_linked"))
        pos = fig.sum("hts_tst_pos", cur, state=s)
        hv += [[s, "Assisted", num(am), num(af), "", "", "", ""],
               [None, "Unassisted", num(um), num(uf), "", "", "", ""],
               [None, "Total", num(am + um), num(af + uf), num(rx), num(cf), num(ln), rate(cf, pos)]]
    d.set_slide(10, add_to_title(fill_table(d.slide(10), hv, block=3), f" ({label})"))
    # 7 yield by modality: one slide per state (done last: it adds slides after 7)
    d.duplicate(7, len(STATES) - 1)
    for i, s in enumerate(STATES):
        pos_all = fig.sum("hts_tst_pos", cur, state=s)
        rows = []
        for j, mo in enumerate(fig.mods(cur, s) or ["—"]):
            t, p = fig.sum("hts_tst", cur, state=s, mod=mo), fig.sum("hts_tst_pos", cur, state=s, mod=mo)
            rows.append([s if j == 0 else "", mo, num(t), num(p), rate(p, pos_all), rate(p, t)])
        t_all = fig.sum("hts_tst", cur, state=s)
        rows.append(["", "Total", num(t_all), num(pos_all), rate(pos_all, pos_all), rate(pos_all, t_all)])
        n = 7 + i
        d.set_slide(n, add_to_title(fill_table(d.slide(n), rows, sz=1100, top=1400000, header_height=1050000), f"– {s} ({label})"))
    d.save(out)


# ---------------------------------------------------------------- all months
def find_template(cfg: dict, root: Path) -> Path | None:
    inbox = root / (cfg.get("paths", {}).get("inbox") or "inbox")
    found = sorted(inbox.glob("*ase finding*emplate*.pptx"), key=lambda f: f.stat().st_mtime)
    if found:
        return found[-1]
    bundled = root / "dashboard" / "assets" / "hts_case_finding_template.pptx"
    return bundled if bundled.is_file() else None


def write_reports(data: dict, out_dir: Path, template: Path | None) -> list[dict]:
    """A report for every month of the fiscal year with monthly HTS reports; returns the list for the page."""
    if not template:
        return []
    fig = Figures(data)
    fy_start = str(data["meta"]["fy_start"])[:7]
    have = sorted({mon for m, f, s, a, mo, mon, v in fig.rows if m == "hts_tst" and v and mon and mon >= fy_start})
    closed = {r["month"]: bool(r.get("closed")) for r in data.get("dataflow", [])}
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for i, month in enumerate(have):
        name = f"HTS_case_finding_{month}.pptx"
        prov = not closed.get(month, False)
        build_report(fig, template, month, have[:i + 1], out_dir / name, prov)
        made.append({"month": month, "label": month_label(month), "file": f"reports/{name}", "provisional": prov})
    for old in out_dir.glob("HTS_case_finding_*.pptx"):          # months no longer in this fiscal year
        if old.name not in {m["file"].split("/")[-1] for m in made}:
            old.unlink(missing_ok=True)
    return made
