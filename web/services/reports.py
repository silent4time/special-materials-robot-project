"""Build report PDFs for the web dashboard (reuses analytics + pdf.generator)."""
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


def _stamp_path(stem: str) -> Path:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return REPORT_DIR / f"web_{stem}_{stamp}.pdf"


def generate_remaining_critical_pdf(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, str | None]:
    """Returns (path, error_fa). Empty data → Persian error, no file."""
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
        return None, "داده‌ای برای موجودی یافت نشد. ابتدا موجودی سایت یا فایل انبار را ثبت کنید."
    crit = critical_materials(rates, rem, CRITICAL_DAYS)
    rem_cols = ["material_name", "remaining_qty", "unit", "location"]
    crit_cols = ["material_name", "remaining_qty", "avg_daily", "days_of_cover", "unit"]
    range_label = f"{days} روز اخیر"
    path = _stamp_path("remaining_critical")
    generate_simple_report_pdf(
        title=f"موجودی و مواد بحرانی — {range_label}",
        subtitle=(
            f"منبع موجودی: {rem_source} | نرخ مصرف بر اساس {range_label} | "
            f"آستانه بحرانی: پوشش < {CRITICAL_DAYS} روز"
        ),
        sections=[
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
        ],
        output_path=path,
        filename_stem="remaining_critical",
        letterhead_path=letterhead_path(db),
    )
    return path, None


def generate_surplus_pdf(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, str | None]:
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
        return None, "داده‌ای برای محاسبه مازاد یافت نشد (منبع اصلی)."
    surplus = surplus_materials(rates, rem)
    if surplus is None or surplus.empty:
        return None, "ماده مازادی شناسایی نشد."
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
    path = _stamp_path("surplus")
    range_label = f"{days} روز اخیر"
    generate_simple_report_pdf(
        title=f"گزارش مواد مازاد — {range_label}",
        subtitle=(
            f"منبع موجودی: {rem_source} | نرخ مصرف بر اساس {range_label} | "
            f"تعریف: پوشش > {cover_th:g} روز"
        ),
        columns=cols,
        rows=_df_rows(surplus, cols),
        empty_message="ماده مازادی شناسایی نشد.",
        output_path=path,
        filename_stem="surplus",
        letterhead_path=letterhead_path(db),
    )
    return path, None


def remaining_or_site(
    db: Database, frames: dict[str, Any]
) -> pd.DataFrame:
    """Backward-compat: site preferred (remaining/critical), else منبع اصلی."""
    rem, _ = resolve_remaining(db, frames)
    return rem


def generate_user_activity_pdf(
    db: Database, user: dict[str, Any], *, days: int = 30
) -> tuple[Path | None, str | None]:
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
        return None, "در این بازه فعالیتی ثبت نشده است."
    pdf_rows = [
        {"زمان": format_datetime(r.get("created_at")), "فعالیت": r.get("message_fa") or ""}
        for r in rows
    ]
    path = _stamp_path("user_activity")
    range_label = f"{days} روز اخیر"
    generate_simple_report_pdf(
        title=f"گزارش فعالیت کاربران — {range_label}",
        subtitle=f"{len(pdf_rows)} فعالیت (جدیدترین ابتدا)",
        columns=["زمان", "فعالیت"],
        rows=pdf_rows,
        empty_message="در این بازه داده‌ای برای این گزارش نیست.",
        output_path=path,
        filename_stem="user_activity",
        letterhead_path=letterhead_path(db),
    )
    return path, None


def generate_monthly_summary_web(
    db: Database, user: dict[str, Any]
) -> tuple[Path | None, str | None]:
    """Latest monthly extract → PDF summary for current Jalali month if possible."""
    from excel.monthly_summary import (
        SUMMARY_FILE_NAME,
        build_monthly_summary,
        summary_sections_for_pdf,
    )

    extract = db.get_latest_extracted(user["bale_user_id"], "monthly_consumption")
    if not extract:
        extract = db.get_latest_extracted_any("monthly_consumption")
    if not extract:
        return None, "فایل مصرف ماهیانه مواد یافت نشد."
    source = None
    for key in ("raw_path", "clean_path"):
        p = extract.get(key)
        if p and Path(str(p)).is_file():
            source = Path(str(p))
            break
    if source is None:
        return None, "مسیر فایل مصرف ماهیانه روی دیسک یافت نشد."
    try:
        ensure_dirs()
        excel_path = REPORT_DIR / f"web_{SUMMARY_FILE_NAME}"
        data, _ = build_monthly_summary(source, excel_out=excel_path)
        sections = summary_sections_for_pdf(data)
        path = REPORT_DIR / "web_خلاصه_مصرفی_ماهیانه.pdf"
        generate_monthly_summary_pdf(
            sections,
            grand_kg=data.grand_kg,
            output_path=path,
            title="خلاصه مصرفی ماهیانه",
            letterhead_path=letterhead_path(db),
        )
        return path, None
    except ValueError as exc:
        return None, str(exc) or "در این بازه داده‌ای برای خلاصه مصرف نیست."
    except Exception as exc:  # noqa: BLE001
        logger.exception("monthly summary web failed")
        return None, f"خطا در تولید خلاصه: {exc}"


def data_status(db: Database, user: dict[str, Any]) -> dict[str, Any]:
    frames = load_frames(db, user)
    return {
        "completeness": frames_completeness(frames),
        "site_stock_date": db.get_latest_site_stock_date(),
    }
