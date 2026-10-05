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
    _fa_text,
    _is_blank,
    _normalize_columns,
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
    "id": "شناسه مواد",
    "product_name": "شرح کالا",
    "work_order": "شماره دستور کار",
    "usage_location": "محل استفاده",
    "keyword": "کلید واژه",
    "quantity": "موجودی",
    "priority": "اولویت",
    "contractor_or_company": "پیمانکار / شرکت",
    "origin": "سازنده",
    "shared": "اشتراکی",
    "critical_point": "نقطه بحرانی",
    "unit": "واحد",
    "casting_floor": "سطح ریخته گری",
    "billet_renovation": "نوسازی تاندیش بیلت",
    "billet_patching": "پچینگ تاندیش بیلت",
    "bloom_renovation": "نوسازی تاندیش بلوم",
    "bloom_patching": "پچینگ تاندیش بلوم",
    "slab_renovation": "نوسازی تاندیش اسلب",
    "slab_patching": "پچینگ تاندیش اسلب",
}

# File phrases for محل استفاده do NOT use ZWNJ (ریخته گری, not ریخته‌گری).
# Casting-floor shroud labels stay distinct from tundish اسلب/بلوم/بیلت.
SHROUD_SLAB_LABEL = "سطح ریخته گری اسلب"
SHROUD_BLOOM_LABEL = "سطح ریخته گری بلوم"
SHROUD_BILLET_LABEL = "سطح ریخته گری بیلت"
SHROUD_CASTING_LABELS = (
    SHROUD_SLAB_LABEL,
    SHROUD_BLOOM_LABEL,
    SHROUD_BILLET_LABEL,
)
# Default shroud label (these plant items are slab). Kept name for callers.
SHROUD_LOCATION_LABEL = SHROUD_SLAB_LABEL
# Previous single label (with ZWNJ) — recognized, never written again.
LEGACY_SHROUD_LOCATION_LABEL = "سطح ریخته‌گری"
LEGACY_SHROUD_LOCATION_LABEL_NO_ZWNJ = "سطح ریخته گری"
_SHROUD_BLOOM_BILLET_LABELS = frozenset({SHROUD_BLOOM_LABEL, SHROUD_BILLET_LABEL})

_LOCATION_ORDER = (
    "اسلب",
    "بلوم",
    "بیلت",
    "بلوم / بیلت",
    "بلوم/اسلب",
    *SHROUD_CASTING_LABELS,
    "سطح ریخته گری اسلب/بلوم",
    "سایر نواحی",
)

_SHROUD_NEEDLES = ("shroud", "شرود")
_SHROUD_GROUP_BY_TUNDISH = {
    "slab": SHROUD_SLAB_LABEL,
    "bloom": SHROUD_BLOOM_LABEL,
    "billet": SHROUD_BILLET_LABEL,
}


def _is_shroud_name(value: object) -> bool:
    """True when product/material name mentions shroud / شرود (case-insensitive)."""
    text = _cell_str(value)
    if not text:
        return False
    folded = text.casefold()
    return any(needle.casefold() in folded or needle in text for needle in _SHROUD_NEEDLES)


def _norm_id(value: object) -> str:
    return _normalize_key_part(value)


def _cell_str(value: object) -> str:
    """Normalize yeh/kaf and extra spaces. Does not rewrite محل استفاده phrases."""
    return _fa_text(value)


def _canon_location(value: object) -> str:
    """Location key for comparisons: ZWNJ becomes a space, then spaces collapse.

    Storage keeps the file phrase. «سطح ریخته‌گری اسلب» matches «سطح ریخته گری اسلب».
    """
    return " ".join(_cell_str(value).replace("\u200c", " ").split())


def _group_from_tundish_text(value: object) -> str | None:
    """slab/bloom/billet when tundish text names exactly one group, else None."""
    if _is_blank(value):
        return None
    text = _cell_str(value)
    if not text:
        return None
    folded = text.casefold()
    hits: list[str] = []
    if "بلوم" in text or "bloom" in folded:
        hits.append("bloom")
    if "بیلت" in text or "billet" in folded:
        hits.append("billet")
    if "اسلب" in text or "slab" in folded:
        hits.append("slab")
    if len(hits) == 1:
        return hits[0]
    return None


def shroud_location_label(name: object, tundish_type: object = None) -> str | None:
    """Casting-floor label for a shroud name, or None if the name is not a shroud.

    Bloom/billet only when tundish type says so. Otherwise «سطح ریخته گری اسلب»
    (no ZWNJ; matches the منبع اصلی file). Never returns plain اسلب/بلوم/بیلت.
    """
    if not _is_shroud_name(name):
        return None
    group = _group_from_tundish_text(tundish_type)
    if group in _SHROUD_GROUP_BY_TUNDISH:
        return _SHROUD_GROUP_BY_TUNDISH[group]
    return SHROUD_SLAB_LABEL


