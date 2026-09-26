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
from excel.work_order import (
    CONSUMPTION_LABELS_FA,
    UNKNOWN_GROUP,
    UNKNOWN_LABEL_FA,
)

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



def apply_letterhead(
    content_pdf: Path | str,
    letterhead_pdf: Path | str | None,
    *,
    output_path: Path | str | None = None,
) -> Path:
    """Stamp letterhead as background on every content page. No-op if missing."""
    content_path = Path(content_pdf)
    if not letterhead_pdf:
        return content_path
    lh_path = Path(letterhead_pdf)
    if not lh_path.is_file():
        return content_path
    try:
        from pypdf import PdfReader, PdfWriter, Transformation, PageObject
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf required for letterhead merge") from exc

    content = PdfReader(str(content_path))
    letter = PdfReader(str(lh_path))
    if not content.pages or not letter.pages:
        return content_path

    out = Path(output_path) if output_path else content_path
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp then replace if overwriting same path
    tmp = out.with_suffix(out.suffix + ".lh.tmp")
    writer = PdfWriter()
    lh0 = letter.pages[0]
    lw = float(lh0.mediabox.width) or 1.0
    lh_h = float(lh0.mediabox.height) or 1.0
    for page in content.pages:
        cw = float(page.mediabox.width) or lw
        ch = float(page.mediabox.height) or lh_h
        blank = PageObject.create_blank_page(width=cw, height=ch)
        scale = Transformation().scale(cw / lw, ch / lh_h)
        try:
            blank.merge_transformed_page(lh0, scale)
        except Exception:  # noqa: BLE001
            # Older fallback: merge without scale if sizes already match
            blank.merge_page(lh0)
        blank.merge_page(page)
        writer.add_page(blank)
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(out)
    return out



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
    letterhead_path: Path | str | None = None,
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
    return apply_letterhead(output_path, letterhead_path)


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
    letterhead_path: Path | str | None = None,
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
            # Per-month اسلب/بلوم/بیلت under that month
            mtot = section.get("tundish_totals") or {}
            for _key in ("slab", "bloom", "billet"):
                _info = mtot.get(_key) or {"kg": 0.0, "count": 0}
                _count = int(_info.get("count") or 0)
                _kg = float(_info.get("kg") or 0)
                if _count == 0 and abs(_kg) < 1e-9:
                    continue
                _cells = []
                for c in cols:
                    if c == "kg":
                        _cells.append(Paragraph(rtl(f"{_kg:g}"), styles["cell"]))
                    elif c == "unit":
                        _cells.append(Paragraph(rtl("کیلوگرم"), styles["cell"]))
                    elif c == "description":
                        _cells.append(
                            Paragraph(
                                rtl(f"{CONSUMPTION_LABELS_FA[_key]} ({_count} قلم)"),
                                styles["cell"],
                            )
                        )
                    else:
                        _cells.append(Paragraph(rtl(""), styles["cell"]))
                data.append(list(reversed(_cells)))
            _unk = mtot.get(UNKNOWN_GROUP) or {"kg": 0.0, "count": 0}
            _unk_kg = float(_unk.get("kg") or 0)
            _unk_count = int(_unk.get("count") or 0)
            if _unk_count or abs(_unk_kg) > 1e-9:
                _cells = []
                for c in cols:
                    if c == "kg":
                        _cells.append(Paragraph(rtl(f"{_unk_kg:g}"), styles["cell"]))
                    elif c == "unit":
                        _cells.append(Paragraph(rtl("کیلوگرم"), styles["cell"]))
                    elif c == "description":
                        _cells.append(
                            Paragraph(
                                rtl(f"{UNKNOWN_LABEL_FA} ({_unk_count} قلم)"),
                                styles["cell"],
                            )
                        )
                    else:
                        _cells.append(Paragraph(rtl(""), styles["cell"]))
                data.append(list(reversed(_cells)))
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
                        Paragraph(rtl("جمع کنترل (اسلب+بلوم+بیلت+سایر نواحی)"), styles["cell"])
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
    return apply_letterhead(output_path, letterhead_path)


# --- Simple reusable RTL report PDF (analytics menu) ---

SIMPLE_HEADER_FA = {
    **HEADER_FA,
    "زمان": "زمان",
    "فعالیت": "فعالیت",
    "surplus_qty": "مقدار مازاد",
    "surplus_reason": "دلیل مازاد",
    "کد کالا": "کد کالا",
    "شرح": "شرح",
    "کد دسته": "کد دسته",
    "مقدار قبلی": "قبلی",
    "مقدار جدید": "جدید",
    "مقدار ورودی": "ورودی",
    "وضعیت": "وضعیت",
}


