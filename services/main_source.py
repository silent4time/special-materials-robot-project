"""Canonical منبع اصلی (product_inventory) load / upsert / add / persist.

Shared by Bale bot settings and the web dashboard so cleaned extracts stay
in sync. Reports always read the latest cleaned extract via analytics.frames.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from analytics.frames import (
    PRIMARY_INVENTORY_TYPE,
    resolve_primary_inventory_path,
)
from config import REQUIRED_COLUMNS, UPLOAD_DIR
from db.models import Database
from excel.processor import (
    _is_blank,
    _normalize_key_part,
    merge_clean_frames,
    write_clean_excel,
)
from excel.work_order import (
    GROUP_LABELS_FA,
    UNKNOWN_GROUP,
    group_for_work_order,
)

logger = logging.getLogger(__name__)

INVENTORY_COLUMNS: list[str] = list(REQUIRED_COLUMNS["product_inventory"])

FIELD_LABELS_FA: dict[str, str] = {
    "category_code": "کد دسته بندی",
    "id": "شناسه",
    "product_name": "نام محصول",
    "keyword": "کلید واژه",
    "usage_location": "محل استفاده",
    "quantity": "موجودی",
    "priority": "اولویت",
}

# Short labels preferred for محل استفاده (work_order / tundish derived)
_LOCATION_ORDER = ("اسلب", "بلوم", "بیلت", "سایر نواحی")


def _norm_id(value: object) -> str:
    return _normalize_key_part(value)


def _cell_str(value: object) -> str:
    if _is_blank(value):
        return ""
    text = str(value).strip()
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    return " ".join(text.split())


def location_label_from_consumption_row(row: Mapping[str, Any] | pd.Series) -> str | None:
    """Pick the best محل استفاده label from a monthly/consumption row.

    Preference:
      1. work_order → اسلب / بلوم / بیلت / سایر نواحی
      2. tundish_type (strip «تاندیش » / map known labels)
      3. non-numeric domain text (skip bare category codes)
    """
    group = group_for_work_order(row.get("work_order") if hasattr(row, "get") else row["work_order"] if "work_order" in row else None)
    if group:
        return GROUP_LABELS_FA.get(group) or group

    tt = row.get("tundish_type") if hasattr(row, "get") else None
    if not _is_blank(tt):
        text = _cell_str(tt)
        # Exact canonical «تاندیش اسلب» etc.
        for key, short in GROUP_LABELS_FA.items():
            if key == UNKNOWN_GROUP:
                continue
            if short in text or text.casefold() == key:
                return short
        if text.startswith("تاندیش "):
            text = text[len("تاندیش ") :].strip()
        return text or None

    domain = row.get("domain") if hasattr(row, "get") else None
    if not _is_blank(domain):
        d = _cell_str(domain)
        # Skip 4-digit category codes used as plant domain placeholders
        if d.isdigit() and len(d) <= 4:
            return None
        if d.casefold() in {"plant", "nan"}:
            return None
        return d
    return None


def build_usage_location_map(monthly_df: pd.DataFrame | None) -> dict[str, str]:
    """Map item id → unique usage locations joined with «، ».

    Order: اسلب، بلوم، بیلت، سایر نواحی، then any leftover labels alphabetically.
    """
    if monthly_df is None or monthly_df.empty or "id" not in monthly_df.columns:
        return {}
    buckets: dict[str, set[str]] = {}
    for _, row in monthly_df.iterrows():
        iid = _norm_id(row.get("id"))
        if not iid:
            # Fall back to material_name token before « - »
            name = _cell_str(row.get("material_name") or row.get("product_name"))
            if " - " in name:
                iid = _norm_id(name.split(" - ", 1)[0])
            else:
                continue
        label = location_label_from_consumption_row(row)
        if not label:
            continue
        buckets.setdefault(iid, set()).add(label)

    out: dict[str, str] = {}
    for iid, labels in buckets.items():
        ordered = [lab for lab in _LOCATION_ORDER if lab in labels]
        rest = sorted(lab for lab in labels if lab not in _LOCATION_ORDER)
        out[iid] = "، ".join([*ordered, *rest])
    return out


def ensure_inventory_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee REQUIRED_COLUMNS order (soft-missing → empty)."""
    out = df.copy() if df is not None else pd.DataFrame()
    for col in INVENTORY_COLUMNS:
        if col not in out.columns:
            if col == "priority":
                out[col] = 1
            elif col == "quantity":
                out[col] = pd.NA
            else:
                out[col] = ""
    return out[INVENTORY_COLUMNS]


