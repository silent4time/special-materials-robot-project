#!/usr/bin/env python3
"""Load the production-style samples in ``samples/real`` into the bot DB.

The inventory workbook is passed through the normal warehouse extractor.  The
monthly workbook is a plant export whose ``ریز اطلاعات`` sheet is converted to
the bot's canonical monthly-consumption schema before it is extracted.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

# ``python scripts/seed_real_samples.py`` puts ``scripts/`` (not the project
# root) on sys.path.  Add the root before importing the application modules.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from config import ADMIN_BALE_USER_ID, TUNDISH_TYPE_LABELS, UPLOAD_DIR, ensure_dirs
from db.models import Database
from excel.processor import extract_and_save_clean
from excel.work_order import tundish_type_label_for_work_order

INVENTORY_SAMPLE = PROJECT_ROOT / "samples" / "real" / "inventory_sample.xlsx"
MONTHLY_SAMPLE = PROJECT_ROOT / "samples" / "real" / "monthly_consumption_sample.xlsx"
DETAIL_SHEET = "ریز اطلاعات"
DEFAULT_ADMIN_NAME = "مالک سیستم"


def _normalise_header(value: object) -> str:
    """Normalise the Arabic/Persian variants used by plant spreadsheets."""
    return (
        str(value)
        .strip()
        .replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
    )


def _normalise_category(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) <= 4:
        text = text.zfill(4)
    return text if len(text) == 4 and text.isdigit() else None


def _copy_sample(sample: Path, destination: Path) -> Path:
    if not sample.exists():
        raise FileNotFoundError(f"فایل نمونه یافت نشد: {sample}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sample, destination)
    return destination


def _convert_monthly_sample(source: Path, destination: Path, uid: str, allowlist: set[str]) -> Path:
    """Convert the 571-row plant detail sheet to the canonical bot schema."""
    raw = pd.read_excel(source, sheet_name=DETAIL_SHEET, engine="openpyxl")
    raw.columns = [_normalise_header(column) for column in raw.columns]

    required = {"کد دسته بندی", "کد کالا", "مقدار", "ماه", "واحد", "شرح", "سفارش کار"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"ستون‌های لازم در برگه «{DETAIL_SHEET}» نیست: {', '.join(missing)}")

    raw["_category_code"] = raw["کد دسته بندی"].map(_normalise_category)
    filtered = raw[raw["_category_code"].isin(allowlist)].copy()
    quantity = pd.to_numeric(filtered["مقدار"], errors="coerce")
    filtered = filtered.loc[quantity.notna()].copy()
    filtered["_quantity"] = quantity.loc[filtered.index]

    status = (
        filtered["درخواستی-برگشتی"].astype(str)
        if "درخواستی-برگشتی" in filtered.columns
        else pd.Series("درخواستی", index=filtered.index)
    )
    converted = pd.DataFrame(
        {
            "domain": filtered["_category_code"].astype(str),
            "tundish_type": filtered["سفارش کار"].map(
                lambda v: tundish_type_label_for_work_order(v) or TUNDISH_TYPE_LABELS[0]
            ),
            "assignee_id": uid,
            "assignee_name": DEFAULT_ADMIN_NAME,
            "material_name": filtered.apply(
                lambda row: (
                    f"{row['کد کالا']} - {str(row['شرح']).strip()}"
                ).strip(" -"),
                axis=1,
            ),
            "month": filtered["ماه"].astype(str),
            "quantity": filtered["_quantity"],
            "unit": filtered["واحد"].astype(str),
            "status": status,
            "notes": filtered.apply(
                lambda row: f"cat={row['_category_code']}; order={row['سفارش کار']}",
                axis=1,
            ),
            "work_order": filtered["سفارش کار"],
            "id": filtered["کد کالا"].map(
                lambda v: (
                    str(v).strip()[:-2]
                    if str(v).strip().endswith(".0") and str(v).strip()[:-2].replace("-", "").isalnum()
                    else str(v).strip()
                )
                if v is not None and not (isinstance(v, float) and pd.isna(v))
                else None
            ),
            "coefficient": (
                pd.to_numeric(filtered["ضریب"], errors="coerce").fillna(1.0)
                if "ضریب" in filtered.columns
                else 1.0
            ),
            "description": filtered["شرح"].astype(str),
            "category_code": filtered["_category_code"].astype(str),
            "request_return": status,
        }
    )
    if converted.empty:
        raise ValueError("پس از فیلتر کدهای دسته‌بندی، مصرف ماهیانه‌ای باقی نماند.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    converted.to_excel(destination, index=False, engine="openpyxl")
    return destination


def seed(uid: str | int | None = None, *, db: Database | None = None) -> dict[str, Any]:
    """Reload both real samples for ``uid`` using the normal DB pipeline."""
    ensure_dirs()
    database = db or Database()
    user_id = str(uid or ADMIN_BALE_USER_ID or "1644670601")
    database.ensure_default_category_codes()
    session = database.get_or_create_session(user_id)
    session_id = int(session["id"])
    destination = UPLOAD_DIR / user_id / str(session_id)
    destination.mkdir(parents=True, exist_ok=True)
    allowlist = database.active_category_code_set()

    inventory_raw = _copy_sample(
        INVENTORY_SAMPLE, destination / "product_inventory.xlsx"
    )
    inventory = extract_and_save_clean(
        inventory_raw,
        "product_inventory",
        category_allowlist=allowlist,
    )
    session = database.store_file_slot(
        user_id, "product_inventory", str(inventory.clean_path)
    )
    database.save_extracted(
        bale_user_id=user_id,
        session_id=session["id"],
        file_type="product_inventory",
        raw_path=str(inventory.raw_path),
        clean_path=str(inventory.clean_path),
        row_count=inventory.kept_row_count,
        columns=inventory.columns,
    )
    catalog = database.seed_catalog_from_inventory_extract(inventory.clean_path)

    monthly_raw = _convert_monthly_sample(
        MONTHLY_SAMPLE,
        destination / "monthly_consumption.xlsx",
        user_id,
        allowlist,
    )
    monthly = extract_and_save_clean(monthly_raw, "monthly_consumption")
    session = database.store_file_slot(
        user_id, "monthly_consumption", str(monthly.clean_path)
    )
    database.save_extracted(
        bale_user_id=user_id,
        session_id=session["id"],
        file_type="monthly_consumption",
        raw_path=str(monthly.raw_path),
        clean_path=str(monthly.clean_path),
        row_count=monthly.kept_row_count,
        columns=monthly.columns,
    )

    wo_sync = database.sync_catalog_groups_from_monthly_path(MONTHLY_SAMPLE)
    if not wo_sync.get("ok"):
        wo_sync = database.sync_catalog_groups_from_monthly_path(monthly.clean_path)

    return {
        "uid": user_id,
        "session_id": session["id"],
        "inventory": {
            "raw": inventory.raw_row_count,
            "kept": inventory.kept_row_count,
        },
        "monthly": {
            "raw": monthly.raw_row_count,
            "kept": monthly.kept_row_count,
        },
        "catalog": catalog,
        "work_order_groups": wo_sync,
    }


if __name__ == "__main__":
    print(seed())
