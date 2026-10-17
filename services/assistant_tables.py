"""جدول پاسخ‌های دستیار: متن جدولی برای بله، و PDF (Vazirmatn، راست‌به‌چپ) / اکسل (Tahoma) با همان
سازندهٔ گزارش‌ها (``pdf.generator.generate_simple_report_pdf`` و ``excel.simple_report``).

``Answer.table`` = {"title", "subtitle", "stem", "sections": [{"title", "columns", "rows", ...}]}
(همان قالب sections گزارش‌های ساده: columns = کلیدها، rows = dict).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

SEP = " │ "


def _cell(v: Any, col: str | None = None) -> str:
    try:
        from pdf.generator import _format_simple_cell

        s = _format_simple_cell(v, col)
    except Exception:  # noqa: BLE001
        s = "" if v is None else str(v)
    return " ".join(s.split())


def text_table(columns: list[str], rows: list[dict[str, Any]], *, max_rows: int = 10, widths: dict[str, int] | None = None,
               header_map: dict[str, str] | None = None) -> list[str]:
    """Compact text table for Bale (header, rule, ≤max_rows rows, «… و N ردیف دیگر»)."""
    if not columns or not rows:
        return []
    widths = widths or {}
    hm = header_map or {}
    head = SEP.join(hm.get(c, c) for c in columns)
    out = [head, "─" * min(max(len(head), 12), 34)]
    for r in rows[:max_rows]:
        cells = []
        for c in columns:
            s = _cell(r.get(c), c)
            w = widths.get(c)
            if w and len(s) > w:
                s = s[: w - 1] + "…"
            cells.append(s or "—")
        out.append(SEP.join(cells))
    if len(rows) > max_rows:
        out.append(f"… و {len(rows) - max_rows} ردیف دیگر (جدول کامل در فایل PDF)")
    return out


def has_table(table: dict | None) -> bool:
    return bool(table and any(s.get("rows") for s in table.get("sections") or []))


def write_files(table: dict, out_dir: Path | str, *, xlsx: bool = True,
                letterhead_path: Path | str | None = None) -> dict[str, Path]:
    """PDF (+ Excel) of the answer table. Never raises; returns what was written."""
    import logging

    out: dict[str, Path] = {}
    if not has_table(table):
        return out
    from services.file_names import fa_stem

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    title = str(table.get("title") or "پاسخ دستیار")
    import re

    stem = fa_stem(re.sub(r"\s*—?\s*\d{4}/\d{2}/\d{2}\s*$", "", title))
    sections = [s for s in table["sections"] if s.get("columns")]
    try:
        from pdf.generator import generate_simple_report_pdf

        out["pdf"] = generate_simple_report_pdf(title, subtitle=table.get("subtitle"), sections=sections,
                                                output_path=d / f"{stem}.pdf", letterhead_path=letterhead_path)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("assistant table PDF failed")
    if xlsx:
        try:
            from excel.simple_report import generate_simple_report_xlsx

            out["xlsx"] = generate_simple_report_xlsx(title, subtitle=table.get("subtitle"), sections=sections,
                                                      output_path=d / f"{stem}.xlsx")
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("assistant table Excel failed")
    return out
