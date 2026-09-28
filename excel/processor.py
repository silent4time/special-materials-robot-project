"""Excel load, validate, row/column extract, and RBAC-aware filtering."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection, Iterable

import pandas as pd

from auth.rbac import filter_dataframe_for_user
from config import (
    FILE_TYPES,
    RBAC_SCOPED_FILE_TYPES,
    REQUIRED_COLUMNS,
    TUNDISH_TYPES,
    TUNDISH_TYPE_LABELS,
    clean_excel_sheet_name,
)
from excel.id_parse import extract_item_id, extract_product_name
from excel.work_order import tundish_type_label_for_work_order

# Critical for RBAC — required only on scoped file types (not warehouse inventory)
CRITICAL_COLUMNS = ["domain", "assignee_id", "assignee_name"]

COLUMN_ALIASES = {
    "domain": ["domain", "scope", "حوزه", "دامنه", "بخش"],
    "tundish_type": ["tundish_type", "tundishtype", "نوع تاندیش", "نوع_تاندیش", "تاندیش نوع"],
    "assignee_id": ["assignee_id", "user_id", "شناسه", "شناسه_کاربر", "کد_کاربر"],
    "assignee_name": ["assignee_name", "name", "نام", "نام_مسئول", "تکنسین"],
    "tundish_id": ["tundish_id", "tank", "تانک", "تاندیش", "شماره_تانک", "شماره_تاندیش"],
    "material_name": ["material_name", "material", "ماده", "نام_ماده", "مواد"],
    "product_name": ["product_name", "product", "محصول", "نام_محصول"],
    "quantity": [
        "quantity",
        "qty",
        "مقدار",
        "میزان",
        "تعداد",
        "موجودی",
        "موجودي",  # Arabic yeh variant (normalized too)
        "stock",
    ],
    "unit": ["unit", "واحد"],
    "date": ["date", "تاریخ", "تاريخ"],  # Arabic yeh variant (normalized too)
    "month": ["month", "ماه"],
    "location": ["location", "محل", "انبار", "مکان"],
    "status": ["status", "وضعیت"],
    "notes": ["notes", "note", "توضیحات", "یادداشت"],
    "category_code": [
        "category_code",
        "category",
        "کد دسته بندی",
        "کد دسته بندي",  # Arabic yeh variant
        "کد دسته‌بندی",
        "کد_دسته_بندی",
        "کد دسته",
        "کد ساختار",  # plant warehouse export (موجودی انبار)
        "کد_ساختار",
    ],
    "item_code_desc": [
        "item_code_desc",
        "کد و شرح کالا",
        "کد_و_شرح_کالا",
        "کد و شرح",
        "شرح کالا",
    ],
    "id": ["id", "کد کالا", "کد_کالا", "شناسه کالا"],
    "priority": ["priority", "اولویت", "اولويت"],
    "keyword": [
        "keyword",
        "کلید واژه",
        "کليد واژه",  # Arabic yeh variant
        "کلیدواژه",
        "کليدواژه",
        "کلید_واژه",
    ],
    "usage_location": [
        "usage_location",
        "usage location",
        "محل استفاده",
        "محل_استفاده",
        "محل مصرف",
        "محل_مصرف",
    ],
    "coefficient": [
        "coefficient",
        "coeff",
        "ضریب",
        "ضريب",
    ],
    "work_order": [
        "work_order",
        "workorder",
        "سفارش کار",
        "سفارش_کار",
    ],
    "request_return": [
        "request_return",
        "request/return",
        "درخواستی-برگشتی",
        "درخواستي-برگشتي",
        "درخواستی برگشتی",
    ],
    "description": [
        "description",
        "desc",
        "شرح",
    ],
}

RowFilterFn = Callable[[pd.DataFrame, str], pd.DataFrame]


class ExcelValidationError(ValueError):
    pass


@dataclass
class ExtractResult:
    """Outcome of extract_and_save_clean (row filter + column project)."""

    clean_path: Path
    raw_path: Path
    file_type: str
    raw_row_count: int
    kept_row_count: int
    dropped_row_count: int
    columns: list[str] = field(default_factory=list)
    extra_columns_dropped: list[str] = field(default_factory=list)
    filter_name: str = "standard_tundish"
    drop_reasons: dict[str, int] = field(default_factory=dict)


def _normalize_fa_header(value: object) -> str:
    """Normalize Excel header for alias match (Arabic/Persian yeh/kaf, spaces)."""
    text = "" if value is None else str(value)
    text = text.replace("ي", "ی")  # Arabic yeh ي → Persian ی
    text = text.replace("ى", "ی")  # Alef maksura ى → ی
    text = text.replace("ك", "ک")  # Arabic kaf ك → Persian ک
    text = " ".join(text.strip().split())
    return text.casefold()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename: dict[str, str] = {}
    # Map normalized header → original column label
    norm_map = {_normalize_fa_header(c): c for c in df.columns}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = _normalize_fa_header(alias)
            if key in norm_map:
                rename[norm_map[key]] = canonical
                break
    out = df.rename(columns=rename)
    out.columns = [str(c).strip() for c in out.columns]
    return out


def _tundish_accepted_map() -> dict[str, str]:
    key_to_label = {str(key).strip().casefold(): label for key, label in TUNDISH_TYPES.items()}
    label_to_label = {str(label).strip().casefold(): label for label in TUNDISH_TYPE_LABELS}
    return {**key_to_label, **label_to_label}


def _normalize_tundish_types(df: pd.DataFrame, *, strict: bool = True) -> pd.DataFrame:
    """Map tundish values to canonical Persian labels.

    strict=True (default): raise if any blank/unknown (used on already-cleaned files).
    strict=False: blank/unknown become None so row filters can drop them.
    """
    if "tundish_type" not in df.columns:
        return df

    accepted = _tundish_accepted_map()
    invalid: list[str] = []
    normalized: list[str | None] = []
    for value in df["tundish_type"]:
        if pd.isna(value) or not str(value).strip():
            invalid.append("خالی")
            normalized.append(None)
            continue
        text = str(value).strip()
        label = accepted.get(text.casefold())
        if label is None:
            invalid.append(text)
            normalized.append(None)
        else:
            normalized.append(label)
    if strict and invalid:
        shown = "، ".join(dict.fromkeys(invalid))
        allowed = "، ".join(TUNDISH_TYPE_LABELS)
        raise ExcelValidationError(
            f"نوع تاندیش نامعتبر است: {shown}. انواع مجاز: {allowed} "
            "(کلیدها: slab، bloom، billet)."
        )
    out = df.copy()
    out["tundish_type"] = normalized
    return out


def filter_standard_tundish_rows(df: pd.DataFrame, file_type: str = "") -> pd.DataFrame:
    """Default keep-rule for tundish-scoped files: standard type + non-empty quantity."""
    del file_type
    out = df
    if "tundish_type" in out.columns:
        out = out[out["tundish_type"].isin(TUNDISH_TYPE_LABELS)]
    if "quantity" in out.columns:
        qty = pd.to_numeric(out["quantity"], errors="coerce")
        out = out.loc[qty.notna()]
    return out.reset_index(drop=True)


def _normalize_category_code(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    # Excel may store 1201 as 1201.0
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) <= 4:
        text = text.zfill(4)
    if len(text) == 4 and text.isdigit():
        return text
    return None


def looks_like_product_inventory(df: pd.DataFrame) -> bool:
    """True when columns match منبع اصلی / warehouse inventory schema.

    Used so an upload on the monthly/consumables slot that is actually in the
    cleaned-inventory format (category_code + item id/desc + quantity) refreshes
    the canonical ``product_inventory`` extract instead of failing monthly parse.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return False
    work = _normalize_columns(df)
    cols = {str(c).strip() for c in work.columns}
    has_cat = "category_code" in cols
    has_desc = "item_code_desc" in cols or (
        "id" in cols and ("product_name" in cols or "material_name" in cols)
    )
    has_qty = "quantity" in cols
    # Monthly ledger markers — if present, treat as monthly not inventory
    monthly_markers = {"month", "work_order", "request_return", "assignee_id", "domain"}
    if cols & monthly_markers:
        return False
    return bool(has_cat and has_desc and has_qty)


