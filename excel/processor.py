"""Excel load, validate, row/column extract, and RBAC-aware filtering."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from auth.rbac import filter_dataframe_for_user
from config import FILE_TYPES, REQUIRED_COLUMNS, TUNDISH_TYPES, TUNDISH_TYPE_LABELS

# Critical for RBAC — must be present in source before extract/process
CRITICAL_COLUMNS = ["domain", "assignee_id", "assignee_name"]

COLUMN_ALIASES = {
    "domain": ["domain", "scope", "حوزه", "دامنه", "بخش"],
    "tundish_type": ["tundish_type", "tundishtype", "نوع تاندیش", "نوع_تاندیش", "تاندیش نوع"],
    "assignee_id": ["assignee_id", "user_id", "شناسه", "شناسه_کاربر", "کد_کاربر"],
    "assignee_name": ["assignee_name", "name", "نام", "نام_مسئول", "تکنسین"],
    "tundish_id": ["tundish_id", "tank", "تانک", "تاندیش", "شماره_تانک", "شماره_تاندیش"],
    "material_name": ["material_name", "material", "ماده", "نام_ماده", "مواد"],
    "product_name": ["product_name", "product", "محصول", "نام_محصول"],
    "quantity": ["quantity", "qty", "مقدار", "میزان", "تعداد"],
    "unit": ["unit", "واحد"],
    "date": ["date", "تاریخ"],
    "month": ["month", "ماه"],
    "location": ["location", "محل", "انبار", "مکان"],
    "status": ["status", "وضعیت"],
    "notes": ["notes", "note", "توضیحات", "یادداشت"],
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


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename: dict[str, str] = {}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            if key in lower_map:
                rename[lower_map[key]] = canonical
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
    """Default keep-rule (pluggable): standard tundish_type + non-empty quantity.

    - Keep rows whose tundish_type is one of the three plant standards
      (تاندیش اسلب / بلوم / بیلت). Drop blank or unknown types.
    - If tundish_type column is absent, do not drop on that criterion.
    - Drop rows where quantity is blank/NaN (zeros are kept).
    - file_type reserved for future per-type rules (e.g. inventory ∩ tank materials).
    """
    del file_type  # reserved for tighter policies later
    out = df
    if "tundish_type" in out.columns:
        out = out[out["tundish_type"].isin(TUNDISH_TYPE_LABELS)]
    if "quantity" in out.columns:
        qty = pd.to_numeric(out["quantity"], errors="coerce")
        out = out.loc[qty.notna()]
    return out.reset_index(drop=True)


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


def _read_raw_excel(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ExcelValidationError(f"فایل یافت نشد: {path}")
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ExcelValidationError("فقط فایل Excel با پسوند .xlsx پذیرفته می‌شود.")
    try:
        df = pd.read_excel(path, engine="openpyxl")
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


def extract_and_save_clean(
    raw_path: Path | str,
    file_type: str,
    *,
    row_filter: str | RowFilterFn | None = None,
    clean_dir: Path | str | None = None,
) -> ExtractResult:
    """Load raw workbook, filter records, project columns, write cleaned xlsx.

    Primary: drop extra/invalid *rows* via pluggable keep-rule (default standard tundish).
    Secondary: project to REQUIRED_COLUMNS only (extra columns dropped).

    Clean file path pattern:
      <raw_parent>/cleaned/<file_type>.xlsx
    """
    if file_type not in FILE_TYPES:
        raise ExcelValidationError(f"نوع فایل ناشناخته: {file_type}")

    raw_path = Path(raw_path)
    df = load_excel(raw_path, strict_tundish=False)
    raw_row_count = int(len(df))
    original_cols = [str(c) for c in df.columns]

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

    filtered = filter_fn(df, file_type)
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
    clean_df.to_excel(clean_path, index=False, engine="openpyxl")

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
    )


def process_file(path: Path | str, file_type: str, user: dict[str, Any]) -> tuple[pd.DataFrame, dict]:
    if file_type not in FILE_TYPES:
        raise ExcelValidationError(f"نوع فایل ناشناخته: {file_type}")
    df = load_excel(path)
    missing = validate_required_columns(df, file_type)
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
