"""اقلام بحرانی — TEMPLATE-LOCKED Excel + PDF (user sample «140506», 2026-10-05).

This report is an EXCEPTION to the shared styler (``excel.table_style``) and to the
material base-columns rule (``services.material_columns``): its look is copied cell
by cell from the user's own sample workbook ``templates/critical_items_template.xlsx(.b64)``
(single sheet, title bar in row 1 merged B..second-to-last column, header row 2,
data rows, then a «توضیحات» block at the BOTTOM of the same sheet; per-column theme
fills, medium/thin borders, Tahoma 10 bold, RTL, A4 landscape, no frozen panes).

The template workbook is LOADED at build time so its theme colours (accent tints)
render exactly as in the sample; styles are copied from prototype cells, the
prototype sheet is then removed. Do NOT pass this workbook through
``finalize_workbook`` / ``finalize_path``.
"""
from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Border, Side

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "critical_items_template.xlsx"
TEMPLATE_SHEET = "140506"
TEMPLATE_FONT = "Tahoma"  # the sample itself uses Tahoma 10 bold on every cell

# report column → template column (A..G) whose style/width it takes.
TEMPLATE_COLUMN_OF = {
    "کد چهاررقمی": "A",
    "ردیف": "B",
    "کد و شرح کالا": "C",
    "موجودی": "D",
    "واحد": "E",
    "نیاز": "F",
    "حد تحمل(روز)": "G",
}
# Columns the user added on 2026-10-06 (not in the sample) take the plain «ردیف»
# style (no fill) with the sample's column borders.
EXTRA_PROTO_COLUMN = "B"
EXTRA_WIDTH = 12.8
TEMPLATE_WIDTHS = {"A": 12.8, "B": 10.3, "C": 80.6, "D": 13.0, "E": 11.9, "F": 16.3, "G": 12.8}

TITLE_ROW_HEIGHT = 45.0
HEADER_ROW_HEIGHT = 57.75
DATA_ROW_HEIGHT = 32.25
NOTE_MIN_HEIGHT = 30.75

NUMBER_FORMATS = {
    "کد چهاررقمی": "@",
    "ردیف": "@",
    "واحد": "@",
    "حد تحمل(روز)": "@",  # the sample stores the tolerance days as text ("45")
    "موجودی": "#,##0",
    "نیاز": "#,##0",
    "مصرف پیش‌بینی‌شده در افق": "#,##0",
}

# Resolved template colours (Excel's rendering of theme colour + tint) for the PDF.
PDF_TITLE_BG = "#A9D08E"  # accent6 70AD47, tint 0.6
PDF_COLUMN_BG = {
    "A": "#FFD966",  # accent4 FFC000, tint 0.4
    "B": None,
    "C": "#8EA9DB",  # accent1 4472C4, tint 0.4
    "D": "#DDEBF7",  # accent5 5B9BD5, tint 0.8
    "E": "#BFBFBF",  # lt1 FFFFFF, tint -0.25
    "F": "#FFFF00",
    "G": "#FF5353",
}

BLANK_NOTE_PDF_HEIGHT = 18.0  # pt — empty bordered notes line in the PDF
MEDIUM = Side(style="medium")
THIN = Side(style="thin")


def open_template_file(path: Path | str):
    """Binary stream of a sample workbook in ``templates/``.

    The repo keeps the samples base64-encoded (``<name>.xlsx.b64``) so they travel as
    text; a plain ``.xlsx`` next to it wins when present. Returns a path or BytesIO.
    """
    import base64
    import io

    p = Path(path)
    if p.is_file():
        return p
    b64 = p.with_name(p.name + ".b64")
    if b64.is_file():
        return io.BytesIO(base64.b64decode(b64.read_text(encoding="ascii")))
    raise FileNotFoundError(f"template missing: {p.name}(.b64)")


def load_template_workbook(path: Path | str = TEMPLATE_PATH):
    return load_workbook(open_template_file(path))


def template_letter(column: str) -> str:
    return TEMPLATE_COLUMN_OF.get(column, EXTRA_PROTO_COLUMN)