def _format_simple_cell(val: Any, col: str | None = None) -> str:
    if val is None:
        return ""
    if isinstance(val, float):
        if val != val:  # NaN
            return ""
        if val == float("inf"):
            return "∞"
        return f"{val:g}" if abs(val) >= 0.01 or val == 0 else f"{val:.4f}"
    if col in {"date", "start", "end", "entry_date"} and val not in ("", None):
        try:
            return format_date(val)
        except Exception:  # noqa: BLE001
            return str(val)
    if col == "created_at" and val not in ("", None):
        try:
            return format_datetime(val)
        except Exception:  # noqa: BLE001
            return str(val)
    return str(val)


def _rows_table(
    columns: list[str],
    rows: list[dict[str, Any]],
    styles: dict,
    *,
    header_map: dict[str, str] | None = None,
    header_bg: str = "#1f4e79",
    max_rows: int = 500,
) -> Table | Paragraph:
    labels = header_map or SIMPLE_HEADER_FA
    if not columns:
        return Paragraph(rtl("هیچ ستونی تعریف نشده."), styles["body"])
    header = [Paragraph(rtl(labels.get(c, c)), styles["cell"]) for c in columns]
    data = [list(reversed(header))]
    for row in rows[:max_rows]:
        cells = [
            Paragraph(rtl(_format_simple_cell(row.get(c, ""), c)), styles["cell"])
            for c in columns
        ]
        data.append(list(reversed(cells)))
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
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
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.whitesmoke, colors.Color(0.93, 0.95, 1)],
                ),
            ]
        )
    )
    return table


def generate_simple_report_pdf(
    title: str,
    *,
    subtitle: str | None = None,
    sections: list[dict[str, Any]] | None = None,
    columns: list[str] | None = None,
    rows: list[dict[str, Any]] | None = None,
    empty_message: str = "داده‌ای یافت نشد.",
    output_path: Path | str | None = None,
    header_map: dict[str, str] | None = None,
    filename_stem: str = "simple_report",
    letterhead_path: Path | str | None = None,
) -> Path:
    """Reusable landscape RTL PDF for analytics menu reports.

    Pass either ``sections`` (list of dicts with title/columns/rows) or a single
    ``columns`` + ``rows`` table. Empty data still produces a one-page PDF with
    ``empty_message``.
    """
    _register_fonts()
    ensure_dirs()
    styles = _styles()

    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = REPORT_DIR / f"{filename_stem}_{stamp}.pdf"
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
    if subtitle:
        story.append(Paragraph(rtl(subtitle), styles["body"]))
        story.append(Spacer(1, 0.25 * cm))

    built_sections: list[dict[str, Any]] = []
    if sections:
        built_sections = list(sections)
    elif columns is not None:
        built_sections = [
            {
                "title": None,
                "columns": list(columns),
                "rows": list(rows or []),
                "empty_message": empty_message,
            }
        ]
    else:
        built_sections = [
            {
                "title": None,
                "columns": [],
                "rows": [],
                "empty_message": empty_message,
            }
        ]

    any_table = False
    for section in built_sections:
        sec_title = section.get("title")
        if sec_title:
            story.append(Paragraph(rtl(str(sec_title)), styles["heading"]))
        cols = list(section.get("columns") or [])
        sec_rows = list(section.get("rows") or [])
        sec_empty = section.get("empty_message") or empty_message
        header_bg = section.get("header_bg") or "#1f4e79"
        if not cols or not sec_rows:
            story.append(Paragraph(rtl(str(sec_empty)), styles["body"]))
            story.append(Spacer(1, 0.3 * cm))
            continue
        any_table = True
        story.append(
            _rows_table(
                cols,
                sec_rows,
                styles,
                header_map=header_map or SIMPLE_HEADER_FA,
                header_bg=str(header_bg),
            )
        )
        story.append(Spacer(1, 0.4 * cm))

    if not any_table and not any(s.get("title") for s in built_sections):
        # Ensure at least empty_message is visible when no sections rendered body
        if not built_sections or (
            not built_sections[0].get("columns") and not built_sections[0].get("rows")
        ):
            # already appended empty for the lone section; if somehow skipped:
            pass

    story.append(
        Paragraph(
            rtl(f"تاریخ تولید: {format_datetime(tehran_now())}"),
            styles["body"],
        )
    )
    doc.build(story)
    return apply_letterhead(output_path, letterhead_path)
