"""Shared Excel table styling for product / usage_location sheets.

Applied to منبع اصلی downloads and simple tabular reports so bot + web
exports look consistent.

Palette (entire data-row fill by محل استفاده / usage_location):
  اسلب            — soft blue   #BDD7EE
  بلوم            — soft green  #C6EFCE
  بیلت            — soft peach  #FCE4D6
  سطح ریخته‌گری   — soft lilac  #E2D5F1
  سایر نواحی      — pale steel  #D9E1F2
  ترکیبی          — soft yellow #FFF2CC  (multi-label joined with «، »)
  (empty/blank)   — light gray  #F2F2F2
"""
from __future__ import annotations

from typing import Any

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

# Documented row-fill palette (hex without #)
USAGE_LOCATION_PALETTE: dict[str, str] = {
    "اسلب": "BDD7EE",
    "بلوم": "C6EFCE",
    "بیلت": "FCE4D6",
    "سطح ریخته‌گری": "E2D5F1",
    "سایر نواحی": "D9E1F2",
    "ترکیبی": "FFF2CC",
    "": "F2F2F2",
}

_PRODUCT_HEADERS = frozenset({
    "product_name",
    "material_name",
    "item_code_desc",
    "نام محصول",
    "شرح کالا",
    "محصول",
    "نام کالا",
    "کالا",
})
_USAGE_HEADERS = frozenset({
    "usage_location",
    "محل استفاده",
    "محل‌استفاده",
})

_KNOWN_LOCATION_LABELS = frozenset({
    "اسلب",
    "بلوم",
    "بیلت",
    "سطح ریخته‌گری",
    "سایر نواحی",
})

_MAX_COL_WIDTH = 48.0
_MIN_COL_WIDTH = 8.0
_PRODUCT_COL_WIDTH = 42.0
_PRODUCT_ROW_HEIGHT = 30.0  # ~2 visual lines with wrap
_HEADER_ROW_HEIGHT = 22.0


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def fill_for_usage_location(value: object) -> PatternFill:
    """Return the PatternFill for a usage_location cell value."""
    if value is None:
        return _fill(USAGE_LOCATION_PALETTE[""])
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return _fill(USAGE_LOCATION_PALETTE[""])
    if text in USAGE_LOCATION_PALETTE:
        return _fill(USAGE_LOCATION_PALETTE[text])
    # Multi-label (e.g. «اسلب، بیلت») or unknown composite → ترکیبی
    parts = [p.strip() for p in text.replace(",", "،").split("،") if p.strip()]
    known_hits = [p for p in parts if p in _KNOWN_LOCATION_LABELS]
    if len(known_hits) >= 2 or (len(parts) >= 2 and known_hits):
        return _fill(USAGE_LOCATION_PALETTE["ترکیبی"])
    if len(known_hits) == 1:
        return _fill(USAGE_LOCATION_PALETTE[known_hits[0]])
    # Unrecognized single label — soft gray, not empty
    return _fill(USAGE_LOCATION_PALETTE[""])


def _header_map(ws: Worksheet, header_row: int) -> dict[int, str]:
    out: dict[int, str] = {}
    max_col = ws.max_column or 0
    for col in range(1, max_col + 1):
        raw = ws.cell(header_row, col).value
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            out[col] = text
    return out


def _is_product_header(name: str) -> bool:
    return name in _PRODUCT_HEADERS or name.casefold() in {
        h.casefold() for h in _PRODUCT_HEADERS if h.isascii()
    }


def _is_usage_header(name: str) -> bool:
    return name in _USAGE_HEADERS or name.casefold() in {
        h.casefold() for h in _USAGE_HEADERS if h.isascii()
    }


def _display_len(value: object) -> int:
    if value is None:
        return 0
    text = str(value)
    # Rough width: CJK/Persian count as ~1.2 of Latin for Excel columns
    wide = sum(1 for ch in text if ord(ch) > 0x0600)
    return len(text) + int(wide * 0.2)


def apply_product_table_style(
    ws: Worksheet,
    *,
    header_row: int = 1,
    max_col_width: float = _MAX_COL_WIDTH,
    min_col_width: float = _MIN_COL_WIDTH,
    product_row_height: float = _PRODUCT_ROW_HEIGHT,
    color_by_usage: bool = True,
) -> None:
    """Auto-size columns, wrap product description (~2 lines), color by usage_location.

    Safe no-op on empty sheets. Detects English or Persian header aliases.
    """
    if ws.max_row is None or ws.max_column is None:
        return
    if ws.max_row < header_row:
        return

    headers = _header_map(ws, header_row)
    if not headers:
        return

    product_cols = {c for c, name in headers.items() if _is_product_header(name)}
    usage_cols = {c for c, name in headers.items() if _is_usage_header(name)}
    usage_col = next(iter(sorted(usage_cols)), None)

    # Header style pass (bold already set by callers is fine; ensure wrap)
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, _name in headers.items():
        cell = ws.cell(header_row, col)
        cell.alignment = header_align
    ws.row_dimensions[header_row].height = _HEADER_ROW_HEIGHT

    # Auto-size from header + data
    for col in range(1, (ws.max_column or 0) + 1):
        maxlen = 0
        for row in range(header_row, (ws.max_row or 0) + 1):
            maxlen = max(maxlen, _display_len(ws.cell(row, col).value))
        width = max(min_col_width, min(max_col_width, float(maxlen) + 2.0))
        if col in product_cols:
            width = min(width, _PRODUCT_COL_WIDTH)
        ws.column_dimensions[get_column_letter(col)].width = width

    wrap_align = Alignment(wrap_text=True, vertical="center", horizontal="right")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row in range(header_row + 1, (ws.max_row or 0) + 1):
        fill = None
        if color_by_usage and usage_col is not None:
            fill = fill_for_usage_location(ws.cell(row, usage_col).value)
        for col in range(1, (ws.max_column or 0) + 1):
            cell = ws.cell(row, col)
            if fill is not None:
                cell.fill = fill
            if col in product_cols:
                cell.alignment = wrap_align
            else:
                cell.alignment = center_align
        if product_cols:
            ws.row_dimensions[row].height = product_row_height


def style_workbook_path(
    path: Any,
    *,
    sheet_name: str | None = None,
    header_row: int = 1,
) -> None:
    """Load an .xlsx path, style the active (or named) sheet, save in place."""
    from openpyxl import load_workbook

    wb = load_workbook(path)
    if sheet_name and sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.active
    apply_product_table_style(ws, header_row=header_row)
    wb.save(path)
