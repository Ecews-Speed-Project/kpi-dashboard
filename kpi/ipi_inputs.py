"""Manual IPI inputs: DQA coverage and joint supportive supervision visits (JSSV), entered each quarter in one
Excel workbook in the inbox (IPI_manual_inputs*.xlsx). Agreed with the M&E lead on 2026-11-16:

- DQA coverage = sites visited for DQA against the planned base for the quarter (about 10% of sites); the base is the target.
- JSSV = joint supportive supervision visits with GoN against an annual target per state (2 each in FY27).

Sheets: "DQA" (Quarter, State, Sites planned (base), Sites visited), "JSSV" (Quarter, State, Visits done) and
"JSSV targets" (Fiscal year, State, Visits required in the year). Blank rows are skipped.

Write a fresh template: python -m kpi.ipi_inputs inbox/IPI_manual_inputs.xlsx
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

STATES = ["Delta", "Ekiti", "Osun"]
QRE = re.compile(r"^\s*FY\s*(\d{2})\s*Q\s*([1-4])\s*$", re.I)


def _quarter(v) -> str | None:
    m = QRE.match(str(v or ""))
    return f"FY{m.group(1)}Q{m.group(2)}" if m else None


def _state(v) -> str | None:
    s = str(v or "").strip().title()
    return s if s in STATES else None


def _num(v) -> float | None:
    try:
        return None if v is None or str(v).strip() == "" else float(v)
    except ValueError:
        return None


def _rows(ws):
    """Data rows under the header row (the first row whose first cell reads Quarter / Fiscal year)."""
    rows = list(ws.iter_rows(values_only=True))
    for i, r in enumerate(rows):
        if r and str(r[0] or "").strip().lower() in ("quarter", "fiscal year"):
            return [r for r in rows[i + 1:] if r and any(c not in (None, "") for c in r)]
    return []


def read_inputs(path: Path) -> tuple[dict, list[str]]:
    """{'dqa': {quarter: {state: [visited, base]}}, 'jssv': {quarter: {state: visits}}, 'jssv_target': {fy: {state: n}}}
    and a list of problems found (rows skipped)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    out, issues = {"dqa": {}, "jssv": {}, "jssv_target": {}}, []
    if "DQA" in wb.sheetnames:
        for r in _rows(wb["DQA"]):
            q, st, base, vis = _quarter(r[0]), _state(r[1]), _num(r[2]), _num(r[3] if len(r) > 3 else None)
            if not (q and st) or base is None or vis is None:
                issues.append(f"DQA row skipped: {list(r[:4])}")
                continue
            out["dqa"].setdefault(q, {})[st] = [vis, base]
    if "JSSV" in wb.sheetnames:
        for r in _rows(wb["JSSV"]):
            q, st, vis = _quarter(r[0]), _state(r[1]), _num(r[2])
            if not (q and st) or vis is None:
                issues.append(f"JSSV row skipped: {list(r[:3])}")
                continue
            out["jssv"].setdefault(q, {})[st] = vis
    if "JSSV targets" in wb.sheetnames:
        for r in _rows(wb["JSSV targets"]):
            fy = str(r[0] or "").strip().upper().replace(" ", "")
            st, n = _state(r[1]), _num(r[2])
            if not re.match(r"^FY\d{2}$", fy) or not st or n is None:
                issues.append(f"JSSV target row skipped: {list(r[:3])}")
                continue
            out["jssv_target"].setdefault(fy, {})[st] = n
    return out, issues