def load_primary_frame(
    db: Database,
    *,
    bale_user_id: str | int | None = None,
) -> pd.DataFrame | None:
    """Load latest cleaned منبع اصلی as a DataFrame (English column keys)."""
    path = resolve_primary_inventory_path(db, bale_user_id=bale_user_id)
    if not path:
        return None
    try:
        df = pd.read_excel(path, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001
        logger.warning("load primary inventory failed (%s): %s", path, exc)
        return None
    if df is None or df.empty:
        return ensure_inventory_columns(pd.DataFrame())
    return ensure_inventory_columns(df)


def persist_primary_frame(
    db: Database,
    df: pd.DataFrame,
    *,
    bale_user_id: str | int,
    session_id: int | None = None,
    raw_path: str | Path | None = None,
) -> Path:
    """Rewrite cleaned Excel + register extract + point session inventory slot."""
    clean = ensure_inventory_columns(df)
    uid = str(bale_user_id)
    session = db.get_or_create_session(uid)
    sid = int(session_id if session_id is not None else session["id"])

    dest_dir = UPLOAD_DIR / uid / str(sid) / "cleaned"
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean_path = dest_dir / f"{PRIMARY_INVENTORY_TYPE}.xlsx"

    # Preserve previous file as snapshot when overwriting in-place sibling
    existing = resolve_primary_inventory_path(db, bale_user_id=uid)
    if existing and Path(existing).is_file() and Path(existing).resolve() != clean_path.resolve():
        try:
            snap_dir = Path(existing).parent / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap = snap_dir / f"product_inventory_before_edit.xlsx"
            shutil.copy2(existing, snap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not snapshot inventory before edit: %s", exc)

    write_clean_excel(clean, clean_path, PRIMARY_INVENTORY_TYPE)

    raw = str(raw_path) if raw_path else str(clean_path)
    db.save_extracted(
        bale_user_id=uid,
        session_id=sid,
        file_type=PRIMARY_INVENTORY_TYPE,
        raw_path=raw,
        clean_path=str(clean_path),
        row_count=int(len(clean)),
        columns=list(INVENTORY_COLUMNS),
    )
    db.store_file_slot(uid, PRIMARY_INVENTORY_TYPE, str(clean_path))
    return clean_path


def find_row_by_id(df: pd.DataFrame, item_id: str) -> tuple[int | None, dict[str, Any] | None]:
    """Return (index, row dict) for the first matching id."""
    target = _norm_id(item_id)
    if not target or df is None or df.empty or "id" not in df.columns:
        return None, None
    for idx, row in df.iterrows():
        if _norm_id(row.get("id")) == target:
            return int(idx) if isinstance(idx, (int,)) else idx, {
                col: row.get(col) for col in INVENTORY_COLUMNS if col in df.columns
            }
    return None, None


def upsert_row(
    db: Database,
    item_id: str,
    updates: Mapping[str, Any],
    *,
    bale_user_id: str | int,
) -> dict[str, Any]:
    """Update fields of an existing inventory row by id; persist cleaned extract."""
    df = load_primary_frame(db, bale_user_id=bale_user_id)
    if df is None or df.empty:
        raise ValueError("منبع اصلی هنوز آپلود نشده است.")
    idx, current = find_row_by_id(df, item_id)
    if idx is None or current is None:
        raise KeyError(f"ردیفی با شناسه «{item_id}» یافت نشد.")
    allowed = {k: v for k, v in updates.items() if k in INVENTORY_COLUMNS and k != "id"}
    for key, value in allowed.items():
        if key == "quantity":
            df.at[idx, key] = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        elif key == "priority":
            try:
                df.at[idx, key] = int(float(str(value).strip() or 1))
            except (TypeError, ValueError):
                df.at[idx, key] = 1
        elif key == "category_code":
            text = _cell_str(value)
            if text.endswith(".0") and text[:-2].isdigit():
                text = text[:-2]
            if text.isdigit() and len(text) <= 4:
                text = text.zfill(4)
            df.at[idx, key] = text
        else:
            df.at[idx, key] = _cell_str(value)
    path = persist_primary_frame(db, df, bale_user_id=bale_user_id)
    _, updated = find_row_by_id(df, item_id)
    return {"path": str(path), "row": updated or {}, "id": _norm_id(item_id)}


def add_row(
    db: Database,
    record: Mapping[str, Any],
    *,
    bale_user_id: str | int,
) -> dict[str, Any]:
    """Append a new inventory row (or merge if id already exists)."""
    item_id = _cell_str(record.get("id"))
    if not item_id:
        raise ValueError("شناسه (id) الزامی است.")
    df = load_primary_frame(db, bale_user_id=bale_user_id)
    if df is None:
        df = ensure_inventory_columns(pd.DataFrame())

    idx, _existing = find_row_by_id(df, item_id)
    row_data = {col: "" for col in INVENTORY_COLUMNS}
    row_data["priority"] = 1
    row_data["quantity"] = None
    for col in INVENTORY_COLUMNS:
        if col in record and record.get(col) is not None:
            row_data[col] = record.get(col)
    row_data["id"] = item_id
    # Normalize types
    row_data["keyword"] = _cell_str(row_data.get("keyword"))
    row_data["usage_location"] = _cell_str(row_data.get("usage_location"))
    row_data["product_name"] = _cell_str(row_data.get("product_name"))
    cat = _cell_str(row_data.get("category_code"))
    if cat.endswith(".0") and cat[:-2].isdigit():
        cat = cat[:-2]
    if cat.isdigit() and len(cat) <= 4:
        cat = cat.zfill(4)
    row_data["category_code"] = cat
    try:
        row_data["quantity"] = float(pd.to_numeric(pd.Series([row_data.get("quantity")]), errors="coerce").iloc[0])
    except (TypeError, ValueError):
        row_data["quantity"] = None
    try:
        row_data["priority"] = int(float(str(row_data.get("priority") or 1)))
    except (TypeError, ValueError):
        row_data["priority"] = 1

    if idx is not None:
        for col in INVENTORY_COLUMNS:
            df.at[idx, col] = row_data[col]
        action = "updated"
    else:
        df = pd.concat([df, pd.DataFrame([row_data])], ignore_index=True)
        action = "added"

    path = persist_primary_frame(db, ensure_inventory_columns(df), bale_user_id=bale_user_id)
    return {"path": str(path), "row": row_data, "id": item_id, "action": action}


def apply_usage_locations(
    inv_df: pd.DataFrame,
    location_map: Mapping[str, str],
    *,
    only_blank: bool = False,
) -> tuple[pd.DataFrame, int]:
    """Set usage_location from map (by id). Returns (frame, updated_count)."""
    out = ensure_inventory_columns(inv_df)
    if not location_map:
        return out, 0
    updated = 0
    for idx, row in out.iterrows():
        iid = _norm_id(row.get("id"))
        if not iid or iid not in location_map:
            continue
        new_val = location_map[iid]
        old_val = _cell_str(row.get("usage_location"))
        if only_blank and old_val:
            continue
        if old_val != new_val:
            out.at[idx, "usage_location"] = new_val
            updated += 1
    return out, updated


def sync_usage_from_monthly(
    db: Database,
    monthly_df: pd.DataFrame | None,
    *,
    bale_user_id: str | int,
) -> dict[str, Any]:
    """Merge usage_location onto matching ids in latest منبع اصلی (qty untouched)."""
    loc_map = build_usage_location_map(monthly_df)
    if not loc_map:
        return {"ok": True, "updated": 0, "mapped": 0, "reason": "no_locations"}
    inv = load_primary_frame(db, bale_user_id=bale_user_id)
    if inv is None or inv.empty:
        return {"ok": False, "updated": 0, "mapped": len(loc_map), "reason": "no_inventory"}
    new_inv, updated = apply_usage_locations(inv, loc_map, only_blank=False)
    if updated:
        path = persist_primary_frame(db, new_inv, bale_user_id=bale_user_id)
    else:
        path = resolve_primary_inventory_path(db, bale_user_id=bale_user_id)
    return {
        "ok": True,
        "updated": updated,
        "mapped": len(loc_map),
        "path": str(path) if path else None,
    }


def merge_inventory_preserving_usage(
    old: pd.DataFrame | None,
    new: pd.DataFrame | None,
) -> pd.DataFrame:
    """Upsert inventory rows; blank usage_location in new keeps previous by id."""
    return merge_clean_frames(old, new, PRIMARY_INVENTORY_TYPE)


def format_row_fa(row: Mapping[str, Any]) -> str:
    """Human-readable Persian dump of one inventory row."""
    lines = []
    for col in INVENTORY_COLUMNS:
        label = FIELD_LABELS_FA.get(col, col)
        val = row.get(col)
        if _is_blank(val):
            shown = "—"
        else:
            shown = _cell_str(val)
        lines.append(f"• {label}: {shown}")
    return "\n".join(lines)


def list_ids_preview(df: pd.DataFrame | None, *, limit: int = 30) -> list[str]:
    """Short preview lines «id — product_name» for pick lists."""
    if df is None or df.empty:
        return []
    lines: list[str] = []
    for _, row in df.head(limit).iterrows():
        iid = _cell_str(row.get("id")) or "—"
        name = _cell_str(row.get("product_name"))
        loc = _cell_str(row.get("usage_location"))
        extra = f" [{loc}]" if loc else ""
        lines.append(f"{iid} — {name}{extra}" if name else f"{iid}{extra}")
    return lines