def _letter(idx: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(idx)


def _num(v: Any) -> Any:
    if v is None:
        return None
    try:
        import math

        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:  # noqa: BLE001
        pass
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:  # noqa: BLE001
            pass
    return v


def _text(v: Any) -> str:
    v = _num(v)
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _note_height(text: str, width_chars: float) -> float:
    per_line = max(20.0, width_chars * 1.05)
    lines = max(1, int(len(text) / per_line) + 1)
    return max(NOTE_MIN_HEIGHT, 15.0 * lines + 8.0)


def _fill_sheet(wb, proto, ws, sheet: dict[str, Any]) -> None:
    columns: list[str] = list(sheet["columns"])
    rows: list[dict[str, Any]] = list(sheet.get("rows") or [])
    # "" = an empty bordered notes line (sample style) for the user's handwriting
    notes: list[str] = [str(x or "").strip() for x in (sheet.get("notes") or [])]
    n = len(columns)

    ws.sheet_view.rightToLeft = True
    ws.sheet_view.zoomScale = proto.sheet_view.zoomScale
    ws.page_setup.orientation = proto.page_setup.orientation or "landscape"
    ws.page_setup.paperSize = proto.page_setup.paperSize or 9
    for attr in ("left", "right", "top", "bottom", "header", "footer"):
        setattr(ws.page_margins, attr, getattr(proto.page_margins, attr))
    ws.sheet_format.defaultRowHeight = proto.sheet_format.defaultRowHeight
    # Only print-setup addition to the sample: the 4 columns added on 2026-10-06 make
    # the table wider than one A4 landscape page at 100 %, so print fits to width.
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    for i, col in enumerate(columns, start=1):
        tl = template_letter(col)
        ws.column_dimensions[_letter(i)].width = (
            TEMPLATE_WIDTHS[tl] if col in TEMPLATE_COLUMN_OF else EXTRA_WIDTH
        )

    # Row 1 — title bar: A1 + last column carry the sample's (unfilled) style, the
    # title is merged from B to the second-to-last column like «B1:F1».
    ws.row_dimensions[1].height = TITLE_ROW_HEIGHT
    ws.cell(1, 1)._style = copy(proto["A1"]._style)
    for c in range(2, n + 1):
        ws.cell(1, c)._style = copy(proto["B1"]._style if c < n else proto["G1"]._style)
    ws.cell(1, 2, sheet["title"])
    if n > 3:
        ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=n - 1)

    # Row 2 — header.
    ws.row_dimensions[2].height = HEADER_ROW_HEIGHT
    for i, col in enumerate(columns, start=1):
        tl = template_letter(col)
        cell = ws.cell(2, i, col)
        cell._style = copy(proto[f"{tl}2"]._style)
        if col not in TEMPLATE_COLUMN_OF:
            cell.alignment = copy(proto["A2"].alignment)  # wrap long added headers
            cell.border = copy(proto["A2"].border)

    # Data rows (sample rows 4..13 are the regular body rows; C10 = right-aligned text).
    r = 3
    bold_rows = set(sheet.get("bold_rows") or [])
    for k, row in enumerate(rows):
        ws.row_dimensions[r].height = DATA_ROW_HEIGHT
        for i, col in enumerate(columns, start=1):
            tl = template_letter(col)
            proto_cell = proto["C10"] if tl == "C" else proto[f"{tl}4"]
            cell = ws.cell(r, i)
            cell._style = copy(proto_cell._style)
            if col not in TEMPLATE_COLUMN_OF:
                cell.border = copy(proto["D4"].border)
            v = _num(row.get(col))
            fmt = NUMBER_FORMATS.get(col)
            if fmt == "@":
                v = _text(v) or None
                if col == "واحد" and v and v.lower() == "no":
                    v = "NO"  # sample spelling (Kg / NO)
            cell.value = v
            if fmt is None and isinstance(v, (int, float)) and not isinstance(v, bool):
                fmt = "#,##0" if float(v).is_integer() else "#,##0.0#"
            cell.number_format = fmt or "General"
            if k == len(rows) - 1:  # last body row closes with a medium bottom border
                b = copy(cell.border)
                cell.border = Border(left=b.left, right=b.right, top=b.top, bottom=MEDIUM)
        r += 1
    if not rows:
        ws.row_dimensions[r].height = DATA_ROW_HEIGHT
        for i in range(1, n + 1):
            ws.cell(r, i)._style = copy(proto["B4"]._style)
            ws.cell(r, i).border = Border(right=MEDIUM, left=MEDIUM if i == 1 else None,
                                          bottom=MEDIUM)
        ws.cell(r, 3 if n >= 3 else 1, sheet.get("empty_message") or "قلمی یافت نشد.")
        r += 1
    _ = bold_rows  # every cell of the sample is already bold

    # توضیحات block — same sheet, under the table (sample rows 15..17).
    if notes:
        first = r
        last = r + len(notes) - 1
        note_end = max(3, n - 1)
        width_chars = sum(
            ws.column_dimensions[_letter(i)].width or 10 for i in range(3, note_end + 1)
        )
        for j, text in enumerate(notes):
            rr = first + j
            if j == 0:
                proto_c = proto["C15"]
            elif j == len(notes) - 1:
                proto_c = proto["C17"]
            else:
                proto_c = proto["C16"]
            ws.row_dimensions[rr].height = (
                _note_height(text, width_chars) if text else NOTE_MIN_HEIGHT
            )
            for c in range(1, n + 1):
                cell = ws.cell(rr, c)
                if c == 1:
                    cell._style = copy(proto["A15"]._style)
                elif c == 2:
                    cell._style = copy(proto["B15"]._style)
                elif c <= note_end:
                    cell._style = copy(proto_c._style)
                else:
                    cell._style = copy(proto["G15"]._style)
            ws.cell(rr, 3, text or None)
            if note_end > 3:
                ws.merge_cells(start_row=rr, start_column=3, end_row=rr, end_column=note_end)
        ws.cell(first, 2, "توضیحات")
        if last > first:
            ws.merge_cells(start_row=first, start_column=1, end_row=last, end_column=1)
            ws.merge_cells(start_row=first, start_column=2, end_row=last, end_column=2)
        # close the block like the sample (bottom medium under the last note row)
        for c in range(1, n + 1):
            cell = ws.cell(last, c)
            b = copy(cell.border)
            cell.border = Border(left=b.left, right=b.right, top=b.top, bottom=MEDIUM)