def location_label_from_consumption_row(row: Mapping[str, Any] | pd.Series) -> str | None:
    """Pick the best محل استفاده label from a monthly/consumption row.

    Preference:
      0. material/product name contains shroud/شرود → سطح ریخته گری اسلب,
         or بلوم/بیلت casting-floor label when tundish type says so
      1. work_order → اسلب / بلوم / بیلت / سایر نواحی
      2. tundish_type (strip «تاندیش » / map known labels)
      3. non-numeric domain text (skip bare category codes)
    """
    get = row.get if hasattr(row, "get") else None

    def _get(key: str):
        if get is not None:
            return get(key)
        try:
            return row[key]
        except Exception:  # noqa: BLE001
            return None

    name = _get("material_name") or _get("product_name")
    shroud_label = shroud_location_label(name, _get("tundish_type"))
    if shroud_label:
        return shroud_label

    group = group_for_work_order(_get("work_order"))
    if group:
        return GROUP_LABELS_FA.get(group) or group

    tt = _get("tundish_type")
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

    domain = _get("domain")
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

    Order: اسلب، بلوم، بیلت، combined file phrases, then the three سطح ریخته گری
    labels, سایر نواحی, then leftovers. Any casting-floor shroud label wins alone
    (plain اسلب is dropped).
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
        # Casting-floor shroud wins alone — never keep plain اسلب/بلوم/بیلت with it.
        casting = [lab for lab in SHROUD_CASTING_LABELS if lab in labels]
        if not casting and (
            LEGACY_SHROUD_LOCATION_LABEL in labels
            or LEGACY_SHROUD_LOCATION_LABEL_NO_ZWNJ in labels
        ):
            casting = [SHROUD_SLAB_LABEL]
        if casting:
            out[iid] = "، ".join(casting)
            continue
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
    # English keys or Persian headers (re-export / old 7-col files).
    return ensure_inventory_columns(_normalize_columns(df))


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
            raw = "" if value is None else str(value).strip()
            if not raw or raw.casefold() in {"nan", "none"}:
                df.at[idx, key] = 1
            else:
                try:
                    df.at[idx, key] = int(float(raw))
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
    allow_update: bool = False,
) -> dict[str, Any]:
    """Append a new inventory row.

    If ``id`` (شناسه مواد) already exists and ``allow_update`` is False, raises
    ValueError so bot/web can ask the user to confirm overwrite or edit instead.
    """
    item_id = _cell_str(record.get("id"))
    if not item_id:
        raise ValueError("شناسه (id) الزامی است.")
    df = load_primary_frame(db, bale_user_id=bale_user_id)
    if df is None:
        df = ensure_inventory_columns(pd.DataFrame())

    idx, _existing = find_row_by_id(df, item_id)
    if idx is not None and not allow_update:
        raise ValueError(
            f"شناسه «{item_id}» از قبل در منبع اصلی هست. "
            "برای ویرایش از «✏️ ویرایش رکورد» استفاده کنید یا با تأیید جایگزینی دوباره بفرستید."
        )
    row_data = {col: "" for col in INVENTORY_COLUMNS}
    row_data["priority"] = 1
    row_data["quantity"] = None
    for col in INVENTORY_COLUMNS:
        if col in record and record.get(col) is not None:
            row_data[col] = record.get(col)
    row_data["id"] = item_id
    # Normalize types
    for col in INVENTORY_COLUMNS:
        if col in {"quantity", "priority", "category_code"}:
            continue
        row_data[col] = _cell_str(row_data.get(col))
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
    raw_priority = row_data.get("priority")
    raw_priority_text = "" if raw_priority is None else str(raw_priority).strip()
    if not raw_priority_text or raw_priority_text.casefold() in {"nan", "none"}:
        row_data["priority"] = 1
    else:
        try:
            row_data["priority"] = int(float(raw_priority_text))
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
    norm_map = {_norm_id(k): v for k, v in location_map.items() if _norm_id(k)}
    if not norm_map:
        return out, 0
    updated = 0
    for idx, row in out.iterrows():
        iid = _norm_id(row.get("id"))
        if not iid or iid not in norm_map:
            continue
        new_val = norm_map[iid]
        old_val = _cell_str(row.get("usage_location"))
        if only_blank and old_val:
            continue
        if old_val != new_val:
            out.at[idx, "usage_location"] = new_val
            updated += 1
    return out, updated


def _location_parts(value: object) -> list[str]:
    text = _cell_str(value)
    if not text:
        return []
    return [p.strip() for p in text.replace(",", "،").split("،") if p.strip()]


