"""Generate a combined Persian/RTL PDF report from three Excel datasets."""
from __future__ import annotations

from datetime import datetime
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

from config import FILE_TYPES, FONTS_DIR, REPORT_DIR, ensure_dirs

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
        "tundish_id",
        "material_name",
        "quantity",
        "unit",
        "assignee_name",
        "date",
        "notes",
    ],
    "product_inventory": [
        "domain",
        "product_name",
        "quantity",
        "unit",
        "location",
        "assignee_name",
        "date",
        "notes",
    ],
    "monthly_consumption": [
        "domain",
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
    "assignee_id": "شناسه",
    "assignee_name": "مسئول",
    "tundish_id": "تانک",
    "material_name": "ماده",
    "product_name": "محصول",
    "quantity": "مقدار",
    "unit": "واحد",
    "date": "تاریخ",
    "month": "ماه",
    "location": "محل",
    "status": "وضعیت",
    "notes": "توضیحات",
}


def _df_to_table(df: pd.DataFrame, file_type: str, styles: dict) -> Table | Paragraph:
    cols = [c for c in DISPLAY_COLUMNS.get(file_type, list(df.columns)) if c in df.columns]
    if not cols:
        cols = list(df.columns)[:8]
    if df.empty or not cols:
        return Paragraph(rtl("هیچ ردیفی مطابق نقش شما یافت نشد."), styles["body"])

    header = [Paragraph(rtl(HEADER_FA.get(c, c)), styles["cell"]) for c in cols]
    # RTL table: reverse column order so rightmost is first domain
    header = list(reversed(header))
    data = [header]
    for _, row in df.head(200).iterrows():
        cells = [
            Paragraph(rtl(row.get(c, "")), styles["cell"]) for c in cols
        ]
        data.append(list(reversed(cells)))

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
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


def generate_report(
    frames: dict[str, pd.DataFrame],
    metas: dict[str, dict],
    user: dict[str, Any],
    output_path: Path | str | None = None,
) -> Path:
    """
    Build PDF with title «گزارش تاندیش / خلاصه داده‌های آپلود‌شده»,
    one section per source file, plus a summary.
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
    role_fa = {"owner": "مالک", "manager": "مدیر", "responsible_officer": "کاردان مسئول", "technician": "تکنسین"}.get(
        user.get("role"), user.get("role")
    )
    info = (
        f"کاربر: {user.get('display_name') or user.get('bale_user_id')} | "
        f"نقش: {role_fa} | "
        f"حوزه: {user.get('scope') or '—'} | "
        f"تاریخ تولید: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    story.append(Paragraph(rtl(info), styles["body"]))
    story.append(Spacer(1, 0.4 * cm))

    # Summary section
    story.append(Paragraph(rtl("خلاصه"), styles["heading"]))
    summary_rows = [[Paragraph(rtl("منبع"), styles["cell"]), Paragraph(rtl("کل ردیف‌ها"), styles["cell"]), Paragraph(rtl("پس از فیلتر نقش"), styles["cell"])]]
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
    # reverse cells for RTL feel
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

    for key in SECTION_ORDER:
        if key not in frames:
            continue
        label = FILE_TYPES[key]["label_fa"]
        story.append(Paragraph(rtl(f"بخش: {label}"), styles["heading"]))
        story.append(_df_to_table(frames[key], key, styles))
        story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    return output_path
