"""اقلام بحرانی — shared PDF + xlsx builder for Bale bot AND web panel.

One code path (do not duplicate in bot/web):
  • inventory = ``analytics.frames.load_primary_inventory`` (ledger applied ONCE
    there — callers must NOT re-apply ``apply_inventory_ledger``)
  • rows split by «تأمین‌کننده» (شرکت/پیمانکار) (``analytics.critical_items`` segments)
  • mode «با نوسازی» (renovation + patching) / «بدون نوسازی» (patching only)
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
    COL_DAYS,
    COL_FORECAST,
    COL_HORIZON,
    COL_MONTHLY,
    COL_NEED,
    COL_ORIGIN,
    RENO_LABEL_FA,
    RENO_WITH,
    RENO_WITHOUT,
    REPORT_COLUMNS,
    SEGMENT_COMPANY,
    SEGMENT_CONTRACTOR,
    SEGMENT_LABEL_FA,
    SEGMENTS,
    TundishMonthCounts,
    average_tundish_basis,
    build_critical_items_rows,
    horizon_header_note,
    origin_audit,
    critical_item_count,
    group_row_indices,
    normalize_reno_mode,
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
RENO_FILE_SUFFIX = {RENO_WITH: "با_نوسازی", RENO_WITHOUT: "بدون_نوسازی"}


@dataclass
class CriticalItemsResult:
    counts: TundishMonthCounts | None = None
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    pdfs: dict[str, Path] = field(default_factory=dict)
    xlsx: Path | None = None
    titles: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    reno_mode: str = RENO_WITH
    audit: dict[str, list[str]] = field(default_factory=dict)

    @property
    def header_note(self) -> str:
        return horizon_header_note(self.counts) if self.counts is not None else ""

    @property
    def reno_label(self) -> str:
        return RENO_LABEL_FA[self.reno_mode]

    def row_count(self, segment: str) -> int:
        # Items = plain codes + shared-need groups (member rows not counted).
        return critical_item_count(self.frames.get(segment))

    @property
    def company_pdf(self) -> Path | None:
        return self.pdfs.get(SEGMENT_COMPANY)

    @property
    def contractor_pdf(self) -> Path | None:
        return self.pdfs.get(SEGMENT_CONTRACTOR)


def month_counts(db: Database, jalali_year: int, jalali_month: int) -> TundishMonthCounts | None:
    """Tundish basis = AVERAGE of the manual monthly counts of the 3 months ending at
    (jalali_year, jalali_month); months without an entry are skipped (named in header)."""
    return average_tundish_basis(
        int(jalali_year), int(jalali_month), db.list_monthly_tundish_counts(limit=240)
    )


def build_critical_items_segments(
    inventory_df: pd.DataFrame | None,
    counts: TundishMonthCounts,
    reno_mode: str = RENO_WITH,
) -> dict[str, pd.DataFrame]:
    """{company: df, contractor: df} — rows already split before aggregation."""
    return {
        seg: build_critical_items_rows(
            inventory_df, counts, segment=seg, reno_mode=reno_mode
        )
        for seg in SEGMENTS
    }


def _segment_table_section(segment: str, df: pd.DataFrame, reno_mode: str) -> dict[str, Any]:
    label = SEGMENT_LABEL_FA[segment]
    mode = RENO_LABEL_FA[reno_mode]
    return {
        "title": f"اقلام بحرانی — {label} ({mode})",
        "sheet_title": f"اقلام بحرانی — {label}",
        "columns": list(REPORT_COLUMNS),
        "rows": rows_for_simple_report(df),
        # Shared-need group subtotal rows are bold + shaded (PDF and xlsx).
        "bold_rows": group_row_indices(df),
        "empty_message": (
            f"قلم «{label}» با کسری (نیاز مثبت) در افق ۳/۶ ماه ({mode}) یافت نشد."
        ),
        "header_bg": SEGMENT_HEADER_BG[segment],
    }


def _notes_section(
    counts: TundishMonthCounts, reno_mode: str, audit: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    return {
        "title": "توضیحات",
        "columns": ["توضیح"],
        "rows": [{"توضیح": n} for n in report_footer_notes(counts, reno_mode, audit)],
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
    reno_mode: str = RENO_WITH,
) -> CriticalItemsResult:
    """Build company PDF, contractor PDF (when it has rows) and a 3-sheet xlsx.

    ``reno_mode``: RENO_WITH «با نوسازی» (default) | RENO_WITHOUT «بدون نوسازی».
    """
    from analytics.frames import load_primary_inventory
    from excel.simple_report import generate_simple_report_xlsx
    from pdf.generator import generate_simple_report_pdf

    reno_mode = normalize_reno_mode(reno_mode)
    res = CriticalItemsResult(reno_mode=reno_mode)
    counts = month_counts(db, jalali_year, jalali_month)
    if counts is None:
        res.error = (
            "برای ۳ ماه منتهی به این ماه هیچ «تعداد تاندیش ماهانه» ثبت نشده است "
            "(مبنای میانگین مصرف ماهانه)."
        )
        return res
    res.counts = counts
    # load_primary_inventory already applies inventory_ledger once.
    inv = inventory_df if inventory_df is not None else load_primary_inventory(db, user)
    if inv is None or inv.empty:
        res.error = "منبع اصلی یافت نشد."
        return res
    res.frames = build_critical_items_segments(inv, counts, reno_mode)
    res.audit = {"missing": [], "mixed": []}
    for seg in SEGMENTS:
        a = origin_audit(inv, seg)
        for k in res.audit:
            res.audit[k] += [c for c in a.get(k, []) if c not in res.audit[k]]
    if all(df is None or df.empty for df in res.frames.values()):
        res.error = (
            "قلمی با کسری (نیاز مثبت) در افق ۳ ماه (داخلی) / ۶ ماه (وارداتی) "
            f"({RENO_LABEL_FA[reno_mode]}) یافت نشد. {horizon_header_note(counts)}"
        )
        return res

    ensure_dirs()
    out_dir = Path(output_dir) if output_dir else REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = file_prefix or f"critical_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    prefix = f"{prefix}_{RENO_FILE_SUFFIX[reno_mode]}"
    notes = _notes_section(counts, reno_mode, res.audit)

    for seg in SEGMENTS:
        df = res.frames[seg]
        title = report_title(counts, seg, reno_mode)
        res.titles[seg] = title
        if df is None or df.empty:
            continue
        pdf_path = out_dir / f"{prefix}_{SEGMENT_FILE_SUFFIX[seg]}.pdf"
        generate_simple_report_pdf(
            title,
            subtitle=report_subtitle(
                counts,
                row_count=critical_item_count(df),
                segment=seg,
                reno_mode=reno_mode,
            ),
            sections=[_segment_table_section(seg, df, reno_mode), notes],
            output_path=pdf_path,
            filename_stem="critical_items",
            letterhead_path=letterhead_path,
        )
        res.pdfs[seg] = pdf_path

    xlsx_title = report_title(counts, None, reno_mode)
    xlsx_sub = (
        f"حالت: {RENO_LABEL_FA[reno_mode]} | "
        f"شرکت: {res.row_count(SEGMENT_COMPANY)} قلم | "
        f"پیمانکار: {res.row_count(SEGMENT_CONTRACTOR)} قلم | "
        f"{horizon_header_note(counts)}"
    )
    res.xlsx = generate_simple_report_xlsx(
        xlsx_title,
        subtitle=xlsx_sub,
        sections=[
            _segment_table_section(SEGMENT_COMPANY, res.frames[SEGMENT_COMPANY], reno_mode),
            _segment_table_section(
                SEGMENT_CONTRACTOR, res.frames[SEGMENT_CONTRACTOR], reno_mode
            ),
            notes,
        ],
        output_path=out_dir / f"{prefix}.xlsx",
        filename_stem="critical_items",
    )
    res.titles["xlsx"] = xlsx_title
    return res


BOT_TOP_ROWS = 8


def critical_items_bot_lines(res: CriticalItemsResult, top: int = BOT_TOP_ROWS) -> list[str]:
    """Bot summary text: per segment count + the most urgent rows (with the
    «میانگین مصرف ماهانه بر اساس ۳ ماه گذشته» column), plus origin flags."""
    lines: list[str] = []
    for seg, tag in ((SEGMENT_COMPANY, "شرکت (گزارش اصلی)"),
                     (SEGMENT_CONTRACTOR, "پیمانکار (گزارش جداگانه)")):
        df = res.frames.get(seg)
        n = res.row_count(seg)
        lines.append(f"• {tag}: {n} قلم" + ("" if n else " — قلمی با نیاز مثبت نبود"))
        if df is None or df.empty:
            continue
        shown = 0
        for _, r in df.iterrows():
            if str(r.get("ردیف", "")).strip() == "":
                continue  # member rows of a shared-need group
            if shown >= top:
                break
            shown += 1
            lines.append(
                f"  {r.get('کد چهاررقمی', '')} {r.get('کد و شرح کالا', '')} — "
                f"{r.get(COL_ORIGIN, '')}، افق {r.get(COL_HORIZON, '')} ماه | "
                f"موجودی {r.get('موجودی', '')} {r.get('واحد', '')} | "
                f"{COL_MONTHLY}: {r.get(COL_MONTHLY, '')} | "
                f"{COL_FORECAST}: {r.get(COL_FORECAST, '')} | "
                f"{COL_NEED}: {r.get(COL_NEED, '')} | {COL_DAYS}: {r.get(COL_DAYS, '')}"
            )
        if n > shown:
            lines.append(f"  … و {n - shown} قلم دیگر (در PDF/اکسل)")
    audit = res.audit or {}
    if audit.get("missing"):
        lines.append(
            "⚠️ مبدأ نامشخص (داخلی/۳ ماه فرض شد): " + "، ".join(audit["missing"])
        )
    if audit.get("mixed"):
        lines.append("ℹ️ مبدأ مختلط (افق ۶ ماه): " + "، ".join(audit["mixed"]))
    return lines
