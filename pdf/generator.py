"""Generate a combined Persian/RTL PDF report from Excel datasets + tundish analytics."""
from __future__ import annotations

from datetime import datetime

from bot.jalali import format_date, format_datetime, tehran_now
from pathlib import Path
from typing import Any

import arabic_reshaper
import pandas as pd
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config import CRITICAL_DAYS, FILE_TYPES, FONTS_DIR, REPORT_DIR, ensure_dirs

_FONT_REGISTERED = False
FONT_NAME = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"


def _register_fonts() -> None:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    regular = FONTS_DIR / "DejaVuSans.ttf"
    bold = FONTS_DIR / "DejaVuSans-Bold.ttf"
    if not regular.exists():
        raise FileNotFoundError(
            f"فونت فارسی یافت نشد: {regular}. پوشه fonts را بررسی کنید."
        )
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(regular)))
    if bold.exists():
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
    else:
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(regular)))
    _FONT_REGISTERED = True


def rtl(text: Any) -> str:
    s = "" if text is None else str(text)
    if not s.strip():
        return ""
    try:
        reshaped = arabic_reshaper.reshape(s)
        return get_display(reshaped)
    except Exception:  # noqa: BLE001
        return s


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleFA",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=16,
            alignment=TA_CENTER,
            leading=22,
            spaceAfter=12,
        ),
        "heading": ParagraphStyle(
            "HeadingFA",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=12,
            alignment=TA_RIGHT,
            leading=18,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "BodyFA",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=9,
            alignment=TA_RIGHT,
            leading=14,
        ),
        "cell": ParagraphStyle(
            "CellFA",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=7,
            alignment=TA_CENTER,
            leading=10,
        ),
    }


SECTION_ORDER = (
    "tank_consumption",
    "product_inventory",
    "monthly_consumption",
)

DISPLAY_COLUMNS = {
    "tank_consumption": [
        "domain",
        "tundish_type",
        "tundish_id",
        "material_name",
        "quantity",
        "unit",
        "assignee_name",
        "date",
        "notes",
    ],
    "product_inventory": [
        "category_code",
        "id",
        "product_name",
        "item_code_desc",
        "quantity",
        "priority",
    ],
    "monthly_consumption": [
        "domain",
        "tundish_type",
        "material_name",
        "month",
        "quantity",
        "unit",
        "status",
        "assignee_name",
        "notes",
    ],
}

HEADER_FA = {
    "domain": "حوزه",
    "tundish_type": "نوع تاندیش",
    "assignee_id": "شناسه",
    "assignee_name": "مسئول",
    "tundish_id": "تاندیش",
    "material_name": "ماده",
    "product_name": "محصول",
    "quantity": "مقدار",
    "unit": "واحد",
    "date": "تاریخ",
    "month": "ماه",
    "location": "محل",
    "status": "وضعیت",
    "notes": "توضیحات",
    "avg_daily": "میانگین روزانه",
    "total_qty": "مجموع",
    "days_span": "روزهای بازه",
    "remaining_qty": "باقیمانده",
    "days_of_cover": "روز پوشش",
    "forecast_need": "نیاز پیش‌بینی",
    "suggest_qty": "پیشنهاد درخواست",
    "days": "روز",
    "source": "منبع",
    "start": "از",
    "end": "تا",
    "critical": "بحرانی",
}


