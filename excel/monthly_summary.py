"""Build «خلاصه مصرفی ماهیانه» Excel (+ PDF inputs) from plant monthly data."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from config import REPORT_DIR, ensure_dirs
from excel.processor import (
    _normalize_category_code,
    _normalize_fa_header,
    _read_raw_excel,
    _normalize_columns,
)
from excel.work_order import (
    CONSUMPTION_LABELS_FA,
    UNKNOWN_GROUP,
    UNKNOWN_LABEL_FA,
    dominant_work_order,
    tundish_kg_totals_from_items,
)

SUMMARY_SHEET_NAME = "خلاصه مصرفی ماهیانه"
SUMMARY_FILE_NAME = "خلاصه مصرفی ماهیانه.xlsx"

PERSIAN_MONTHS = {
    1: "فروردین",
    2: "اردیبهشت",
    3: "خرداد",
    4: "تیر",
    5: "مرداد",
    6: "شهریور",
    7: "مهر",
    8: "آبان",
    9: "آذر",
    10: "دی",
    11: "بهمن",
    12: "اسفند",
}

LATIN_TO_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

DETAIL_COLUMNS_FA = [
    "کد دسته بندی",
    "کد کالا",
    "مقدار",
    "ضریب",
    "سفارش کار",
    "تاریخ",
    "ماه",
    "واحد",
    "شرح",
]

MONTH_SECTION_COLUMNS_FA = [
    "کد دسته بندی",
    "کد کالا",
    "مقدار",
    "ضریب",
    "مصرف کیلوگرم (مقدار×ضریب)",
    "تاریخ",
    "ماه",
    "واحد",
    "شرح",
]

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
YELLOW_FILL = PatternFill("solid", fgColor="FFF2CC")
GREEN_FILL = PatternFill("solid", fgColor="C6EFCE")
SECTION_FILL = PatternFill("solid", fgColor="7030A0")
SECTION_FONT = Font(bold=True, color="FFFFFF")
MONTH_TITLE_FILL = PatternFill("solid", fgColor="2E75B6")
MONTH_TITLE_FONT = Font(bold=True, color="FFFFFF")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True, readingOrder=2)
THIN = Border(
    left=Side(style="thin", color="B0B0B0"),
    right=Side(style="thin", color="B0B0B0"),
    top=Side(style="thin", color="B0B0B0"),
    bottom=Side(style="thin", color="B0B0B0"),
)

COL_WIDTHS = {
    "A": 16.0,
    "B": 15.0,
    "C": 12.0,
    "D": 8.0,
    "E": 14.5,
    "F": 8.0,
    "G": 6.0,
    "H": 10.0,
    "I": 42.0,
}


def to_persian_digits(value: object) -> str:
    return str(value).translate(LATIN_TO_PERSIAN_DIGITS)


def _blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if bool(pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return (not text) or text.casefold() in {"nan", "none", "nat"}


def normalize_fa_text(value: object) -> str:
    if _blank(value):
        return ""
    text = str(value).strip()
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    return " ".join(text.split())


def _year_only_latin(value: object) -> int | str | None:
    if _blank(value):
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return text


def _month_int(value: object) -> int | None:
    if _blank(value):
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    # map Persian month names
    for num, name in PERSIAN_MONTHS.items():
        if name in text:
            return num
    try:
        num = int(float(text))
    except (TypeError, ValueError):
        return None
    if 1 <= num <= 12:
        return num
    return None


def _signed_quantity(qty: float, request_return: object) -> float:
    label = normalize_fa_text(request_return)
    if "برگشت" in label:
        return -abs(float(qty))
    # درخواستی or blank → add
    return float(qty)


def _looks_like_plant_detail(columns: Iterable[str]) -> bool:
    norms = {_normalize_fa_header(c) for c in columns}
    needed = {
        _normalize_fa_header("کد کالا"),
        _normalize_fa_header("مقدار"),
        _normalize_fa_header("ماه"),
        _normalize_fa_header("شرح"),
    }
    return needed.issubset(norms)


@dataclass
class MonthlySummaryData:
    """Aggregated item rows + computed totals for Excel/PDF."""

    items: pd.DataFrame
    grand_kg: float
    group_order: list[str] = field(default_factory=list)
    month_sections: list[dict[str, Any]] = field(default_factory=list)
    # kg/count per slab|bloom|billet|unknown from aggregated item rows
    tundish_totals: dict[str, dict[str, float | int]] = field(default_factory=dict)


def _map_cleaned_to_plant_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Adapt cleaned monthly schema (English) toward plant detail columns."""
    out = pd.DataFrame()
    out["کد دسته بندی"] = df["category_code"] if "category_code" in df.columns else None
    if "id" in df.columns:
        out["کد کالا"] = df["id"]
    elif "material_name" in df.columns:
        out["کد کالا"] = df["material_name"].map(
            lambda v: str(v).split(" - ", 1)[0].strip() if not _blank(v) else None
        )
    else:
        out["کد کالا"] = None
    out["مقدار"] = df["quantity"] if "quantity" in df.columns else None
    out["ضریب"] = df["coefficient"] if "coefficient" in df.columns else 1
    out["سفارش کار"] = df["work_order"] if "work_order" in df.columns else None
    out["تاریخ"] = df["date"] if "date" in df.columns else None
    out["ماه"] = df["month"] if "month" in df.columns else None
    out["واحد"] = df["unit"] if "unit" in df.columns else None
    if "request_return" in df.columns:
        out["درخواستی-برگشتی"] = df["request_return"]
    elif "status" in df.columns:
        out["درخواستی-برگشتی"] = df["status"]
    else:
        out["درخواستی-برگشتی"] = "درخواستی"
    if "description" in df.columns:
        out["شرح"] = df["description"]
    elif "material_name" in df.columns:
        out["شرح"] = df["material_name"].map(
            lambda v: (
                str(v).split(" - ", 1)[1].strip()
                if (not _blank(v) and " - " in str(v))
                else ("" if _blank(v) else str(v).strip())
            )
        )
    else:
        out["شرح"] = None
    return out