def _keeps_bloom_or_billet_floor(value: object) -> bool:
    """True when محل استفاده already names bloom/billet casting floor."""
    parts = [_canon_location(p) for p in _location_parts(value)]
    canon_labs = {_canon_location(lab) for lab in _SHROUD_BLOOM_BILLET_LABELS}
    if any(p in canon_labs for p in parts):
        return True
    text = _canon_location(value)
    return any(_canon_location(lab) in text for lab in _SHROUD_BLOOM_BILLET_LABELS)


def _is_casting_floor_location(value: object) -> bool:
    """True for one casting-floor label or a join of only those labels."""
    parts = [_canon_location(p) for p in _location_parts(value)]
    allowed = {_canon_location(lab) for lab in SHROUD_CASTING_LABELS}
    return bool(parts) and all(p in allowed for p in parts)


def _shroud_ids_with_bloom_or_billet(inv_df: pd.DataFrame) -> set[str]:
    """Inventory shroud ids whose محل استفاده is already bloom/billet casting floor."""
    protected: set[str] = set()
    if inv_df is None or inv_df.empty or "product_name" not in inv_df.columns:
        return protected
    for _, row in inv_df.iterrows():
        if not _is_shroud_name(row.get("product_name")):
            continue
        iid = _norm_id(row.get("id"))
        if not iid:
            continue
        if _keeps_bloom_or_billet_floor(row.get("usage_location")):
            protected.add(iid)
    return protected


def usage_map_keeping_shroud_floors(
    inv_df: pd.DataFrame,
    location_map: Mapping[str, str],
) -> dict[str, str]:
    """Copy of location_map safe to apply onto an inventory that contains shrouds.

    A shroud row that already has محل استفاده (the منبع اصلی file value) is
    left alone — monthly sync must not clobber it. Blank shroud rows may still
    receive a casting-floor label. A shroud id is never written back to plain اسلب.
    """
    if not location_map:
        return {}
    occupied: set[str] = set()
    shroud_ids: set[str] = set()
    if inv_df is not None and not inv_df.empty and "product_name" in inv_df.columns:
        for _, row in inv_df.iterrows():
            if not _is_shroud_name(row.get("product_name")):
                continue
            iid = _norm_id(row.get("id"))
            if not iid:
                continue
            shroud_ids.add(iid)
            if _cell_str(row.get("usage_location")):
                occupied.add(iid)
    out: dict[str, str] = {}
    for key, value in location_map.items():
        iid = _norm_id(key)
        if not iid or iid in occupied:
            continue
        label = _cell_str(value)
        if label in {LEGACY_SHROUD_LOCATION_LABEL, LEGACY_SHROUD_LOCATION_LABEL_NO_ZWNJ}:
            label = SHROUD_SLAB_LABEL
        if iid in shroud_ids and not _is_casting_floor_location(label):
            label = SHROUD_SLAB_LABEL
        if label:
            out[iid] = label
    return out


def force_shroud_usage_locations(inv_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Fill blank shroud محل استفاده with «سطح ریخته گری اسلب».

    Any location already stored (file value, including plain اسلب or a
    casting-floor phrase) is kept. Does not invent bloom/billet rows.
    """
    out = ensure_inventory_columns(inv_df)
    if out.empty or "product_name" not in out.columns:
        return out, 0
    updated = 0
    for idx, row in out.iterrows():
        if not _is_shroud_name(row.get("product_name")):
            continue
        old_val = _cell_str(row.get("usage_location"))
        if old_val:
            continue
        out.at[idx, "usage_location"] = SHROUD_SLAB_LABEL
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
    inv = load_primary_frame(db, bale_user_id=bale_user_id)
    if inv is None or inv.empty:
        return {
            "ok": False,
            "updated": 0,
            "mapped": len(loc_map),
            "reason": "no_inventory",
        }
    updated = 0
    new_inv = inv
    # Shroud rows that already have a file location are not overwritten.
    # Blank shroud ids still receive a casting-floor label, never plain اسلب.
    safe_map = usage_map_keeping_shroud_floors(inv, loc_map)
    if safe_map:
        new_inv, updated = apply_usage_locations(inv, safe_map, only_blank=False)
    # Only blank shroud rows are filled. A location already in منبع اصلی stays.
    new_inv, shroud_n = force_shroud_usage_locations(new_inv)
    updated += shroud_n
    if not loc_map and shroud_n == 0:
        return {"ok": True, "updated": 0, "mapped": 0, "reason": "no_locations"}
    if updated:
        path = persist_primary_frame(db, new_inv, bale_user_id=bale_user_id)
    else:
        path = resolve_primary_inventory_path(db, bale_user_id=bale_user_id)
    return {
        "ok": True,
        "updated": updated,
        "mapped": len(loc_map),
        "shroud_forced": shroud_n,
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