def enrich_warehouse_inventory(df: pd.DataFrame) -> pd.DataFrame:
    """Parse id/product_name from raw «کد و شرح کالا»; add keyword / priority.

    ``item_code_desc`` is required on the *raw* upload for parsing, but is not
    written into the cleaned extract (see REQUIRED_COLUMNS).
    """
    out = df.copy()
    if "item_code_desc" not in out.columns and "product_name" in out.columns:
        out["item_code_desc"] = out["product_name"]
    if "item_code_desc" not in out.columns:
        raise ExcelValidationError(
            "ستون «کد و شرح کالا» در فایل منبع اصلی یافت نشد."
        )
    if "category_code" not in out.columns:
        raise ExcelValidationError(
            "ستون «کد دسته بندی» در فایل منبع اصلی یافت نشد."
        )
    if "quantity" not in out.columns:
        raise ExcelValidationError(
            "ستون «موجودی» در فایل منبع اصلی یافت نشد."
        )

    raw_desc = out["item_code_desc"]
    if "id" not in out.columns:
        out["id"] = raw_desc.map(extract_item_id)
    else:
        # Fill blanks from parser
        existing = out["id"]
        parsed = raw_desc.map(extract_item_id)
        out["id"] = [
            (str(e).strip() if e is not None and str(e).strip() and str(e).lower() != "nan" else p)
            for e, p in zip(existing, parsed)
        ]

    out["product_name"] = raw_desc.map(extract_product_name)
    out["category_code"] = out["category_code"].map(_normalize_category_code)

    def _keyword_cell(v: object) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        text = str(v).strip()
        if not text or text.lower() == "nan":
            return ""
        return text

    if "keyword" in out.columns:
        out["keyword"] = out["keyword"].map(_keyword_cell)
    else:
        out["keyword"] = ""

    if "priority" not in out.columns:
        out["priority"] = 1
    else:
        prio = pd.to_numeric(out["priority"], errors="coerce")
        out["priority"] = prio.fillna(1).astype(int)

    if "usage_location" in out.columns:
        out["usage_location"] = out["usage_location"].map(_keyword_cell)
    else:
        out["usage_location"] = ""

    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce")
    return out


