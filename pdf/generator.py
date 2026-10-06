"""Generate a combined Persian/RTL PDF report from Excel datasets + tundish analytics."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from xml.sax.saxutils import escape as _xml_escape

from bot.jalali import format_date, format_datetime, tehran_now
from pathlib import Path
from typing import Any

import arabic_reshaper
import pandas as pd
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
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

# PDF body fonts (Persian/RTL). Registration order:
#   1) Vazirmatn (bundled under fonts/) — preferred
#   2) Tahoma (system) — fallback if Vazirmatn missing/unusable
#   3) DejaVuSans (bundled) — last resort
_FONT_REGISTERED = False
FONT_NAME = "Vazirmatn"
FONT_BOLD = "Vazirmatn-Bold"

_TAHOMA_REGULAR_CANDIDATES = (
    Path("/usr/share/fonts/truetype/msttcorefonts/tahoma.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Tahoma.ttf"),
    Path("/usr/share/fonts/truetype/tahoma/tahoma.ttf"),
    Path("/Windows/Fonts/tahoma.ttf"),
    Path("/mnt/c/Windows/Fonts/tahoma.ttf"),
)
_TAHOMA_BOLD_CANDIDATES = (
    Path("/usr/share/fonts/truetype/msttcorefonts/tahomabd.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/TahomaBd.ttf"),
    Path("/usr/share/fonts/truetype/tahoma/tahomabd.ttf"),
    Path("/Windows/Fonts/tahomabd.ttf"),
    Path("/mnt/c/Windows/Fonts/tahomabd.ttf"),
)


def _try_register_font(name: str, path: Path) -> bool:
    """Register a TTF/OTF if the file exists; return False on missing/failure."""
    try:
        if not path.is_file():
            return False
        pdfmetrics.registerFont(TTFont(name, str(path)))
        return True
    except Exception:  # noqa: BLE001
        return False


def _register_pair(family: str, bold_name: str, regular: Path, bold: Path | None) -> bool:
    """Register regular (+ bold or reuse regular). Sets FONT_NAME / FONT_BOLD on success."""
    global FONT_NAME, FONT_BOLD
    if not _try_register_font(family, regular):
        return False
    if bold is None or not _try_register_font(bold_name, bold):
        # Bold file missing or failed — reuse regular under bold name
        if not _try_register_font(bold_name, regular):
            return False
    FONT_NAME = family
    FONT_BOLD = bold_name
    return True


def _register_fonts() -> None:
    """Register PDF fonts once. Prefer Vazirmatn, then Tahoma, then DejaVuSans."""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return

    # 1) Bundled Vazirmatn (OFL) — preferred for all PDF reports
    if _register_pair(
        "Vazirmatn",
        "Vazirmatn-Bold",
        FONTS_DIR / "Vazirmatn-Regular.ttf",
        FONTS_DIR / "Vazirmatn-Bold.ttf",
    ):
        _FONT_REGISTERED = True
        return

    # 2) System Tahoma fallback
    tahoma_reg = next((p for p in _TAHOMA_REGULAR_CANDIDATES if p.is_file()), None)
    if tahoma_reg is not None:
        tahoma_bold = next((p for p in _TAHOMA_BOLD_CANDIDATES if p.is_file()), None)
        if _register_pair("Tahoma", "Tahoma-Bold", tahoma_reg, tahoma_bold):
            _FONT_REGISTERED = True
            return

    # 3) Bundled DejaVuSans — last resort (already shipped historically)
    if _register_pair(
        "DejaVuSans",
        "DejaVuSans-Bold",
        FONTS_DIR / "DejaVuSans.ttf",
        FONTS_DIR / "DejaVuSans-Bold.ttf",
    ):
        _FONT_REGISTERED = True
        return

    raise FileNotFoundError(
        "هیچ فونت فارسی قابل ثبت نبود. "
        f"حداقل یکی از این‌ها لازم است: {FONTS_DIR / 'Vazirmatn-Regular.ttf'} "
        "(ترجیحی)، Tahoma سیستم، یا DejaVuSans در fonts/."
    )


def rtl(text: Any) -> str:
    s = "" if text is None else str(text)
    if not s.strip():
        return ""
    try:
        reshaped = arabic_reshaper.reshape(s)
        return get_display(reshaped)
    except Exception:  # noqa: BLE001
        return s


# ---------------------------------------------------------------------------
# Page geometry — ALL report PDFs (bot + web) are A4 PORTRAIT (عمودی).
# ---------------------------------------------------------------------------
PAGE_SIZE = A4  # (595.27, 841.89) pt — portrait
PAGE_MARGIN = 1.0 * cm


def content_width(margin: float = PAGE_MARGIN) -> float:
    """Usable frame width on the portrait A4 page."""
    return float(PAGE_SIZE[0]) - 2.0 * float(margin)


@lru_cache(maxsize=65536)
def _shaped_width(text: str, font: str, size: float) -> float:
    """Rendered width of a logical (unshaped) Persian/Latin string."""
    if not text:
        return 0.0
    try:
        shaped = arabic_reshaper.reshape(text)
    except Exception:  # noqa: BLE001
        shaped = text
    try:
        return float(pdfmetrics.stringWidth(shaped, font, size))
    except Exception:  # noqa: BLE001
        return float(len(shaped)) * float(size) * 0.55


def _break_long_word(word: str, width: float, font: str, size: float) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for ch in word:
        cand = cur + ch
        if cur and _shaped_width(cand, font, size) > width:
            chunks.append(cur)
            cur = ch
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    return chunks or [word]


def rtl_wrap_lines(text: Any, width: float, font: str, size: float) -> list[str]:
    """Greedy word-wrap in LOGICAL order (before bidi) so multi-line RTL cells
    read top→bottom correctly. Each returned line is still logical text."""
    s = "" if text is None else str(text)
    width = max(4.0, float(width))
    out: list[str] = []
    for para in s.splitlines() or [""]:
        words = para.split()
        if not words:
            continue
        line = ""
        for w in words:
            if _shaped_width(w, font, size) > width:
                if line:
                    out.append(line)
                chunks = _break_long_word(w, width, font, size)
                out.extend(chunks[:-1])
                line = chunks[-1]
                continue
            cand = f"{line} {w}" if line else w
            if line and _shaped_width(cand, font, size) > width:
                out.append(line)
                line = w
            else:
                line = cand
        if line:
            out.append(line)
    return out


def rtl_markup(
    text: Any,
    width: float | None = None,
    font: str | None = None,
    size: float | None = None,
) -> str:
    """Paragraph-safe RTL markup; pre-wrapped per line when width is given."""
    if width is None or font is None or size is None:
        return _xml_escape(rtl(text))
    lines = rtl_wrap_lines(text, width, font, size)
    return "<br/>".join(_xml_escape(rtl(line)) for line in lines)


def _p(text: Any, style: ParagraphStyle, width: float | None = None) -> Paragraph:
    """Paragraph with RTL line-wrapping at ``width`` (frame width by default)."""
    w = content_width() if width is None else float(width)
    return Paragraph(
        rtl_markup(text, w - 2.0, style.fontName, float(style.fontSize)),
        style,
    )


def table_font_size(n_cols: int) -> float:
    """Shrink cell font as column count grows so portrait tables stay readable."""
    if n_cols <= 5:
        return 8.5
    if n_cols <= 8:
        return 7.5
    if n_cols <= 11:
        return 6.6
    if n_cols <= 15:
        return 5.8
    return 5.0


_CELL_PAD = 2.0
_WRAP_SLACK = 0.5  # pt kept free inside a cell so pre-wrapped lines never re-wrap


def auto_col_widths(
    header_texts: list[str],
    rows_texts: list[list[str]],
    avail: float,
    font: str,
    size: float,
    *,
    pad: float = _CELL_PAD,
) -> list[float]:
    """Content-weighted column widths that always sum to ``avail``.

    Priority when space is short (wide tables on portrait A4): never break a
    body word (codes, numbers) → then keep header words whole → then spread the
    rest toward each column's single-line width.
    """
    n = len(header_texts)
    if n == 0:
        return []
    sample = rows_texts[:300]
    extra = 2.0 * pad + _WRAP_SLACK + 0.5
    cap = avail * 0.22
    desired: list[float] = []
    min_body: list[float] = []
    min_head: list[float] = []
    for i in range(n):
        head = str(header_texts[i] or "")
        body = [str(r[i] if i < len(r) else "") for r in sample]
        full = max((_shaped_width(t, font, size) for t in [head] + body), default=0.0)
        lb = max((_shaped_width(w, font, size) for t in body for w in t.split()), default=0.0)
        hfont = FONT_BOLD if font == FONT_NAME else font  # header row is bold
        lh = max((_shaped_width(w, hfont, size) for w in head.split()), default=0.0)
        mb = min(lb, cap) + extra
        mh = max(mb, min(lh, cap) + extra)
        min_body.append(mb)
        min_head.append(mh)
        desired.append(max(mh, min(full, avail * 0.45) + extra))
    total_des = sum(desired)
    if total_des <= avail:
        return [d * avail / total_des for d in desired]
    total_head = sum(min_head)
    if total_head <= avail:
        spare = avail - total_head
        flex = [max(0.0, d - m) for d, m in zip(desired, min_head)]
        fs = sum(flex) or 1.0
        return [m + spare * f / fs for m, f in zip(min_head, flex)]
    total_body = sum(min_body)
    if total_body <= avail:
        ratio = (avail - total_body) / max(1e-6, total_head - total_body)
        return [b + (h - b) * ratio for b, h in zip(min_body, min_head)]
    return [m * avail / total_body for m in min_body]


def _cell_style(size: float, *, bold: bool = False) -> ParagraphStyle:
    return ParagraphStyle(
        f"CellFA_{size}_{int(bold)}",
        fontName=FONT_BOLD if bold else FONT_NAME,
        fontSize=size,
        leading=round(size * 1.35, 2),
        alignment=TA_CENTER,
    )


def build_rtl_table(
    header_texts: list[str] | None,
    rows_texts: list[list[str]],
    *,
    avail: float | None = None,
    header_bg: str = "#1f4e79",
    font_size: float | None = None,
    zebra: bool = True,
    col_widths: list[float] | None = None,
    bold_rows: set[int] | None = None,
    shade_rows: set[int] | None = None,
    shade_color: str = "#ffe0b2",
) -> Table:
    """Portrait-friendly RTL table: auto widths, logical wrapping, repeat header.

    ``bold_rows`` / ``shade_rows``: 0-based body row indices to emphasise.

    ``header_texts`` None → no header row. Column 0 is rendered rightmost.
    """
    _register_fonts()
    avail = content_width() if avail is None else float(avail)
    n = len(header_texts) if header_texts else max((len(r) for r in rows_texts), default=0)
    size = float(font_size or table_font_size(n))
    head = [str(h or "") for h in (header_texts or [""] * n)]
    pad = _CELL_PAD if n <= 15 else 1.5
    body = [[str(c if c is not None else "") for c in (list(r) + [""] * n)[:n]] for r in rows_texts]
    widths = list(col_widths) if col_widths else auto_col_widths(
        head if header_texts else [""] * n, body, avail, FONT_NAME, size, pad=pad
    )
    reg = _cell_style(size)
    bold = _cell_style(size, bold=True)

    def _row(texts: list[str], style: ParagraphStyle) -> list[Paragraph]:
        cells = [
            Paragraph(
                rtl_markup(t, widths[i] - 2 * pad - _WRAP_SLACK, style.fontName, size),
                style,
            )
            for i, t in enumerate(texts)
        ]
        return list(reversed(cells))

    data: list[list[Any]] = []
    if header_texts:
        data.append(_row(head, bold))
    bold_set = set(bold_rows or ())
    for bi, r in enumerate(body):
        data.append(_row(r, bold if bi in bold_set else reg))
    table = Table(
        data,
        colWidths=list(reversed(widths)),
        repeatRows=1 if header_texts else 0,
    )
    cmds: list = [
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    if header_texts:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ]
    if zebra and len(data) > (1 if header_texts else 0):
        cmds.append(
            (
                "ROWBACKGROUNDS",
                (0, 1 if header_texts else 0),
                (-1, -1),
                [colors.whitesmoke, colors.Color(0.93, 0.95, 1)],
            )
        )
    off = 1 if header_texts else 0
    for bi in sorted(set(shade_rows or ())):
        if 0 <= bi < len(body):
            cmds.append(
                ("BACKGROUND", (0, bi + off), (-1, bi + off), colors.HexColor(shade_color))
            )
    table.setStyle(TableStyle(cmds))
    return table


def _new_doc(
    output_path: Path,
    *,
    title: str,
    letterhead_path: Path | str | None,
    margin: float = PAGE_MARGIN,
) -> SimpleDocTemplate:
    """A4 portrait doc template with letterhead-aware top/bottom margins."""
    top, bottom = _letterhead_margins(letterhead_path, PAGE_SIZE, margin, margin)
    return SimpleDocTemplate(
        str(output_path),
        pagesize=PAGE_SIZE,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=top,
        bottomMargin=bottom,
        title=title,
    )



# Letterhead header/footer bands (fraction of page height). The company
# letterhead's logo + name sit in the top ~14 % and the address line in the
# bottom ~7 %, so report content starts/ends outside those bands.
LETTERHEAD_TOP_FRACTION = 0.155
LETTERHEAD_BOTTOM_FRACTION = 0.085


def _letterhead_margins(
    letterhead_pdf: Path | str | None,
    pagesize: tuple[float, float],
    top: float,
    bottom: float,
) -> tuple[float, float]:
    """(topMargin, bottomMargin) — enlarged when a letterhead PDF is in use."""
    if not letterhead_pdf or not Path(letterhead_pdf).is_file():
        return top, bottom
    height = float(pagesize[1])
    return (
        max(top, height * LETTERHEAD_TOP_FRACTION),
        max(bottom, height * LETTERHEAD_BOTTOM_FRACTION),
    )


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
        sx, sy = cw / lw, ch / lh_h
        if abs(sx - sy) / max(sx, sy) > 0.03:
            # Orientation/aspect mismatch: keep letterhead proportions, centered.
            k = min(sx, sy)
            scale = Transformation().scale(k, k).translate(
                (cw - lw * k) / 2.0, (ch - lh_h * k) / 2.0
            )
        else:
            scale = Transformation().scale(sx, sy)
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
        "work_order",
        "usage_location",
        "keyword",
        "quantity",
        "priority",
        "contractor_or_company",
        "origin",
        "shared",
        "critical_point",
        "unit",
        "casting_floor",
        "billet_renovation",
        "billet_patching",
        "bloom_renovation",
        "bloom_patching",
        "slab_renovation",
        "slab_patching",
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
    "product_name": "شرح کالا",
    "keyword": "کلید واژه",
    "id": "شناسه مواد",
    "category_code": "کد دسته بندی",
    "work_order": "شماره دستور کار",
    "usage_location": "محل استفاده",
    "priority": "اولویت",
    "quantity": "موجودی",
    "contractor_or_company": "تأمین‌کننده",
    "origin": "سازنده",
    "shared": "اشتراکی",
    "critical_point": "نقطه بحرانی",
    "casting_floor": "سطح ریخته گری",
    "billet_renovation": "نوسازی تاندیش بیلت",
    "billet_patching": "پچینگ تاندیش بیلت",
    "bloom_renovation": "نوسازی تاندیش بلوم",
    "bloom_patching": "پچینگ تاندیش بلوم",
    "slab_renovation": "نوسازی تاندیش اسلب",
    "slab_patching": "پچینگ تاندیش اسلب",
    "rate_group": "گروه نرخ مشترک (ادغام)",
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


def _df_cell_text(val: Any, col: str) -> str:
    if col in {"date", "start", "end", "entry_date", "created_at"} and val not in ("", None):
        try:
            from datetime import date as _date, datetime as _datetime
            if isinstance(val, _datetime):
                val = format_datetime(val)
            elif isinstance(val, _date) or (isinstance(val, str) and len(str(val)) >= 8):
                # date-only columns → Jalali date; created_at → datetime
                val = format_datetime(val) if c_is_dt(col) else format_date(val)
        except Exception:  # noqa: BLE001
            val = str(val)
    if isinstance(val, float):
        if val != val:  # NaN
            return ""
        if val == float("inf"):
            return "∞"
        return f"{val:.2f}"
    if val is None:
        return ""
    return str(val)


def c_is_dt(col: str) -> bool:
    return col in {"created_at"}


def _df_to_table(
    df: pd.DataFrame,
    cols: list[str] | None,
    styles: dict,
    *,
    max_rows: int = 200,
    header_bg: str = "#1f4e79",
    header_map: dict[str, str] | None = None,
    avail: float | None = None,
) -> Table | Paragraph:
    if df is None or df.empty:
        return _p("هیچ ردیفی یافت نشد.", styles["body"], avail)
    use_cols = [c for c in (cols or list(df.columns)) if c in df.columns]
    if not use_cols:
        use_cols = list(df.columns)[:8]
    if not use_cols:
        return _p("هیچ ردیفی یافت نشد.", styles["body"], avail)

    labels = header_map or HEADER_FA
    header = [str(labels.get(c, HEADER_FA.get(c, c))) for c in use_cols]
    body: list[list[str]] = []
    for _, row in df.head(max_rows).iterrows():
        body.append([_df_cell_text(row.get(c, ""), c) for c in use_cols])
    return build_rtl_table(header, body, avail=avail, header_bg=header_bg)


def _append_analytics(
    story: list, analytics: dict[str, Any], styles: dict, avail: float | None = None
) -> None:
    if not analytics:
        return
    start = analytics.get("start")
    end = analytics.get("end")
    days = analytics.get("days")
    crit_days = analytics.get("critical_days", CRITICAL_DAYS)
    range_label = ""
    if start and end:
        range_label = f" (بازه {format_date(start)} تا {format_date(end)} — {days} روز)"

    story.append(_p(("تحلیل تاندیش"), styles["heading"], avail))
    story.append(
        _p(
            (
                f"آستانه مواد بحرانی: پوشش موجودی کمتر از {crit_days} روز "
                f"(CRITICAL_DAYS). پیشنهاد درخواست = max(0, نیاز پیش‌بینی − موجودی)."
            ),
            styles["body"],
            avail,
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(_p(("۱) خلاصه مصرف روزانه مواد"), styles["heading"], avail))
    story.append(
        _df_to_table(
            analytics.get("daily_rates") if analytics.get("daily_rates") is not None else pd.DataFrame(),
            ["material_name", "tundish_type", "tundish_id", "avg_daily", "total_qty", "days_span", "unit", "source"],
            styles,
            avail=avail,
            header_bg="#0d47a1",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(_p((f"۲) مصرف در بازه درخواستی{range_label}"), styles["heading"], avail))
    story.append(
        _df_to_table(
            analytics.get("period_consumption") if analytics.get("period_consumption") is not None else pd.DataFrame(),
            ["material_name", "tundish_type", "tundish_id", "quantity", "unit", "start", "end"],
            styles,
            avail=avail,
            header_bg="#1565c0",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(_p(("۳) موجودی باقیمانده و مواد بحرانی"), styles["heading"], avail))
    story.append(
        _df_to_table(
            analytics.get("remaining") if analytics.get("remaining") is not None else pd.DataFrame(),
            ["material_name", "remaining_qty", "unit", "location"],
            styles,
            avail=avail,
            header_bg="#2e7d32",
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    story.append(
        _p(
            (f"مواد در حال اتمام (بحرانی — پوشش < {crit_days} روز):"),
            styles["body"],
            avail,
        )
    )
    story.append(
        _df_to_table(
            analytics.get("critical") if analytics.get("critical") is not None else pd.DataFrame(),
            ["material_name", "remaining_qty", "avg_daily", "days_of_cover", "unit"],
            styles,
            avail=avail,
            header_bg="#c62828",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(
        _p(
            (f"۴) پیش‌بینی مواد مورد نیاز تاندیش‌ها{range_label}"),
            styles["heading"],
            avail,
        )
    )
    story.append(
        _df_to_table(
            analytics.get("forecast") if analytics.get("forecast") is not None else pd.DataFrame(),
            ["material_name", "tundish_type", "tundish_id", "avg_daily", "days", "forecast_need", "unit"],
            styles,
            avail=avail,
            header_bg="#6a1b9a",
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    story.append(_p(("۵) پیشنهاد درخواست مواد"), styles["heading"], avail))
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
            avail=avail,
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

    doc = _new_doc(output_path, title="گزارش تاندیش", letterhead_path=letterhead_path)
    avail = float(doc.width)

    story: list = []
    story.append(_p("گزارش تاندیش / خلاصه داده‌های آپلود‌شده", styles["title"], avail))
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
    story.append(_p(info, styles["body"], avail))
    story.append(Spacer(1, 0.4 * cm))

    story.append(_p("خلاصه فایل‌ها", styles["heading"], avail))
    summary_body: list[list[str]] = []
    for key in SECTION_ORDER:
        meta = metas.get(key)
        if not meta:
            continue
        summary_body.append(
            [
                str(meta.get("label", key)),
                str(meta.get("total_rows", 0)),
                str(meta.get("visible_rows", 0)),
            ]
        )
    summary = build_rtl_table(
        ["منبع", "کل ردیف‌ها", "پس از فیلتر نقش"],
        summary_body,
        avail=min(avail, 12 * cm),
        header_bg="#2e7d32",
        font_size=8.5,
        col_widths=[6 * cm, 3 * cm, 3 * cm][: 3] if avail >= 12 * cm else None,
    )
    story.append(summary)
    story.append(Spacer(1, 0.5 * cm))

    if analytics:
        _append_analytics(story, analytics, styles, avail)

    for key in SECTION_ORDER:
        if key not in frames:
            continue
        label = FILE_TYPES[key]["label_fa"]
        story.append(_p(f"بخش: {label}", styles["heading"], avail))
        story.append(
            _df_to_table(
                frames[key],
                DISPLAY_COLUMNS.get(key),
                styles,
                header_map=HEADER_FA,
                avail=avail,
            )
        )
        story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    return apply_letterhead(output_path, letterhead_path)


# --- Monthly consumption summary PDF ---

_BOLD_SUMMARY_KINDS = {"subtotal", "grand_total", "month_total", "group_total", "check"}

# Approximate Excel fills (content-aligned; PDF cannot match pattern fills pixel-perfect)
_PDF_FILL = {
    "header": colors.HexColor("#1f4e79"),
    "yellow": colors.HexColor("#fff2cc"),
    "green": colors.HexColor("#c6efce"),
    "section": colors.HexColor("#7030a0"),
    "month_title": colors.HexColor("#2e75b6"),
    "slab": colors.HexColor("#ededed"),
    "bloom": colors.HexColor("#d9d9d9"),
    "billet": colors.HexColor("#9a9a9a"),
    "unknown": colors.HexColor("#b0b0b0"),
    "wo_slab": colors.HexColor("#ededed"),
    "wo_bloom": colors.HexColor("#d9d9d9"),
    "wo_billet": colors.HexColor("#9a9a9a"),
    "wo_unknown": colors.HexColor("#c0c0c0"),
}


def _pdf_fill_for_row(row: dict[str, Any]) -> colors.Color | None:
    key = row.get("_fill_key")
    if key == "wo":
        from excel.work_order import group_for_work_order, UNKNOWN_GROUP

        group = group_for_work_order(row.get("_work_order")) or UNKNOWN_GROUP
        return _PDF_FILL.get(f"wo_{group}", colors.whitesmoke)
    if key in _PDF_FILL:
        return _PDF_FILL[key]
    return None


def _format_summary_cell(val: Any) -> str:
    if val is None:
        return ""
    try:
        if bool(pd.isna(val)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(val, float):
        if val != val:
            return ""
        # Enough precision to keep Excel-like decimals (avoid default :g rounding)
        text = f"{val:.10g}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    if isinstance(val, int):
        return str(val)
    return str(val)


def generate_monthly_summary_pdf(
    sections: list[dict[str, Any]],
    *,
    grand_kg: float,
    output_path: Path | str | None = None,
    title: str = "خلاصه مصرفی ماهیانه",
    letterhead_path: Path | str | None = None,
) -> Path:
    """A4 portrait RTL PDF mirroring the monthly summary Excel sheet content."""
    _register_fonts()
    ensure_dirs()
    styles = _styles()

    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = REPORT_DIR / f"monthly_summary_{stamp}.pdf"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = _new_doc(output_path, title=title, letterhead_path=letterhead_path)
    avail = float(doc.width)
    story: list = []
    story.append(_p(title, styles["title"], avail))
    story.append(
        _p(
            f"جمع کل مصرفی: {_format_summary_cell(float(grand_kg))} کیلوگرم",
            styles["body"],
            avail,
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    for section in sections:
        kind = section.get("kind")
        sec_title = section.get("title")

        if kind == "banner":
            if sec_title:
                story.append(_p(str(sec_title), styles["heading"], avail))
                story.append(Spacer(1, 0.15 * cm))
            continue

        if sec_title:
            story.append(_p(str(sec_title), styles["heading"], avail))

        cols = list(section.get("columns") or [])
        rows = list(section.get("rows") or [])
        if not cols or not rows:
            continue

        show_header = section.get("show_header", True)
        row_fills: list[colors.Color | None] = []
        data_row_meta: list[dict] = []
        body_texts: list[list[str]] = []
        bold_body: set[int] = set()

        if show_header:
            row_fills.append(None)  # styled as header below
            data_row_meta.append({"_kind": "header"})

        for row in rows:
            values = row.get("_values")
            if values is None:
                values = [row.get(c, "") for c in cols]
            values = list(values) + [None] * max(0, len(cols) - len(values))
            values = values[: len(cols)]
            if row.get("_kind") in _BOLD_SUMMARY_KINDS:
                bold_body.add(len(body_texts))
            body_texts.append([_format_summary_cell(v) for v in values])
            row_fills.append(_pdf_fill_for_row(row))
            data_row_meta.append(row)

        table = build_rtl_table(
            [str(c) for c in cols] if show_header else None,
            body_texts,
            avail=avail,
            zebra=False,
            bold_rows=bold_body,
        )
        style_cmds: list = []
        for i, fill in enumerate(row_fills):
            meta = data_row_meta[i]
            if meta.get("_kind") == "header":
                continue
            if fill is not None:
                style_cmds.append(("BACKGROUND", (0, i), (-1, i), fill))
            if meta.get("_kind") in {
                "subtotal",
                "grand_total",
                "month_total",
                "group_total",
                "check",
            }:
                style_cmds.append(("FONTNAME", (0, i), (-1, i), FONT_BOLD))
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
    "شرح کالا": "شرح کالا",
    "کد دسته": "کد دسته",
    "موجودی": "موجودی",
    "شناسه": "شناسه",
    "گروه": "گروه",
    "ردیف": "ردیف",
    "مصرف روز": "مصرف روز",
    "پیشنهاد": "پیشنهاد",
    "موجودی سایت": "موجودی سایت",
    "مازاد": "مازاد",
    "برگشت": "برگشت",
    "دلیل": "دلیل",
    "واحد": "واحد",
    "مقدار": "مقدار",
    "مقدار قبلی": "قبلی",
    "مقدار جدید": "جدید",
    "مقدار ورودی": "ورودی",
    "وضعیت": "وضعیت",
    "کد چهاررقمی": "کد چهاررقمی",
    "کد و شرح کالا": "کد و شرح کالا",
    "نیاز": "نیاز",
    "حد تحمل(روز)": "حد تحمل(روز)",
    "توضیح": "توضیح",
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
    avail: float | None = None,
    bold_rows: set[int] | None = None,
) -> Table | Paragraph:
    labels = header_map or SIMPLE_HEADER_FA
    if not columns:
        return _p("هیچ ستونی تعریف نشده.", styles["body"], avail)
    header = [str(labels.get(c, c)) for c in columns]
    body = [
        [_format_simple_cell(row.get(c, ""), c) for c in columns]
        for row in rows[:max_rows]
    ]
    return build_rtl_table(
        header,
        body,
        avail=avail,
        header_bg=header_bg,
        bold_rows=bold_rows,
        shade_rows=bold_rows,
    )


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
    """Reusable A4 portrait RTL PDF for analytics menu reports.

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

    doc = _new_doc(output_path, title=title, letterhead_path=letterhead_path)
    avail = float(doc.width)
    story: list = []
    story.append(_p(title, styles["title"], avail))
    if subtitle:
        story.append(_p(subtitle, styles["body"], avail))
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
            story.append(_p(str(sec_title), styles["heading"], avail))
        cols = list(section.get("columns") or [])
        sec_rows = list(section.get("rows") or [])
        sec_empty = section.get("empty_message") or empty_message
        header_bg = section.get("header_bg") or "#1f4e79"
        if not cols or not sec_rows:
            story.append(_p(str(sec_empty), styles["body"], avail))
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
                avail=avail,
                bold_rows=set(section.get("bold_rows") or ()),
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
        _p(f"تاریخ تولید: {format_datetime(tehran_now())}", styles["body"], avail)
    )
    doc.build(story)
    return apply_letterhead(output_path, letterhead_path)