def write_critical_items_xlsx(sheets: list[dict[str, Any]], output_path: Path | str) -> Path:
    """``sheets``: [{name, title, columns, rows, notes, empty_message?}] → xlsx path.

    One sheet per segment, each laid out exactly like the sample (notes at the bottom).
    """
    wb = load_template_workbook(TEMPLATE_PATH)
    proto = wb[TEMPLATE_SHEET]
    used: set[str] = set()
    for sheet in sheets:
        name = str(sheet["name"])[:31]
        for ch in '[]:*?/\\':
            name = name.replace(ch, " ")
        base, k = name, 2
        while name in used or name == TEMPLATE_SHEET:
            name = f"{base[:28]} {k}"
            k += 1
        used.add(name)
        ws = wb.create_sheet(name)
        _fill_sheet(wb, proto, ws, sheet)
    wb.remove(proto)
    from excel.table_style import mark_template_locked

    mark_template_locked(wb, "critical_items")
    wb.active = 0
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    from excel.contrast import fix_workbook_contrast as _fwc
    _fwc(wb)
    wb.save(out)
    return out


# ---------------------------------------------------------------------------
# PDF — mirrors the sample sheet: title bar, per-column fills, «توضیحات» block.
# ---------------------------------------------------------------------------
def write_critical_items_pdf(
    sheet: dict[str, Any],
    output_path: Path | str,
    *,
    letterhead_path: Path | str | None = None,
) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    from pdf import generator as g

    g._register_fonts()
    columns: list[str] = list(sheet["columns"])
    rows: list[dict[str, Any]] = list(sheet.get("rows") or [])
    notes: list[str] = [str(x or "").strip() for x in (sheet.get("notes") or [])]
    n = len(columns)

    size = landscape(A4)
    margin = 0.6 * cm
    top, bottom = g._letterhead_margins(letterhead_path, size, margin, margin)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out), pagesize=size, rightMargin=margin, leftMargin=margin,
        topMargin=top, bottomMargin=bottom, title=str(sheet["title"]),
    )
    avail = float(size[0]) - 2 * margin
    raw_w = [TEMPLATE_WIDTHS[template_letter(c)] if c in TEMPLATE_COLUMN_OF else EXTRA_WIDTH
             for c in columns]
    k = avail / sum(raw_w)
    widths = [w * k for w in raw_w]

    fs = 7.0 if n > 8 else 8.5
    cell_st = ParagraphStyle("CritCell", fontName=g.FONT_BOLD, fontSize=fs,
                             leading=fs + 3, alignment=TA_CENTER)
    desc_st = ParagraphStyle("CritDesc", parent=cell_st, alignment=2)  # right
    title_st = ParagraphStyle("CritTitle", parent=cell_st, fontSize=11, leading=15)

    def P(text: Any, st: ParagraphStyle, w: float):
        return g._p(text, st, w)

    # RTL: logical column 0 (A) is the RIGHTMOST visual column.
    vis = list(range(n))[::-1]
    vw = [widths[i] for i in vis]

    def vrow(cells: list[Any]) -> list[Any]:
        return [cells[i] for i in vis]

    data: list[list[Any]] = []
    style: list[tuple] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, -1), g.FONT_BOLD),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]
    # title row: spans logical cols 1..n-2 (B..second-to-last)
    title_cells = [""] * n
    t_lo, t_hi = (1, n - 2) if n > 3 else (0, n - 1)
    span_w = sum(widths[t_lo:t_hi + 1])
    # reportlab draws a SPAN from its top-LEFT cell = logical t_hi in RTL
    title_cells[t_hi] = P(sheet["title"], title_st, span_w)
    data.append(vrow(title_cells))
    vx = lambda i: n - 1 - i  # noqa: E731 — logical index → visual column
    style += [
        ("SPAN", (vx(t_hi), 0), (vx(t_lo), 0)),
        ("BACKGROUND", (vx(t_hi), 0), (vx(t_lo), 0), colors.HexColor(PDF_TITLE_BG)),
        ("BOX", (vx(t_hi), 0), (vx(t_lo), 0), 1.4, colors.black),
    ]
    # header row
    data.append(vrow([P(c, cell_st, widths[i]) for i, c in enumerate(columns)]))
    style.append(("GRID", (0, 1), (-1, 1), 1.4, colors.black))
    # body
    body_start = 2
    for row in rows:
        cells = []
        for i, c in enumerate(columns):
            v = _num(row.get(c))
            if c in ("موجودی", "نیاز", "مصرف پیش‌بینی‌شده در افق") and isinstance(v, (int, float)):
                txt = f"{v:,.0f}"
            elif isinstance(v, float) and not v.is_integer():
                txt = f"{v:,.2f}".rstrip("0").rstrip(".")
            elif isinstance(v, (int, float)) and c not in ("کد چهاررقمی", "ردیف"):
                txt = f"{v:,.0f}"
            else:
                txt = _text(v)
                if c == "واحد" and txt.lower() == "no":
                    txt = "NO"  # same spelling as the xlsx/template (Kg / NO)
            cells.append(P(txt, desc_st if c == "کد و شرح کالا" else cell_st, widths[i]))
        data.append(vrow(cells))
    if not rows:
        cells = [""] * n
        cells[min(2, n - 1)] = P(sheet.get("empty_message") or "قلمی یافت نشد.", cell_st,
                                 widths[min(2, n - 1)])
        data.append(vrow(cells))
    body_end = len(data) - 1
    for i, c in enumerate(columns):  # per-column sample fills: header + body
        bg = PDF_COLUMN_BG.get(template_letter(c)) if c in TEMPLATE_COLUMN_OF else None
        if bg:
            style.append(("BACKGROUND", (vx(i), 1), (vx(i), body_end), colors.HexColor(bg)))
    style += [
        ("LINEBELOW", (0, body_start), (-1, body_end - 1), 0.5, colors.black),
        ("LINEBEFORE", (0, body_start), (-1, body_end), 1.4, colors.black),
        ("LINEAFTER", (0, body_start), (-1, body_end), 1.4, colors.black),
        ("LINEBELOW", (0, body_end), (-1, body_end), 1.4, colors.black),
    ]
    # notes block
    row_heights: list = []
    if notes:
        nb = len(data)
        lo, hi = 2, max(2, n - 2)
        nw = sum(widths[lo:hi + 1])
        for j, text in enumerate(notes):
            cells = [""] * n
            if j == 0:
                cells[1] = P("توضیحات", cell_st, widths[1])
            if text:
                cells[hi] = P(text, cell_st, nw)  # top-left cell of the RTL span
            data.append(vrow(cells))
            row_heights.append(None if text else BLANK_NOTE_PDF_HEIGHT)
            style.append(("SPAN", (vx(hi), nb + j), (vx(lo), nb + j)))
        ne = len(data) - 1
        # The sample merges A/B over the notes rows; a vertical SPAN would stop
        # reportlab from splitting a long notes block across pages, so A/B are drawn
        # as open (line-less) columns with «توضیحات» in the first row instead.
        style += [
            ("BOX", (0, nb), (-1, ne), 1.4, colors.black),
            ("LINEBELOW", (vx(hi), nb), (vx(lo), ne - 1), 0.5, colors.black),
            ("LINEBEFORE", (vx(1), nb), (vx(1), ne), 1.4, colors.black),
            ("LINEAFTER", (vx(1), nb), (vx(1), ne), 1.4, colors.black),
            ("LINEBEFORE", (vx(lo), nb), (vx(lo), ne), 1.4, colors.black),
        ]
    row_heights = [None] * (len(data) - len(row_heights)) + row_heights if notes else None
    tbl = Table(data, colWidths=vw, rowHeights=row_heights, repeatRows=2)
    tbl.setStyle(TableStyle(g.apply_dark_contrast(tbl, style)))
    doc.build([tbl])
    return g.apply_letterhead(out, letterhead_path)