def write_template(path: Path, dqa=None, jssv=None, targets=None) -> Path:
    """The input workbook, pre-filled with the rows given (FY27Q1 figures agreed on 2026-11-16 by default)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    dqa = dqa if dqa is not None else [("FY27Q1", "Delta", 6, 7), ("FY27Q1", "Ekiti", 2, 4), ("FY27Q1", "Osun", 3, 4)]
    jssv = jssv if jssv is not None else [("FY27Q1", "Delta", 0), ("FY27Q1", "Ekiti", 8), ("FY27Q1", "Osun", 0)]
    targets = targets if targets is not None else [("FY27", s, 2) for s in STATES]
    green, cream, ink = "163F31", "F1ECDF", "55452F"
    thin = Side(style="thin", color="DED0AE")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    quarters = [f"FY{y}Q{q}" for y in (27, 28) for q in (1, 2, 3, 4)]

    wb = Workbook()
    ws = wb.active
    ws.title = "Read me"
    lines = [("SPEED IPI manual inputs", Font(bold=True, size=14, color=green)),
             ("Fill one row per state per quarter, then save this file in the inbox folder. The next dashboard update reads it.", None),
             ("", None),
             ("DQA sheet", Font(bold=True, color=green)),
             ("Sites planned (base) = the sites planned for DQA that quarter (about 10% of sites). It is the target on the DQA coverage card.", None),
             ("Sites visited = the sites where DQA was actually done that quarter. Achievement = visited ÷ planned.", None),
             ("", None),
             ("JSSV sheet", Font(bold=True, color=green)),
             ("Visits done = joint supportive supervision visits with at least three GoN entities (SASCP, SACA, HMB, SPHCDA, LMCU…) that quarter.", None),
             ("JSSV targets sheet: visits each state must do in the fiscal year (2 per state in FY27). Achievement = visits done ÷ annual target.", None),
             ("", None),
             ("Quarter is written like FY27Q1 (Oct–Dec 2026). State is Delta, Ekiti or Osun. To correct a figure, change it here: the newest file in the inbox is used.", None)]
    for i, (t, f) in enumerate(lines, 1):
        c = ws.cell(i, 1, t)
        c.font = f or Font(color=ink)
    ws.column_dimensions["A"].width = 120

    def sheet(title, head, rows, widths, note):
        s = wb.create_sheet(title)
        s["A1"] = note
        s["A1"].font = Font(italic=True, color=ink)
        for j, h in enumerate(head, 1):
            c = s.cell(3, j, h)
            c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=green), box
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        s.row_dimensions[3].height = 32
        for i in range(60):                       # room for 20 quarters x 3 states
            for j in range(len(head)):
                c = s.cell(4 + i, 1 + j, rows[i][j] if i < len(rows) else None)
                c.border = box
                if j >= 2:
                    c.number_format = "0"
                    c.fill = PatternFill("solid", fgColor=cream)
        for j, w in enumerate(widths, 1):
            s.column_dimensions[chr(64 + j)].width = w
        sv = DataValidation(type="list", formula1='"' + ",".join(STATES) + '"', allow_blank=True)
        s.add_data_validation(sv)
        sv.add("B4:B63")
        s.freeze_panes = "A4"
        return s

    s = sheet("DQA", ["Quarter", "State", "Sites planned (base)", "Sites visited"], dqa, [12, 12, 20, 16],
              "DQA coverage: sites visited against the sites planned for the quarter.")
    qv = DataValidation(type="list", formula1='"' + ",".join(quarters) + '"', allow_blank=True)
    s.add_data_validation(qv)
    qv.add("A4:A63")
    s = sheet("JSSV", ["Quarter", "State", "Visits done"], jssv, [12, 12, 14],
              "Joint supportive supervision visits with GoN done in the quarter.")
    qv = DataValidation(type="list", formula1='"' + ",".join(quarters) + '"', allow_blank=True)
    s.add_data_validation(qv)
    qv.add("A4:A63")
    sheet("JSSV targets", ["Fiscal year", "State", "Visits required in the year"], targets, [12, 12, 24],
          "Annual JSSV target per state.")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


if __name__ == "__main__":
    print(write_template(Path(sys.argv[1] if len(sys.argv) > 1 else "inbox/IPI_manual_inputs.xlsx")))