def filter_warehouse_inventory_rows(
    df: pd.DataFrame,
    allowlist: Collection[str] | None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Keep rows with allowlisted category, valid id, priority != 0, numeric qty."""
    reasons = {
        "wrong_category": 0,
        "priority_0": 0,
        "bad_id": 0,
        "bad_category": 0,
        "bad_quantity": 0,
    }
    allowed = {str(c).zfill(4) if str(c).isdigit() else str(c) for c in (allowlist or [])}
    keep_mask: list[bool] = []
    for _, row in df.iterrows():
        cat = row.get("category_code")
        item_id = row.get("id")
        prio = int(row.get("priority") or 0)
        qty = row.get("quantity")
        ok = True
        if cat is None:
            reasons["bad_category"] += 1
            ok = False
        elif cat not in allowed:
            reasons["wrong_category"] += 1
            ok = False
        if item_id is None or (isinstance(item_id, float) and pd.isna(item_id)) or not str(item_id).strip():
            reasons["bad_id"] += 1
            ok = False
        if prio == 0:
            reasons["priority_0"] += 1
            ok = False
        if qty is None or (isinstance(qty, float) and pd.isna(qty)):
            reasons["bad_quantity"] += 1
            ok = False
        keep_mask.append(ok)
    kept = df.loc[keep_mask].reset_index(drop=True)
    return kept, reasons


# Registry of keep-rules — swap/extend after product owner confirms the final rule
ROW_FILTERS: dict[str, RowFilterFn] = {
    "standard_tundish": filter_standard_tundish_rows,
}
DEFAULT_ROW_FILTER = "standard_tundish"


def project_required_columns(df: pd.DataFrame, file_type: str) -> pd.DataFrame:
    """Keep only REQUIRED_COLUMNS for file_type (stable order); soft-missing → empty col."""
    if file_type not in REQUIRED_COLUMNS:
        raise ExcelValidationError(f"نوع فایل ناشناخته: {file_type}")
    required = REQUIRED_COLUMNS[file_type]
    if file_type in RBAC_SCOPED_FILE_TYPES:
        critical_missing = [c for c in CRITICAL_COLUMNS if c not in df.columns]
        if critical_missing:
            raise ExcelValidationError(
                "ستون‌های ضروری برای کنترل دسترسی موجود نیست: "
                + "، ".join(critical_missing)
                + "\nنام ستون‌ها باید شامل domain/حوزه و assignee_id یا assignee_name باشد."
            )
    data: dict[str, Any] = {}
    for col in required:
        if col in df.columns:
            data[col] = df[col].values
        else:
            data[col] = [None] * len(df)
    return pd.DataFrame(data)


# Preferred detail sheet name used by plant exports (warehouse + monthly).
_DETAIL_SHEET = "ریز اطلاعات"
# Warehouse inventory header fingerprints (after FA normalization).
_WAREHOUSE_HEADER_HINTS = (
    "کد دسته بندی",
    "کد و شرح کالا",
    "موجودی",
)


def _sheet_names(path: Path) -> list[str]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _headers_of_sheet(path: Path, sheet_name: str) -> list[str]:
    peek = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", nrows=0)
    return [str(c) for c in peek.columns]


def _sheet_has_warehouse_headers(headers: list[str]) -> bool:
    norms = {_normalize_fa_header(h) for h in headers}
    needed = {_normalize_fa_header(h) for h in _WAREHOUSE_HEADER_HINTS}
    return needed.issubset(norms)


def _pick_excel_sheet(path: Path) -> str | int:
    """Choose workbook sheet: «ریز اطلاعات» preferred, else warehouse 3-col, else first.

    Rules (in order):
    1. Sheet whose name is exactly «ریز اطلاعات»
    2. Sheet whose name contains «ریز اطلاعات»
    3. First sheet that looks like 3-column warehouse inventory
    4. First sheet (pandas default index 0)
    """
    try:
        names = _sheet_names(path)
    except Exception:  # noqa: BLE001
        return 0
    if not names:
        return 0
    if _DETAIL_SHEET in names:
        return _DETAIL_SHEET
    for name in names:
        if _DETAIL_SHEET in str(name):
            return name
    # Optional: detect warehouse headers across sheets
    for name in names:
        try:
            headers = _headers_of_sheet(path, name)
        except Exception:  # noqa: BLE001
            continue
        if _sheet_has_warehouse_headers(headers):
            return name
    return names[0]


def _read_raw_excel(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ExcelValidationError(f"فایل یافت نشد: {path}")
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ExcelValidationError("فقط فایل Excel با پسوند .xlsx پذیرفته می‌شود.")
    sheet = _pick_excel_sheet(path)
    try:
        df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001
        raise ExcelValidationError(f"خواندن Excel ناموفق بود: {exc}") from exc
    if df.empty:
        raise ExcelValidationError("فایل Excel خالی است.")
    return df


def load_excel(path: Path | str, *, strict_tundish: bool = True) -> pd.DataFrame:
    path = Path(path)
    df = _read_raw_excel(path)
    return _normalize_tundish_types(_normalize_columns(df), strict=strict_tundish)


def validate_required_columns(df: pd.DataFrame, file_type: str) -> list[str]:
    required = REQUIRED_COLUMNS.get(file_type, [])
    missing = [c for c in required if c not in df.columns]
    return missing



def _is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if bool(pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return (not text) or text.casefold() in {"nan", "none", "nat"}


def _normalize_key_part(value: object) -> str:
    """Stable string key part (Arabic/Persian yeh/kaf, trim, drop .0)."""
    if _is_blank(value):
        return ""
    text = str(value).strip()
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = " ".join(text.split())
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        text = text[:-2]
    return text.casefold()


def is_plant_monthly_frame(df: pd.DataFrame) -> bool:
    """Plant monthly detail: item/qty/month + coeff or request/return/desc, no domain."""
    if df is None or df.empty:
        return False
    cols = set(df.columns)
    has_core = "quantity" in cols and "month" in cols
    has_item = "id" in cols or "category_code" in cols
    has_plant = bool(cols & {"coefficient", "request_return", "description", "work_order"})
    missing_domain = "domain" not in cols
    return bool(has_core and has_item and has_plant and missing_domain)


def enrich_plant_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Map plant monthly columns into canonical analytics + summary retain fields."""
    out = df.copy()
    if "category_code" in out.columns:
        out["category_code"] = out["category_code"].map(_normalize_category_code)
    if "domain" not in out.columns or out["domain"].map(_is_blank).all():
        if "category_code" in out.columns:
            out["domain"] = out["category_code"].map(
                lambda v: str(v) if v is not None else "plant"
            )
        else:
            out["domain"] = "plant"
    # Derive tundish_type from سفارش کار when possible (slab/bloom/billet).
    if "work_order" in out.columns:
        mapped = out["work_order"].map(tundish_type_label_for_work_order)
        if "tundish_type" not in out.columns:
            out["tundish_type"] = mapped
        else:
            out.loc[mapped.notna(), "tundish_type"] = mapped[mapped.notna()]
    if "tundish_type" not in out.columns or out["tundish_type"].map(_is_blank).all():
        # Placeholder so standard tundish filter keeps plant rows for analytics
        out["tundish_type"] = TUNDISH_TYPE_LABELS[0]
    if "assignee_id" not in out.columns:
        out["assignee_id"] = None
    if "assignee_name" not in out.columns:
        out["assignee_name"] = None
    if "description" in out.columns:
        def _desc_display(v: object) -> str:
            if _is_blank(v):
                return ""
            text = str(v).strip()
            text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
            return " ".join(text.split())

        out["description"] = out["description"].map(_desc_display)
    if "material_name" not in out.columns or out["material_name"].map(_is_blank).all():
        ids = out["id"] if "id" in out.columns else pd.Series([None] * len(out))
        descs = out["description"] if "description" in out.columns else pd.Series([""] * len(out))
        out["material_name"] = [
            f"{str(i).strip()} - {d}".strip(" -") if not _is_blank(i) else d
            for i, d in zip(ids, descs)
        ]
    if "request_return" in out.columns:
        out["request_return"] = out["request_return"].map(
            lambda v: "" if _is_blank(v) else str(v).strip().replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
        )
    if "status" not in out.columns or out["status"].map(_is_blank).all():
        if "request_return" in out.columns:
            out["status"] = out["request_return"]
        else:
            out["status"] = None
    if "notes" not in out.columns or out["notes"].map(_is_blank).all():
        parts = []
        for _, row in out.iterrows():
            bits = []
            cat = row.get("category_code")
            wo = row.get("work_order")
            if not _is_blank(cat):
                bits.append(f"cat={cat}")
            if not _is_blank(wo):
                bits.append(f"order={wo}")
            parts.append("; ".join(bits) if bits else None)
        out["notes"] = parts
    if "quantity" in out.columns:
        out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce")
    if "coefficient" in out.columns:
        out["coefficient"] = pd.to_numeric(out["coefficient"], errors="coerce")
    return out


def merge_row_key(row: pd.Series, file_type: str) -> tuple[str, ...]:
    """Stable upsert key per file_type (see merge rules)."""
    if file_type == "product_inventory":
        item_id = row.get("id")
        if _is_blank(item_id):
            return (
                _normalize_key_part(row.get("category_code")),
                _normalize_key_part(row.get("product_name")),
            )
        return (_normalize_key_part(item_id),)
    if file_type == "monthly_consumption":
        item = row.get("id")
        if _is_blank(item):
            item = row.get("material_name")
        desc = row.get("description")
        if _is_blank(desc):
            desc = row.get("material_name") or row.get("notes")
        rr = row.get("request_return")
        if _is_blank(rr):
            rr = row.get("status")
        return (
            _normalize_key_part(item),
            _normalize_key_part(desc),
            _normalize_key_part(row.get("month")),
            _normalize_key_part(row.get("date")),
            _normalize_key_part(row.get("work_order")),
            _normalize_key_part(rr),
        )
    # tank_consumption and any other upload slot
    return (
        _normalize_key_part(row.get("tundish_id")),
        _normalize_key_part(row.get("material_name")),
        _normalize_key_part(row.get("date")),
        _normalize_key_part(row.get("tundish_type")),
        _normalize_key_part(row.get("assignee_id")),
        _normalize_key_part(row.get("domain")),
    )


def merge_clean_frames(
    old: pd.DataFrame | None,
    new: pd.DataFrame | None,
    file_type: str,
) -> pd.DataFrame:
    """Upsert-merge previous clean + new clean; newer wins on key collision.

    - product_inventory: by item id (keep old-only items)
    - monthly_consumption: by id/desc/month/date/work_order/request_return
    - tank_consumption: by tundish/material/date/…
    """
    if new is None or (isinstance(new, pd.DataFrame) and new.empty):
        if old is None:
            return pd.DataFrame()
        return old.copy()
    if old is None or (isinstance(old, pd.DataFrame) and old.empty):
        return new.copy()

    preferred_cols = list(REQUIRED_COLUMNS.get(file_type, []))
    all_cols = list(
        dict.fromkeys(
            [*preferred_cols, *[str(c) for c in old.columns], *[str(c) for c in new.columns]]
        )
    )
    old_a = old.copy()
    new_a = new.copy()
    for col in all_cols:
        if col not in old_a.columns:
            old_a[col] = None
        if col not in new_a.columns:
            new_a[col] = None
    old_a = old_a[all_cols]
    new_a = new_a[all_cols]

    merged_map: dict[tuple[str, ...], dict] = {}
    for _, row in old_a.iterrows():
        merged_map[merge_row_key(row, file_type)] = row.to_dict()
    for _, row in new_a.iterrows():
        key = merge_row_key(row, file_type)
        new_dict = row.to_dict()
        # Full inventory re-upload: keep previous usage_location when the new
        # file omits it (or leaves it blank). Quantity still comes from new.
        if file_type == "product_inventory" and key in merged_map:
            old_dict = merged_map[key]
            if _is_blank(new_dict.get("usage_location")) and not _is_blank(
                old_dict.get("usage_location")
            ):
                new_dict["usage_location"] = old_dict.get("usage_location")
        merged_map[key] = new_dict
    if not merged_map:
        return new_a.iloc[0:0].copy()
    out = pd.DataFrame(list(merged_map.values()))
    # Stable column order
    out = out[[c for c in all_cols if c in out.columns]]
    return out.reset_index(drop=True)



def write_clean_excel(df: pd.DataFrame, path: Path | str, file_type: str) -> None:
    """Persist a cleaned extract with the user-facing sheet title."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(
        path,
        index=False,
        engine="openpyxl",
        sheet_name=clean_excel_sheet_name(file_type),
    )


def extract_and_save_clean(
    raw_path: Path | str,
    file_type: str,
    *,
    row_filter: str | RowFilterFn | None = None,
    clean_dir: Path | str | None = None,
    category_allowlist: Collection[str] | None = None,
) -> ExtractResult:
    """Load raw workbook, filter records, project columns, write cleaned xlsx.

    For product_inventory (منبع اصلی): extract id, filter by category allowlist,
    drop priority==0, default priority=1.
    For other types: standard tundish keep-rule + REQUIRED_COLUMNS projection.
    """
    if file_type not in FILE_TYPES:
        raise ExcelValidationError(f"نوع فایل ناشناخته: {file_type}")

    raw_path = Path(raw_path)
    df = load_excel(raw_path, strict_tundish=False)
    raw_row_count = int(len(df))
    original_cols = [str(c) for c in df.columns]
    drop_reasons: dict[str, int] = {}

    if file_type == "product_inventory":
        filter_name = "warehouse_category_allowlist"
        allowlist = list(category_allowlist or [])
        if not allowlist:
            raise ExcelValidationError(
                "لیست کدهای دسته‌بندی خالی است. "
                "ابتدا از منوی «منبع اصلی» → «اضافه کردن کد دسته بندی» "
                "حداقل یک کد ۴ رقمی اضافه کنید."
            )
        enriched = enrich_warehouse_inventory(df)
        filtered, drop_reasons = filter_warehouse_inventory_rows(enriched, allowlist)
        kept_row_count = int(len(filtered))
        if kept_row_count == 0:
            raise ExcelValidationError(
                "پس از استخراج، هیچ ردیف معتبری باقی نماند.\n"
                f"حذف‌شده‌ها: دسته نامجاز={drop_reasons.get('wrong_category', 0)}، "
                f"اولویت ۰={drop_reasons.get('priority_0', 0)}، "
                f"شناسه نامعتبر={drop_reasons.get('bad_id', 0)}، "
                f"دسته خالی={drop_reasons.get('bad_category', 0)}، "
                f"موجودی نامعتبر={drop_reasons.get('bad_quantity', 0)}."
            )
        clean_df = project_required_columns(filtered, file_type)
    else:
        filter_name = DEFAULT_ROW_FILTER
        if row_filter is None:
            filter_fn = ROW_FILTERS[DEFAULT_ROW_FILTER]
        elif isinstance(row_filter, str):
            if row_filter not in ROW_FILTERS:
                raise ExcelValidationError(f"قواعد فیلتر ناشناخته: {row_filter}")
            filter_name = row_filter
            filter_fn = ROW_FILTERS[row_filter]
        else:
            filter_name = getattr(row_filter, "__name__", "custom")
            filter_fn = row_filter

        work = df
        if file_type == "monthly_consumption" and is_plant_monthly_frame(work):
            work = enrich_plant_monthly(work)
            filter_name = "plant_monthly_enrich"

        filtered = filter_fn(work, file_type)
        kept_row_count = int(len(filtered))
        if kept_row_count == 0:
            raise ExcelValidationError(
                "پس از استخراج، هیچ ردیف معتبری باقی نماند. "
                "ردیف‌ها باید نوع تاندیش استاندارد (اسلب/بلوم/بیلت) و مقدار غیرخالی داشته باشند."
            )
        clean_df = project_required_columns(filtered, file_type)

    required = list(REQUIRED_COLUMNS[file_type])
    extra_dropped = [c for c in original_cols if c not in required]

    if clean_dir is not None:
        out_dir = Path(clean_dir)
    else:
        out_dir = raw_path.parent / "cleaned"
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_path = out_dir / f"{file_type}.xlsx"
    write_clean_excel(clean_df, clean_path, file_type)

    return ExtractResult(
        clean_path=clean_path,
        raw_path=raw_path,
        file_type=file_type,
        raw_row_count=raw_row_count,
        kept_row_count=kept_row_count,
        dropped_row_count=raw_row_count - kept_row_count,
        columns=required,
        extra_columns_dropped=extra_dropped,
        filter_name=filter_name,
        drop_reasons=drop_reasons,
    )


# Keep table chunks below Bale's practical message limit; callers may prepend context.
INVENTORY_TABLE_CHUNK_CHARS = 3200


def _display_inventory_value(value: object, *, empty: str = "—") -> str:
    """Render a cell for the Persian inventory table without pandas artefacts."""
    if value is None:
        return empty
    try:
        if bool(pd.isna(value)):
            return empty
    except (TypeError, ValueError):
        pass
    text = " ".join(str(value).strip().split())
    if not text or text.casefold() in {"nan", "none", "nat"}:
        return empty
    return text


def _inventory_description(row: pd.Series) -> str:
    """Choose the most useful item description for an inventory row."""
    product_name = _display_inventory_value(row.get("product_name"), empty="")
    item_id = _display_inventory_value(row.get("id"), empty="")
    if item_id and product_name:
        return f"{item_id} - {product_name}"
    if product_name:
        return product_name
    if item_id:
        return item_id
    # Legacy fallback for older cleans / catalog rows that still carry the combined string
    item_code_desc = _display_inventory_value(row.get("item_code_desc"), empty="")
    return item_code_desc or "—"


def _format_inventory_quantity(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return f"{float(numeric):g}"
    return _display_inventory_value(value)


def inventory_table_rows(df: pd.DataFrame | None) -> list[dict[str, str]]:
    """Build PDF/HTML row dicts: کد دسته، شرح کالا، موجودی."""
    if df is None or df.empty:
        return []
    rows: list[dict[str, str]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "کد دسته": _display_inventory_value(row.get("category_code")),
                "شرح کالا": _inventory_description(row),
                "موجودی": _format_inventory_quantity(row.get("quantity")),
            }
        )
    return rows


def format_inventory_table_fa(
    df: pd.DataFrame | None,
    *,
    max_chars: int = INVENTORY_TABLE_CHUNK_CHARS,
) -> list[str]:
    """Format warehouse inventory as Persian table chunks.

    The stable three-column layout intentionally works for both cleaned Excel
    data and catalog fallbacks: category, item description, and quantity.
    Each returned string repeats the header so every Bale message is readable.
    """
    if df is None or df.empty:
        return []
    max_chars = max(500, int(max_chars))
    header = "کد دسته | شرح کالا | موجودی\n───────── | ───────────── | ───────"
    row_lines: list[str] = []
    for _, row in df.iterrows():
        category = _display_inventory_value(row.get("category_code"))
        description = _inventory_description(row)
        quantity = _format_inventory_quantity(row.get("quantity"))
        row_lines.append(f"{category} | {description} | {quantity}")

    chunks: list[str] = []
    current = header
    for row_line in row_lines:
        candidate = f"{current}\n{row_line}"
        if current != header and len(candidate) > max_chars:
            chunks.append(current)
            current = f"{header}\n{row_line}"
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def process_file(path: Path | str, file_type: str, user: dict[str, Any]) -> tuple[pd.DataFrame, dict]:
    if file_type not in FILE_TYPES:
        raise ExcelValidationError(f"نوع فایل ناشناخته: {file_type}")
    # Inventory clean files have no tundish_type — skip strict tundish check
    strict = file_type != "product_inventory"
    df = load_excel(path, strict_tundish=strict)
    missing = validate_required_columns(df, file_type)
    if file_type in RBAC_SCOPED_FILE_TYPES:
        critical_missing = [c for c in CRITICAL_COLUMNS if c not in df.columns]
        if critical_missing:
            raise ExcelValidationError(
                "ستون‌های ضروری برای کنترل دسترسی موجود نیست: "
                + "، ".join(critical_missing)
                + "\nنام ستون‌ها باید شامل domain/حوزه و assignee_id یا assignee_name باشد."
            )
    filtered = filter_dataframe_for_user(df, user)
    meta = {
        "file_type": file_type,
        "label": FILE_TYPES[file_type]["label_fa"],
        "total_rows": int(len(df)),
        "visible_rows": int(len(filtered)),
        "missing_optional": missing,
    }
    return filtered, meta


def process_session_files(
    paths: dict[str, str | None], user: dict[str, Any]
) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    """paths keys: tank_consumption, product_inventory, monthly_consumption"""
    frames: dict[str, pd.DataFrame] = {}
    metas: dict[str, dict] = {}
    for key, path in paths.items():
        if not path:
            continue
        frames[key], metas[key] = process_file(path, key, user)
    return frames, metas
