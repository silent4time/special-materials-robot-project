"""Build report PDFs + Excel for the web dashboard (reuses analytics + shared generators)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from analytics.tundish import (
    critical_materials,
    daily_rates,
    surplus_materials,
)
from bot.jalali import format_datetime
from config import CRITICAL_DAYS, REPORT_DIR, SURPLUS_COVER_DAYS, ensure_dirs
from db.models import Database
from excel.simple_report import generate_simple_report_xlsx
from pdf.generator import generate_monthly_summary_pdf, generate_simple_report_pdf
from web.services.data import (
    frames_completeness,
    letterhead_path,
    load_frames,
    resolve_remaining,
    resolve_warehouse_remaining,
)

logger = logging.getLogger(__name__)


def _df_rows(df: pd.DataFrame | None, cols: list[str]) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    work = df.copy()
    for c in cols:
        if c not in work.columns:
            work[c] = None
    return work[cols].to_dict(orient="records")


def _stamp_stem(stem: str) -> str:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"web_{stem}_{stamp}"


def generate_remaining_critical_pdf(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, str | None]:
    """Returns (pdf_path, error_fa). Also writes sibling .xlsx next to the PDF."""
    pdf, xlsx, err = generate_remaining_critical_files(db, user, days=days)
    _ = xlsx
    return pdf, err


def generate_remaining_critical_files(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, Path | None, str | None]:
    """Returns (pdf_path, xlsx_path, error_fa). Empty data → Persian error, no files."""
    frames = load_frames(db, user)
    end = date.today()
    start = end - timedelta(days=max(0, days - 1))
    rates = daily_rates(
        frames.get("tank_consumption"),
        frames.get("monthly_consumption"),
        start=start,
        end=end,
    )
    rem, rem_source = resolve_remaining(db, frames)
    if rem is None or rem.empty:
        return None, None, "داده‌ای برای موجودی یافت نشد. ابتدا موجودی سایت یا فایل انبار را ثبت کنید."
    crit = critical_materials(rates, rem, CRITICAL_DAYS)
    rem_cols = ["material_name", "remaining_qty", "unit", "location"]
    crit_cols = ["material_name", "remaining_qty", "avg_daily", "days_of_cover", "unit"]
    range_label = f"{days} روز اخیر"
    title = f"موجودی و مواد بحرانی — {range_label}"
    subtitle = (
        f"منبع موجودی: {rem_source} | نرخ مصرف بر اساس {range_label} | "
        f"آستانه بحرانی: پوشش < {CRITICAL_DAYS} روز"
    )
    sections = [
        {
            "title": f"موجودی باقیمانده ({rem_source})",
            "columns": rem_cols,
            "rows": _df_rows(rem, rem_cols),
            "empty_message": "موجودی خالی است.",
            "header_bg": "#2e7d32",
        },
        {
            "title": f"مواد بحرانی (پوشش < {CRITICAL_DAYS} روز)",
            "columns": crit_cols,
            "rows": _df_rows(crit, crit_cols),
            "empty_message": "ماده بحرانی‌ای شناسایی نشد.",
            "header_bg": "#c62828",
        },
    ]
    stem = _stamp_stem("remaining_critical")
    pdf_path = REPORT_DIR / f"{stem}.pdf"
    xlsx_path = REPORT_DIR / f"{stem}.xlsx"
    generate_simple_report_pdf(
        title=title,
        subtitle=subtitle,
        sections=sections,
        output_path=pdf_path,
        filename_stem="remaining_critical",
        letterhead_path=letterhead_path(db),
    )
    generate_simple_report_xlsx(
        title=title,
        subtitle=subtitle,
        sections=sections,
        output_path=xlsx_path,
        filename_stem="remaining_critical",
    )
    return pdf_path, xlsx_path, None


def generate_surplus_pdf(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, str | None]:
    pdf, xlsx, err = generate_surplus_files(db, user, days=days)
    _ = xlsx
    return pdf, err


def generate_surplus_files(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, Path | None, str | None]:
    frames = load_frames(db, user)
    end = date.today()
    start = end - timedelta(days=max(0, days - 1))
    rates = daily_rates(
        frames.get("tank_consumption"),
        frames.get("monthly_consumption"),
        start=start,
        end=end,
    )
    rem, rem_source = resolve_warehouse_remaining(db, frames)
    if rem is None or rem.empty:
        return None, None, "داده‌ای برای محاسبه مازاد یافت نشد (منبع اصلی)."
    surplus = surplus_materials(rates, rem)
    if surplus is None or surplus.empty:
        return None, None, "ماده مازادی شناسایی نشد."
    cover_th = max(float(CRITICAL_DAYS) * 3.0, float(SURPLUS_COVER_DAYS))
    cols = [
        "material_name",
        "remaining_qty",
        "surplus_qty",
        "avg_daily",
        "days_of_cover",
        "forecast_need",
        "unit",
        "surplus_reason",
    ]
    range_label = f"{days} روز اخیر"
    title = f"گزارش مواد مازاد — {range_label}"
    subtitle = (
        f"منبع موجودی: {rem_source} | نرخ مصرف بر اساس {range_label} | "
        f"تعریف: پوشش > {cover_th:g} روز؛ "
        f"کد دسته ۱۸۰۰ به‌صورت خودکار اقلام مازاد محسوب می‌شود"
    )
    rows = _df_rows(surplus, cols)
    stem = _stamp_stem("surplus")
    pdf_path = REPORT_DIR / f"{stem}.pdf"
    xlsx_path = REPORT_DIR / f"{stem}.xlsx"
    generate_simple_report_pdf(
        title=title,
        subtitle=subtitle,
        columns=cols,
        rows=rows,
        empty_message="ماده مازادی شناسایی نشد.",
        output_path=pdf_path,
        filename_stem="surplus",
        letterhead_path=letterhead_path(db),
    )
    generate_simple_report_xlsx(
        title=title,
        subtitle=subtitle,
        columns=cols,
        rows=rows,
        empty_message="ماده مازادی شناسایی نشد.",
        output_path=xlsx_path,
        filename_stem="surplus",
    )
    return pdf_path, xlsx_path, None


def remaining_or_site(
    db: Database, frames: dict[str, Any]
) -> pd.DataFrame:
    """Backward-compat: site preferred (remaining/critical), else منبع اصلی."""
    rem, _ = resolve_remaining(db, frames)
    return rem


def generate_user_activity_pdf(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, str | None]:
    pdf, xlsx, err = generate_user_activity_files(db, user, days=days)
    _ = xlsx
    return pdf, err


def generate_user_activity_files(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, Path | None, str | None]:
    tehran = ZoneInfo("Asia/Tehran")
    end_g = date.today()
    start_g = end_g - timedelta(days=max(0, days - 1))
    start_local = datetime.combine(start_g, datetime.min.time(), tzinfo=tehran)
    end_local = datetime.combine(end_g, datetime.max.time().replace(microsecond=0), tzinfo=tehran)
    rows = db.list_user_activities(
        start_iso=start_local.astimezone(ZoneInfo("UTC")).isoformat(),
        end_iso=end_local.astimezone(ZoneInfo("UTC")).isoformat(),
        newest_first=True,
    )
    if not rows:
        return None, None, "در این بازه فعالیتی ثبت نشده است."
    pdf_rows = [
        {"زمان": format_datetime(r.get("created_at")), "فعالیت": r.get("message_fa") or ""}
        for r in rows
    ]
    range_label = f"{days} روز اخیر"
    title = f"گزارش فعالیت کاربران — {range_label}"
    subtitle = f"{len(pdf_rows)} فعالیت (جدیدترین ابتدا)"
    stem = _stamp_stem("user_activity")
    pdf_path = REPORT_DIR / f"{stem}.pdf"
    xlsx_path = REPORT_DIR / f"{stem}.xlsx"
    generate_simple_report_pdf(
        title=title,
        subtitle=subtitle,
        columns=["زمان", "فعالیت"],
        rows=pdf_rows,
        empty_message="در این بازه داده‌ای برای این گزارش نیست.",
        output_path=pdf_path,
        filename_stem="user_activity",
        letterhead_path=letterhead_path(db),
    )
    generate_simple_report_xlsx(
        title=title,
        subtitle=subtitle,
        columns=["زمان", "فعالیت"],
        rows=pdf_rows,
        empty_message="در این بازه داده‌ای برای این گزارش نیست.",
        output_path=xlsx_path,
        filename_stem="user_activity",
    )
    return pdf_path, xlsx_path, None


def generate_monthly_summary_web(
    db: Database, user: dict[str, Any]
) -> tuple[Path | None, str | None]:
    """Latest monthly extract → PDF summary; also writes sibling Excel when built."""
    pdf, xlsx, err = generate_monthly_summary_files(db, user)
    _ = xlsx
    return pdf, err


def generate_monthly_summary_files(
    db: Database, user: dict[str, Any]
) -> tuple[Path | None, Path | None, str | None]:
    """Latest monthly extract → PDF + Excel summary."""
    from excel.monthly_summary import (
        SUMMARY_FILE_NAME,
        build_monthly_summary,
        summary_sections_for_pdf,
    )

    extract = db.get_latest_extracted(user["bale_user_id"], "monthly_consumption")
    if not extract:
        extract = db.get_latest_extracted_any("monthly_consumption")
    if not extract:
        return None, None, "فایل مصرف ماهیانه مواد یافت نشد."
    source = None
    for key in ("raw_path", "clean_path"):
        p = extract.get(key)
        if p and Path(str(p)).is_file():
            source = Path(str(p))
            break
    if source is None:
        return None, None, "مسیر فایل مصرف ماهیانه روی دیسک یافت نشد."
    try:
        ensure_dirs()
        excel_path = REPORT_DIR / f"web_{SUMMARY_FILE_NAME}"
        data, excel_path = build_monthly_summary(source, excel_out=excel_path)
        sections = summary_sections_for_pdf(data)
        path = REPORT_DIR / "web_خلاصه_مصرفی_ماهیانه.pdf"
        generate_monthly_summary_pdf(
            sections,
            grand_kg=data.grand_kg,
            output_path=path,
            title="خلاصه مصرفی ماهیانه",
            letterhead_path=letterhead_path(db),
        )
        return path, Path(excel_path), None
    except ValueError as exc:
        return None, None, str(exc) or "در این بازه داده‌ای برای خلاصه مصرف نیست."
    except Exception as exc:  # noqa: BLE001
        logger.exception("monthly summary web failed")
        return None, None, f"خطا در تولید خلاصه: {exc}"


def data_status(db: Database, user: dict[str, Any]) -> dict[str, Any]:
    from web.services.data import data_completeness

    frames = load_frames(db, user)
    return {
        "completeness": data_completeness(db, user),
        "frames_completeness": frames_completeness(frames),
        "site_stock_date": db.get_latest_site_stock_date(),
    }