def load_monthly_detail(path: Path | str) -> pd.DataFrame:
    """Load best-available monthly detail rows (prefer plant raw sheet)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"فایل مصرف ماهیانه یافت نشد: {path}")

    # Prefer explicit plant detail sheet when present
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            names = list(wb.sheetnames)
        finally:
            wb.close()
    except Exception:  # noqa: BLE001
        names = []

    sheet: str | int | None = None
    if "ریز اطلاعات" in names:
        sheet = "ریز اطلاعات"
    else:
        for name in names:
            if "ریز اطلاعات" in str(name):
                sheet = name
                break

    if sheet is not None:
        raw = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    else:
        raw = _read_raw_excel(path)

    if raw.empty:
        raise ValueError("فایل مصرف ماهیانه خالی است.")

    # Normalize headers to Persian plant names when plant-like
    if _looks_like_plant_detail(raw.columns):
        rename = {}
        target_map = {
            _normalize_fa_header("کد دسته بندی"): "کد دسته بندی",
            _normalize_fa_header("کد کالا"): "کد کالا",
            _normalize_fa_header("مقدار"): "مقدار",
            _normalize_fa_header("ضریب"): "ضریب",
            _normalize_fa_header("سفارش کار"): "سفارش کار",
            _normalize_fa_header("تاریخ"): "تاریخ",
            _normalize_fa_header("ماه"): "ماه",
            _normalize_fa_header("واحد"): "واحد",
            _normalize_fa_header("درخواستی-برگشتی"): "درخواستی-برگشتی",
            _normalize_fa_header("شرح"): "شرح",
        }
        for col in raw.columns:
            key = _normalize_fa_header(col)
            if key in target_map:
                rename[col] = target_map[key]
        detail = raw.rename(columns=rename)
    else:
        # Cleaned / canonical English schema
        normalized = _normalize_columns(raw)
        detail = _map_cleaned_to_plant_columns(normalized)

    required = {"کد کالا", "مقدار", "ماه", "شرح"}
    missing = sorted(required - set(detail.columns))
    if missing:
        raise ValueError(
            "ستون‌های لازم برای خلاصه مصرفی موجود نیست: " + "، ".join(missing)
        )
    if "ضریب" not in detail.columns:
        detail["ضریب"] = 1
    if "درخواستی-برگشتی" not in detail.columns:
        detail["درخواستی-برگشتی"] = "درخواستی"
    if "کد دسته بندی" not in detail.columns:
        detail["کد دسته بندی"] = None
    if "سفارش کار" not in detail.columns:
        detail["سفارش کار"] = None
    if "تاریخ" not in detail.columns:
        detail["تاریخ"] = None
    if "واحد" not in detail.columns:
        detail["واحد"] = None
    return detail


def aggregate_monthly_detail(detail: pd.DataFrame) -> MonthlySummaryData:
    """Aggregate by کد کالا + شرح + ماه; درخواستی add / برگشتی subtract."""
    work = detail.copy()
    work["شرح"] = work["شرح"].map(normalize_fa_text)
    def _item_code(v: object) -> str:
        if _blank(v):
            return ""
        text = str(v).strip()
        if text.endswith(".0") and text[:-2].replace("-", "").isalnum():
            text = text[:-2]
        return text

    work["کد کالا"] = work["کد کالا"].map(_item_code)
    work["مقدار"] = pd.to_numeric(work["مقدار"], errors="coerce")
    work = work.loc[work["مقدار"].notna()].copy()
    work["ضریب"] = pd.to_numeric(work["ضریب"], errors="coerce").fillna(1.0)
    work["ماه_num"] = work["ماه"].map(_month_int)
    work = work.loc[work["ماه_num"].notna()].copy()
    work["signed_qty"] = [
        _signed_quantity(q, rr)
        for q, rr in zip(work["مقدار"], work["درخواستی-برگشتی"])
    ]
    work["_order"] = range(len(work))

    # Preserve first-appearance order of شرح for main section
    group_order = list(dict.fromkeys(work["شرح"].tolist()))

    rows: list[dict[str, Any]] = []
    for (item_code, desc, month_num), grp in work.groupby(
        ["کد کالا", "شرح", "ماه_num"], sort=False
    ):
        grp_sorted = grp.sort_values("_order")
        first = grp_sorted.iloc[0]
        qty = float(grp_sorted["signed_qty"].sum())
        coeff = float(first["ضریب"]) if not _blank(first["ضریب"]) else 1.0
        cat = first.get("کد دسته بندی")
        cat_norm = _normalize_category_code(cat)
        # Dominant WO by |signed_qty * coeff|; first appearance as tie-break
        wo_weights = [
            abs(float(sq) * (float(c) if not _blank(c) else 1.0))
            for sq, c in zip(grp_sorted["signed_qty"], grp_sorted["ضریب"])
        ]
        wo_value = dominant_work_order(grp_sorted["سفارش کار"].tolist(), wo_weights)
        rows.append(
            {
                "کد دسته بندی": int(cat_norm) if cat_norm and cat_norm.isdigit() else cat_norm,
                "کد کالا": item_code,
                "مقدار": qty,
                "ضریب": coeff,
                "سفارش کار": wo_value,
                "تاریخ": _year_only_latin(first.get("تاریخ")),
                "ماه": int(month_num),
                "واحد": first.get("واحد"),
                "شرح": desc,
                "مصرف_کیلوگرم": qty * coeff,
                "_first_order": int(grp_sorted["_order"].min()),
            }
        )

    items = pd.DataFrame(rows)
    if items.empty:
        return MonthlySummaryData(
            items=items,
            grand_kg=0.0,
            group_order=[],
            month_sections=[],
            tundish_totals=tundish_kg_totals_from_items(items),
        )

    # Sort within each شرح by month; groups by first appearance
    items["_group_rank"] = items["شرح"].map(
        lambda d: group_order.index(d) if d in group_order else 10**9
    )
    items = items.sort_values(["_group_rank", "ماه", "_first_order"]).reset_index(drop=True)
    grand_kg = float(items["مصرف_کیلوگرم"].sum())

    # Month sections: for each year+month, items sorted by شرح
    month_sections: list[dict[str, Any]] = []
    items["_year"] = items["تاریخ"].map(
        lambda v: int(v) if isinstance(v, int) else (_year_only_latin(v) or 0)
    )
    for (year, month), sec in items.groupby(["_year", "ماه"], sort=True):
        sec_sorted = sec.sort_values(["شرح", "کد کالا"]).reset_index(drop=True)
        month_name = PERSIAN_MONTHS.get(int(month), str(month))
        title = f"مصرف {month_name} ماه {to_persian_digits(year)}"
        total_kg = float(sec_sorted["مصرف_کیلوگرم"].sum())
        month_sections.append(
            {
                "year": int(year) if year else None,
                "month": int(month),
                "month_name": month_name,
                "title": title,
                "total_title": f"جمع کل مصرف {month_name} ماه {to_persian_digits(year)}",
                "total_kg": total_kg,
                "rows": sec_sorted,
            }
        )

    tundish_totals = tundish_kg_totals_from_items(items)
    return MonthlySummaryData(
        items=items,
        grand_kg=grand_kg,
        group_order=group_order,
        month_sections=month_sections,
        tundish_totals=tundish_totals,
    )


def _style_row(ws, row_idx: int, fill: PatternFill | None = None, bold: bool = False) -> None:
    for col in range(1, 10):
        cell = ws.cell(row=row_idx, column=col)
        cell.alignment = CENTER
        cell.border = THIN
        if fill is not None:
            cell.fill = fill
        if bold:
            cell.font = Font(bold=True)


def build_monthly_summary_workbook(data: MonthlySummaryData) -> Workbook:
    """Create RTL formatted workbook matching the approved sample layout."""
    wb = Workbook()
    ws = wb.active
    ws.title = SUMMARY_SHEET_NAME
    ws.sheet_view.rightToLeft = True

    for col, width in COL_WIDTHS.items():
        ws.column_dimensions[col].width = width

    # --- Header ---
    for col_idx, title in enumerate(DETAIL_COLUMNS_FA, start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = THIN
    ws.row_dimensions[1].height = 32

    row_idx = 2
    items = data.items
    if not items.empty:
        for desc in data.group_order:
            group = items[items["شرح"] == desc]
            if group.empty:
                continue
            for _, item in group.iterrows():
                values = [
                    item.get("کد دسته بندی"),
                    item.get("کد کالا"),
                    item.get("مقدار"),
                    item.get("ضریب"),
                    item.get("سفارش کار"),
                    item.get("تاریخ"),
                    item.get("ماه"),
                    item.get("واحد"),
                    item.get("شرح"),
                ]
                for col_idx, val in enumerate(values, start=1):
                    ws.cell(row=row_idx, column=col_idx, value=val)
                _style_row(ws, row_idx)
                ws.row_dimensions[row_idx].height = 32
                row_idx += 1
            # yellow subtotal
            subtotal_qty = float(group["مقدار"].sum())
            ws.cell(row=row_idx, column=3, value=subtotal_qty)
            ws.cell(row=row_idx, column=9, value=f"جمع {desc}")
            _style_row(ws, row_idx, fill=YELLOW_FILL, bold=True)
            ws.row_dimensions[row_idx].height = 32
            row_idx += 1

    # Grand total (green)
    ws.cell(row=row_idx, column=3, value=data.grand_kg)
    ws.cell(row=row_idx, column=8, value="کیلوگرم")
    ws.cell(row=row_idx, column=9, value="جمع کل مصرفی")
    _style_row(ws, row_idx, fill=GREEN_FILL, bold=True)
    ws.row_dimensions[row_idx].height = 32
    row_idx += 2

    # Section title
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=9)
    cell = ws.cell(row=row_idx, column=1, value="مصرف به تفکیک ماه و سال")
    cell.fill = SECTION_FILL
    cell.font = SECTION_FONT
    cell.alignment = CENTER
    for col in range(1, 10):
        ws.cell(row=row_idx, column=col).border = THIN
        ws.cell(row=row_idx, column=col).fill = SECTION_FILL
    ws.row_dimensions[row_idx].height = 32
    row_idx += 1

    for section in data.month_sections:
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=9)
        title_cell = ws.cell(row=row_idx, column=1, value=section["title"])
        title_cell.fill = MONTH_TITLE_FILL
        title_cell.font = MONTH_TITLE_FONT
        title_cell.alignment = CENTER
        for col in range(1, 10):
            ws.cell(row=row_idx, column=col).fill = MONTH_TITLE_FILL
            ws.cell(row=row_idx, column=col).border = THIN
        ws.row_dimensions[row_idx].height = 32
        row_idx += 1

        for col_idx, title in enumerate(MONTH_SECTION_COLUMNS_FA, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=title)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = CENTER
            cell.border = THIN
        ws.row_dimensions[row_idx].height = 32
        row_idx += 1

        for _, item in section["rows"].iterrows():
            values = [
                item.get("کد دسته بندی"),
                item.get("کد کالا"),
                item.get("مقدار"),
                item.get("ضریب"),
                item.get("مصرف_کیلوگرم"),
                item.get("تاریخ"),
                item.get("ماه"),
                item.get("واحد"),
                item.get("شرح"),
            ]
            for col_idx, val in enumerate(values, start=1):
                ws.cell(row=row_idx, column=col_idx, value=val)
            _style_row(ws, row_idx)
            ws.row_dimensions[row_idx].height = 32
            row_idx += 1

        # month total
        ws.cell(row=row_idx, column=5, value=section["total_kg"])
        ws.cell(row=row_idx, column=8, value="کیلوگرم")
        ws.cell(row=row_idx, column=9, value=section["total_title"])
        _style_row(ws, row_idx, fill=GREEN_FILL, bold=True)
        ws.row_dimensions[row_idx].height = 32
        row_idx += 2

    # --- مصرف مواد بر حسب اسلب، بلوم و بیلت (after last month block) ---
    # month loop already left one blank via +=2; add one more ≈ two rows gap
    row_idx += 1
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=9)
    title_cell = ws.cell(
        row=row_idx,
        column=1,
        value="مصرف مواد بر حسب اسلب، بلوم و بیلت",
    )
    title_cell.fill = SECTION_FILL
    title_cell.font = SECTION_FONT
    title_cell.alignment = CENTER
    for col in range(1, 10):
        ws.cell(row=row_idx, column=col).border = THIN
        ws.cell(row=row_idx, column=col).fill = SECTION_FILL
    ws.row_dimensions[row_idx].height = 32
    row_idx += 1

    totals = data.tundish_totals or tundish_kg_totals_from_items(data.items)
    section_order = ["slab", "bloom", "billet"]
    for key in section_order:
        info = totals.get(key) or {"kg": 0.0, "count": 0}
        label = CONSUMPTION_LABELS_FA[key]
        count = int(info.get("count") or 0)
        kg = float(info.get("kg") or 0)
        ws.cell(row=row_idx, column=3, value=kg)
        ws.cell(row=row_idx, column=8, value="کیلوگرم")
        ws.cell(
            row=row_idx,
            column=9,
            value=f"{label} ({to_persian_digits(count)} قلم)",
        )
        _style_row(ws, row_idx, fill=YELLOW_FILL, bold=True)
        ws.row_dimensions[row_idx].height = 32
        row_idx += 1

    unk = totals.get(UNKNOWN_GROUP) or {"kg": 0.0, "count": 0}
    unk_kg = float(unk.get("kg") or 0)
    unk_count = int(unk.get("count") or 0)
    if unk_count or abs(unk_kg) > 1e-9:
        ws.cell(row=row_idx, column=3, value=unk_kg)
        ws.cell(row=row_idx, column=8, value="کیلوگرم")
        ws.cell(
            row=row_idx,
            column=9,
            value=f"{UNKNOWN_LABEL_FA} ({to_persian_digits(unk_count)} قلم)",
        )
        _style_row(ws, row_idx, fill=YELLOW_FILL, bold=True)
        ws.row_dimensions[row_idx].height = 32
        row_idx += 1

    check_kg = (
        float((totals.get("slab") or {}).get("kg") or 0)
        + float((totals.get("bloom") or {}).get("kg") or 0)
        + float((totals.get("billet") or {}).get("kg") or 0)
        + unk_kg
    )
    ws.cell(row=row_idx, column=3, value=check_kg)
    ws.cell(row=row_idx, column=8, value="کیلوگرم")
    ws.cell(
        row=row_idx,
        column=9,
        value="جمع کنترل (اسلب+بلوم+بیلت+سایر نواحی) — باید برابر جمع کل مصرفی باشد",
    )
    _style_row(ws, row_idx, fill=GREEN_FILL, bold=True)
    ws.row_dimensions[row_idx].height = 36
    row_idx += 1

    return wb


def build_monthly_summary(
    source_path: Path | str,
    *,
    excel_out: Path | str | None = None,
) -> tuple[MonthlySummaryData, Path]:
    """Load → aggregate → write Excel. Returns (data, excel_path)."""
    ensure_dirs()
    detail = load_monthly_detail(source_path)
    data = aggregate_monthly_detail(detail)
    if data.items.empty:
        raise ValueError("پس از تجمیع، ردیفی برای خلاصه مصرفی باقی نماند.")

    if excel_out is None:
        excel_out = REPORT_DIR / SUMMARY_FILE_NAME
    excel_out = Path(excel_out)
    excel_out.parent.mkdir(parents=True, exist_ok=True)
    wb = build_monthly_summary_workbook(data)
    wb.save(excel_out)
    return data, excel_out


def summary_sections_for_pdf(data: MonthlySummaryData) -> list[dict[str, Any]]:
    """Flatten summary into PDF-friendly section dicts."""
    sections: list[dict[str, Any]] = []
    # Main detail table (without yellow subtotals — PDF adds group breaks lightly)
    main_rows = []
    for desc in data.group_order:
        group = data.items[data.items["شرح"] == desc]
        for _, item in group.iterrows():
            main_rows.append(
                {
                    "category_code": item.get("کد دسته بندی"),
                    "id": item.get("کد کالا"),
                    "quantity": item.get("مقدار"),
                    "coefficient": item.get("ضریب"),
                    "work_order": item.get("سفارش کار"),
                    "date": item.get("تاریخ"),
                    "month": item.get("ماه"),
                    "unit": item.get("واحد"),
                    "description": item.get("شرح"),
                    "kg": item.get("مصرف_کیلوگرم"),
                }
            )
        main_rows.append(
            {
                "category_code": "",
                "id": "",
                "quantity": float(group["مقدار"].sum()) if not group.empty else 0,
                "coefficient": "",
                "work_order": "",
                "date": "",
                "month": "",
                "unit": "",
                "description": f"جمع {desc}",
                "kg": "",
                "_subtotal": True,
            }
        )
    sections.append(
        {
            "title": SUMMARY_SHEET_NAME,
            "kind": "main",
            "columns": [
                "category_code",
                "id",
                "quantity",
                "coefficient",
                "work_order",
                "date",
                "month",
                "unit",
                "description",
            ],
            "rows": main_rows,
            "grand_kg": data.grand_kg,
        }
    )
    for section in data.month_sections:
        rows = []
        for _, item in section["rows"].iterrows():
            rows.append(
                {
                    "category_code": item.get("کد دسته بندی"),
                    "id": item.get("کد کالا"),
                    "quantity": item.get("مقدار"),
                    "coefficient": item.get("ضریب"),
                    "kg": item.get("مصرف_کیلوگرم"),
                    "date": item.get("تاریخ"),
                    "month": item.get("ماه"),
                    "unit": item.get("واحد"),
                    "description": item.get("شرح"),
                }
            )
        sections.append(
            {
                "title": section["title"],
                "kind": "month",
                "columns": [
                    "category_code",
                    "id",
                    "quantity",
                    "coefficient",
                    "kg",
                    "date",
                    "month",
                    "unit",
                    "description",
                ],
                "rows": rows,
                "total_kg": section["total_kg"],
                "total_title": section["total_title"],
            }
        )
    totals = data.tundish_totals or tundish_kg_totals_from_items(data.items)
    tundish_rows = []
    for key in ("slab", "bloom", "billet"):
        info = totals.get(key) or {"kg": 0.0, "count": 0}
        tundish_rows.append(
            {
                "description": CONSUMPTION_LABELS_FA[key],
                "count": int(info.get("count") or 0),
                "kg": float(info.get("kg") or 0),
                "unit": "کیلوگرم",
            }
        )
    unk = totals.get(UNKNOWN_GROUP) or {"kg": 0.0, "count": 0}
    if int(unk.get("count") or 0) or abs(float(unk.get("kg") or 0)) > 1e-9:
        tundish_rows.append(
            {
                "description": UNKNOWN_LABEL_FA,
                "count": int(unk.get("count") or 0),
                "kg": float(unk.get("kg") or 0),
                "unit": "کیلوگرم",
            }
        )
    sections.append(
        {
            "title": "مصرف مواد بر حسب اسلب، بلوم و بیلت",
            "kind": "tundish_wo",
            "columns": ["description", "count", "kg", "unit"],
            "rows": tundish_rows,
            "grand_kg": data.grand_kg,
            "check_kg": sum(float(r["kg"]) for r in tundish_rows),
        }
    )
    return sections
