"""اقلام بحرانی — shared PDF + xlsx builder for Bale bot AND web panel.

One code path (do not duplicate in bot/web):
  • inventory = ``analytics.frames.load_primary_inventory`` (ledger applied ONCE
    there — callers must NOT re-apply ``apply_inventory_ledger``)
  • rows split by «پیمانکار / شرکت» (``analytics.critical_items`` segments)
  • company PDF (primary, only «شرکت» items), contractor PDF (only «پیمانکار»),
    one xlsx with separate sheets: شرکت | پیمانکار | توضیحات
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from analytics.critical_items import (
    REPORT_COLUMNS,
    SEGMENT_COMPANY,
    SEGMENT_CONTRACTOR,
    SEGMENT_LABEL_FA,
    SEGMENTS,
    TundishMonthCounts,
    build_critical_items_rows,
    report_footer_notes,
    report_subtitle,
    report_title,
    rows_for_simple_report,
)
from config import REPORT_DIR, ensure_dirs
from db.models import Database

SEGMENT_HEADER_BG = {SEGMENT_COMPANY: "#b71c1c", SEGMENT_CONTRACTOR: "#e65100"}
SEGMENT_FILE_SUFFIX = {SEGMENT_COMPANY: "شرکت", SEGMENT_CONTRACTOR: "پیمانکار"}
NOTES_HEADER_BG = "#546e7a"


@dataclass
class CriticalItemsResult:
    counts: TundishMonthCounts | None = None
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    pdfs: dict[str, Path] = field(default_factory=dict)
    xlsx: Path | None = None
    titles: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def row_count(self, segment: str) -> int:
        df = self.frames.get(segment)
        return 0 if df is None else int(len(df))

    @property
    def company_pdf(self) -> Path | None:
        return self.pdfs.get(SEGMENT_COMPANY)

    @property
    def contractor_pdf(self) -> Path | None:
        return self.pdfs.get(SEGMENT_CONTRACTOR)


def month_counts(db: Database, jalali_year: int, jalali_month: int) -> TundishMonthCounts | None:
    stored = db.get_monthly_tundish_counts(int(jalali_year), int(jalali_month))
    if not stored:
        return None
    return TundishMonthCounts(
        jalali_year=int(jalali_year),
        jalali_month=int(jalali_month),
        count_billet=int(stored["count_billet"]),
        count_bloom=int(stored["count_bloom"]),
        count_slab=int(stored["count_slab"]),
    )


def build_critical_items_segments(
    inventory_df: pd.DataFrame | None, counts: TundishMonthCounts
) -> dict[str, pd.DataFrame]:
    """{company: df, contractor: df} — rows already split before aggregation."""
    return {
        seg: build_critical_items_rows(inventory_df, counts, segment=seg)
        for seg in SEGMENTS
    }


def _segment_table_section(segment: str, df: pd.DataFrame) -> dict[str, Any]:
    label = SEGMENT_LABEL_FA[segment]
    return {
        "title": f"اقلام بحرانی — {label}",
        "columns": list(REPORT_COLUMNS),
        "rows": rows_for_simple_report(df),
        "empty_message": f"قلم «{label}» با نرخ و نیاز مثبت برای این ماه یافت نشد.",
        "header_bg": SEGMENT_HEADER_BG[segment],
    }


def _notes_section(counts: TundishMonthCounts) -> dict[str, Any]:
    return {
        "title": "توضیحات",
        "columns": ["توضیح"],
        "rows": [{"توضیح": n} for n in report_footer_notes(counts)],
        "empty_message": "",
        "header_bg": NOTES_HEADER_BG,
    }


def generate_critical_items_files(
    db: Database,
    user: dict[str, Any],
    *,
    jalali_year: int,
    jalali_month: int,
    output_dir: Path | None = None,
    file_prefix: str | None = None,
    letterhead_path: Path | str | None = None,
    inventory_df: pd.DataFrame | None = None,
) -> CriticalItemsResult:
    """Build company PDF, contractor PDF (when it has rows) and a 3-sheet xlsx."""
    from analytics.frames import load_primary_inventory
    from excel.simple_report import generate_simple_report_xlsx
    from pdf.generator import generate_simple_report_pdf

    res = CriticalItemsResult()
    counts = month_counts(db, jalali_year, jalali_month)
    if counts is None:
        res.error = "برای این ماه تعداد تاندیش ثبت نشده است."
        return res
    res.counts = counts
    # load_primary_inventory already applies inventory_ledger once.
    inv = inventory_df if inventory_df is not None else load_primary_inventory(db, user)
    if inv is None or inv.empty:
        res.error = "منبع اصلی یافت نشد."
        return res
    res.frames = build_critical_items_segments(inv, counts)
    if all(df is None or df.empty for df in res.frames.values()):
        res.error = "قلمی با نرخ و نیاز مثبت برای این تعداد تاندیش یافت نشد."
        return res

    ensure_dirs()
    out_dir = Path(output_dir) if output_dir else REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = file_prefix or f"critical_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    notes = _notes_section(counts)

    for seg in SEGMENTS:
        df = res.frames[seg]
        title = report_title(counts, seg)
        res.titles[seg] = title
        if df is None or df.empty:
            continue
        pdf_path = out_dir / f"{prefix}_{SEGMENT_FILE_SUFFIX[seg]}.pdf"
        generate_simple_report_pdf(
            title,
            subtitle=report_subtitle(counts, row_count=len(df), segment=seg),
            sections=[_segment_table_section(seg, df), notes],
            output_path=pdf_path,
            filename_stem="critical_items",
            letterhead_path=letterhead_path,
        )
        res.pdfs[seg] = pdf_path

    xlsx_title = report_title(counts)
    xlsx_sub = (
        f"شرکت: {res.row_count(SEGMENT_COMPANY)} قلم | "
        f"پیمانکار: {res.row_count(SEGMENT_CONTRACTOR)} قلم | "
        f"تعداد تاندیش — بیلت: {counts.count_billet}، بلوم: {counts.count_bloom}، "
        f"اسلب: {counts.count_slab}"
    )
    res.xlsx = generate_simple_report_xlsx(
        xlsx_title,
        subtitle=xlsx_sub,
        sections=[
            _segment_table_section(SEGMENT_COMPANY, res.frames[SEGMENT_COMPANY]),
            _segment_table_section(SEGMENT_CONTRACTOR, res.frames[SEGMENT_CONTRACTOR]),
            notes,
        ],
        output_path=out_dir / f"{prefix}.xlsx",
        filename_stem="critical_items",
    )
    res.titles["xlsx"] = xlsx_title
    return res