def _df_to_table(
    df: pd.DataFrame,
    cols: list[str] | None,
    styles: dict,
    *,
    max_rows: int = 200,
    header_bg: str = "#1f4e79",
) -> Table | Paragraph:
    use_cols = [c for c in (cols or list(df.columns)) if c in df.columns]
    if not use_cols:
        use_cols = list(df.columns)[:8]
    if df is None or df.empty or not use_cols:
        return Paragraph(rtl("هیچ ردیفی یافت نشد."), styles["body"])

    header = [Paragraph(rtl(HEADER_FA.get(c, c)), styles["cell"]) for c in use_cols]
    header = list(reversed(header))
    data = [header]
    view = df.head(max_rows)
    for _, row in view.iterrows():
        cells = []
        for c in use_cols:
            val = row.get(c, "")
            if c in {"date", "start", "end", "entry_date", "created_at"} and val not in ("", None):
                try:
                    from datetime import date as _date, datetime as _datetime
                    if isinstance(val, _datetime):
                        val = format_datetime(val)
                    elif isinstance(val, _date) or (isinstance(val, str) and len(str(val)) >= 8):
                        # date-only columns → Jalali date; created_at → datetime
                        val = format_datetime(val) if c in {"created_at"} else format_date(val)
                except Exception:  # noqa: BLE001
                    val = str(val)
            if isinstance(val, float):
                if val != val:  # NaN
                    val = ""
                elif val == float("inf"):
                    val = "∞"
                else:
                    val = f"{val:.2f}"
            cells.append(Paragraph(rtl(val), styles["cell"]))
        data.append(list(reversed(cells)))

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.Color(0.92, 0.95, 1)]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _append_analytics(story: list, analytics: dict[str, Any], styles: dict) -> None:
    if not analytics:
        return
    start = analytics.get("start")
    end = analytics.get("end")
    days = analytics.get("days")
    crit_days = analytics.get("critical_days", CRITICAL_DAYS)
    range_label = ""
    if start and end:
        range_label = f" (بازه {format_date(start)} تا {format_date(end)} — {days} روز)"

    story.append(Paragraph(rtl("تحلیل تاندیش"), styles["heading"]))
    story.append(
        Paragraph(
            rtl(
                f"آستانه مواد بحرانی: پوشش موجودی کمتر از {crit_days} روز "
                f"(CRITICAL_DAYS). پیشنهاد درخواست = max(0, نیاز پیش‌بینی − موجودی)."
            ),
            styles["body"],
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph(rtl("۱) خلاصه مصرف روزانه مواد"), styles["heading"]))
    story.append(
        _df_to_table(
            analytics.get("daily_rates") if analytics.get("daily_rates") is not None else pd.DataFrame(),
            ["material_name", "tundish_type", "tundish_id", "avg_daily", "total_qty", "days_span", "unit", "source"],
            styles,
            header_bg="#0d47a1",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph(rtl(f"۲) مصرف در بازه درخواستی{range_label}"), styles["heading"]))
    story.append(
        _df_to_table(
            analytics.get("period_consumption") if analytics.get("period_consumption") is not None else pd.DataFrame(),
            ["material_name", "tundish_type", "tundish_id", "quantity", "unit", "start", "end"],
            styles,
            header_bg="#1565c0",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph(rtl("۳) موجودی باقیمانده و مواد بحرانی"), styles["heading"]))
    story.append(
        _df_to_table(
            analytics.get("remaining") if analytics.get("remaining") is not None else pd.DataFrame(),
            ["material_name", "remaining_qty", "unit", "location"],
            styles,
            header_bg="#2e7d32",
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    story.append(
        Paragraph(
            rtl(f"مواد در حال اتمام (بحرانی — پوشش < {crit_days} روز):"),
            styles["body"],
        )
    )
    story.append(
        _df_to_table(
            analytics.get("critical") if analytics.get("critical") is not None else pd.DataFrame(),
            ["material_name", "remaining_qty", "avg_daily", "days_of_cover", "unit"],
            styles,
            header_bg="#c62828",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(
        Paragraph(
            rtl(f"۴) پیش‌بینی مواد مورد نیاز تاندیش‌ها{range_label}"),
            styles["heading"],
        )
    )
    story.append(
        _df_to_table(
            analytics.get("forecast") if analytics.get("forecast") is not None else pd.DataFrame(),
            ["material_name", "tundish_type", "tundish_id", "avg_daily", "days", "forecast_need", "unit"],
            styles,
            header_bg="#6a1b9a",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph(rtl("۵) پیشنهاد درخواست مواد"), styles["heading"]))
    story.append(
        _df_to_table(
            analytics.get("suggest") if analytics.get("suggest") is not None else pd.DataFrame(),
            [
                "material_name",
                "avg_daily",
                "days",
                "forecast_need",
                "remaining_qty",
                "suggest_qty",
                "unit",
            ],
            styles,
            header_bg="#ef6c00",
        )
    )
    story.append(Spacer(1, 0.4 * cm))


def generate_report(
    frames: dict[str, pd.DataFrame],
    metas: dict[str, dict],
    user: dict[str, Any],
    output_path: Path | str | None = None,
    analytics: dict[str, Any] | None = None,
) -> Path:
    """
    Build PDF with title «گزارش تاندیش / خلاصه داده‌های آپلود‌شده»,
    analytics sections (when provided), then one section per source file.
    """
    _register_fonts()
    ensure_dirs()
    styles = _styles()

    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = REPORT_DIR / f"report_{user.get('bale_user_id')}_{stamp}.pdf"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title="گزارش تاندیش",
    )

    story: list = []
    story.append(Paragraph(rtl("گزارش تاندیش / خلاصه داده‌های آپلود‌شده"), styles["title"]))
    role_fa = {
        "owner": "مالک",
        "manager": "مدیر",
        "responsible_officer": "کاردان مسئول",
        "technician": "تکنسین",
    }.get(user.get("role"), user.get("role"))
    info = (
        f"کاربر: {user.get('display_name') or user.get('bale_user_id')} | "
        f"نقش: {role_fa} | "
        f"حوزه: {user.get('scope') or '—'} | "
        f"تاریخ تولید: {format_datetime(tehran_now())}"
    )
    story.append(Paragraph(rtl(info), styles["body"]))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph(rtl("خلاصه فایل‌ها"), styles["heading"]))
    summary_rows = [
        [
            Paragraph(rtl("منبع"), styles["cell"]),
            Paragraph(rtl("کل ردیف‌ها"), styles["cell"]),
            Paragraph(rtl("پس از فیلتر نقش"), styles["cell"]),
        ]
    ]
    for key in SECTION_ORDER:
        meta = metas.get(key)
        if not meta:
            continue
        summary_rows.append(
            [
                Paragraph(rtl(meta.get("label", key)), styles["cell"]),
                Paragraph(rtl(str(meta.get("total_rows", 0))), styles["cell"]),
                Paragraph(rtl(str(meta.get("visible_rows", 0))), styles["cell"]),
            ]
        )
    summary_rows = [list(reversed(r)) for r in summary_rows]
    summary = Table(summary_rows, colWidths=[4 * cm, 4 * cm, 8 * cm])
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e7d32")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    story.append(summary)
    story.append(Spacer(1, 0.5 * cm))

    if analytics:
        _append_analytics(story, analytics, styles)

    for key in SECTION_ORDER:
        if key not in frames:
            continue
        label = FILE_TYPES[key]["label_fa"]
        story.append(Paragraph(rtl(f"بخش: {label}"), styles["heading"]))
        story.append(
            _df_to_table(
                frames[key],
                DISPLAY_COLUMNS.get(key),
                styles,
            )
        )
        story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    return output_path


# --- Monthly consumption summary PDF ---

SUMMARY_HEADER_FA = {
    "category_code": "کد دسته بندی",
    "id": "کد کالا",
    "quantity": "مقدار",
    "coefficient": "ضریب",
    "work_order": "سفارش کار",
    "date": "تاریخ",
    "month": "ماه",
    "unit": "واحد",
    "description": "شرح",
    "kg": "مصرف کیلوگرم (مقدار×ضریب)",
    "count": "تعداد قلم",
}


def generate_monthly_summary_pdf(
    sections: list[dict[str, Any]],
    *,
    grand_kg: float,
    output_path: Path | str | None = None,
    title: str = "خلاصه مصرفی ماهیانه",
) -> Path:
    """Landscape RTL PDF for the monthly consumption summary report."""
    _register_fonts()
    ensure_dirs()
    styles = _styles()

    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = REPORT_DIR / f"monthly_summary_{stamp}.pdf"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        rightMargin=1.0 * cm,
        leftMargin=1.0 * cm,
        topMargin=1.0 * cm,
        bottomMargin=1.0 * cm,
        title=title,
    )
    story: list = []
    story.append(Paragraph(rtl(title), styles["title"]))
    story.append(
        Paragraph(
            rtl(f"جمع کل مصرفی: {grand_kg:g} کیلوگرم"),
            styles["body"],
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    for section in sections:
        story.append(Paragraph(rtl(section.get("title") or title), styles["heading"]))
        cols = list(section.get("columns") or [])
        rows = section.get("rows") or []
        header = [
            Paragraph(rtl(SUMMARY_HEADER_FA.get(c, c)), styles["cell"]) for c in cols
        ]
        data = [list(reversed(header))]
        for row in rows:
            cells = []
            for c in cols:
                val = row.get(c, "")
                if val is None:
                    val = ""
                elif isinstance(val, float):
                    val = f"{val:g}"
                cells.append(Paragraph(rtl(val), styles["cell"]))
            data.append(list(reversed(cells)))
            if row.get("_subtotal"):
                # highlight last appended row
                pass
        if section.get("kind") == "main":
            # grand total row
            grand_cells = []
            for c in cols:
                if c == "quantity":
                    grand_cells.append(Paragraph(rtl(f"{grand_kg:g}"), styles["cell"]))
                elif c == "unit":
                    grand_cells.append(Paragraph(rtl("کیلوگرم"), styles["cell"]))
                elif c == "description":
                    grand_cells.append(Paragraph(rtl("جمع کل مصرفی"), styles["cell"]))
                else:
                    grand_cells.append(Paragraph(rtl(""), styles["cell"]))
            data.append(list(reversed(grand_cells)))
        elif section.get("kind") == "month":
            total_cells = []
            for c in cols:
                if c == "kg":
                    total_cells.append(
                        Paragraph(rtl(f"{float(section.get('total_kg') or 0):g}"), styles["cell"])
                    )
                elif c == "unit":
                    total_cells.append(Paragraph(rtl("کیلوگرم"), styles["cell"]))
                elif c == "description":
                    total_cells.append(
                        Paragraph(rtl(section.get("total_title") or ""), styles["cell"])
                    )
                else:
                    total_cells.append(Paragraph(rtl(""), styles["cell"]))
            data.append(list(reversed(total_cells)))
        elif section.get("kind") == "tundish_wo":
            check_cells = []
            for c in cols:
                if c == "kg":
                    check_cells.append(
                        Paragraph(rtl(f"{float(section.get('check_kg') or 0):g}"), styles["cell"])
                    )
                elif c == "unit":
                    check_cells.append(Paragraph(rtl("کیلوگرم"), styles["cell"]))
                elif c == "description":
                    check_cells.append(
                        Paragraph(rtl("جمع کنترل (اسلب+بلوم+بیلت+ناشناخته)"), styles["cell"])
                    )
                else:
                    check_cells.append(Paragraph(rtl(""), styles["cell"]))
            data.append(list(reversed(check_cells)))

        table = Table(data, repeatRows=1)
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.Color(0.93, 0.95, 1)]),
        ]
        # Green last row (totals)
        style_cmds.append(
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#c6efce"))
        )
        table.setStyle(TableStyle(style_cmds))
        story.append(table)
        story.append(Spacer(1, 0.45 * cm))

    doc.build(story)
    return output_path
