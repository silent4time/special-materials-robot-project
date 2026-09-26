"""Excel load, validate, and RBAC-aware filtering for the three file types."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from auth.rbac import filter_dataframe_for_user
from config import FILE_TYPES, REQUIRED_COLUMNS, TUNDISH_TYPES, TUNDISH_TYPE_LABELS


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


class ExcelValidationError(ValueError):
    pass


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
    # also strip leftover names
    out.columns = [str(c).strip() for c in out.columns]
    return out


def _normalize_tundish_types(df: pd.DataFrame) -> pd.DataFrame:
    """Store every accepted tundish key/label as its canonical Persian label."""
    if "tundish_type" not in df.columns:
        return df

    key_to_label = {str(key).strip().casefold(): label for key, label in TUNDISH_TYPES.items()}
    label_to_label = {str(label).strip().casefold(): label for label in TUNDISH_TYPE_LABELS}
    accepted = {**key_to_label, **label_to_label}
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
    if invalid:
        shown = "، ".join(dict.fromkeys(invalid))
        allowed = "، ".join(TUNDISH_TYPE_LABELS)
        raise ExcelValidationError(
            f"نوع تاندیش نامعتبر است: {shown}. انواع مجاز: {allowed} "
            "(کلیدها: slab، bloom، billet)."
        )
    out = df.copy()
    out["tundish_type"] = normalized
    return out


def load_excel(path: Path | str) -> pd.DataFrame:
    path = Path(path)
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
    return _normalize_tundish_types(_normalize_columns(df))


def validate_required_columns(df: pd.DataFrame, file_type: str) -> list[str]:
    required = REQUIRED_COLUMNS.get(file_type, [])
    missing = [c for c in required if c not in df.columns]
    return missing


def process_file(path: Path | str, file_type: str, user: dict[str, Any]) -> tuple[pd.DataFrame, dict]:
    if file_type not in FILE_TYPES:
        raise ExcelValidationError(f"نوع فایل ناشناخته: {file_type}")
    df = load_excel(path)
    missing = validate_required_columns(df, file_type)
    # soft-required: domain + assignee fields are critical for RBAC
    critical = ["domain", "assignee_id", "assignee_name"]
    critical_missing = [c for c in critical if c not in df.columns]
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
