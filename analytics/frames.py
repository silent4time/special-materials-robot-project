"""Shared frame loaders for bot + web.

Architectural rule (منبع اصلی):
  Cleaned ``product_inventory`` (latest extract in ``extracted_datasets``) is the
  canonical master table for warehouse / plant report facts.

  Cleaned columns: category_code, id, product_name, keyword, quantity, priority.

  Continuously refreshed by uploads that match this format (منوی «منبع اصلی»,
  and any consumables-path upload whose columns match the same schema).

  Site daily stock (``site_stock_entries``) is a separate on-site snapshot used
  only where product behavior intentionally prefers it (remaining / critical).
  Plant/warehouse reports (surplus, suggest, forecast, material-request,
  inbound, full analytics inventory) always root in منبع اصلی + ledger.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from analytics.tundish import apply_inventory_ledger, remaining
from config import FILE_TYPES
from db.models import Database
from excel.processor import process_file

logger = logging.getLogger(__name__)

EXTRACT_FILE_TYPES = ("tank_consumption", "product_inventory", "monthly_consumption")

# Canonical warehouse master key (cleaned extract type).
PRIMARY_INVENTORY_TYPE = "product_inventory"
PRIMARY_INVENTORY_LABEL = FILE_TYPES[PRIMARY_INVENTORY_TYPE]["label_fa"]  # منبع اصلی


def latest_extract_row(
    db: Database,
    file_type: str,
    user: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    """Prefer caller's newest extract, else plant-wide newest."""
    uid = (user or {}).get("bale_user_id")
    if uid:
        own = db.get_latest_extracted(uid, file_type)
        if own:
            return own
    return db.get_latest_extracted_any(file_type)


def load_extract_frame(
    db: Database,
    file_type: str,
    user: dict[str, Any],
) -> Optional[pd.DataFrame]:
    """Load a cleaned extract frame (own latest → plant-wide latest)."""
    uid = user.get("bale_user_id")
    candidates: list[dict] = []
    if uid:
        own = db.get_latest_extracted(uid, file_type)
        if own:
            candidates.append(own)
    any_row = db.get_latest_extracted_any(file_type)
    if any_row and (not candidates or any_row.get("id") != candidates[0].get("id")):
        candidates.append(any_row)
    for row in candidates:
        for key in ("clean_path", "raw_path"):
            path = row.get(key)
            if not path or not Path(str(path)).is_file():
                continue
            try:
                frame, _meta = process_file(path, file_type, user)
                return frame
            except Exception as exc:  # noqa: BLE001
                logger.warning("load %s from %s failed: %s", file_type, path, exc)
    return None


def load_frames(db: Database, user: dict[str, Any]) -> dict[str, Optional[pd.DataFrame]]:
    """Load all extract types from latest cleaned paths (bot + web shared)."""
    return {ft: load_extract_frame(db, ft, user) for ft in EXTRACT_FILE_TYPES}


def inventory_with_ledger(
    db: Database, frame: pd.DataFrame | None
) -> pd.DataFrame | None:
    """Apply inventory_ledger deltas onto a warehouse (منبع اصلی) frame."""
    if frame is None:
        return None
    sums = db.inventory_ledger_sums()
    return apply_inventory_ledger(frame, sums.get("by_id"), sums.get("by_name"))


def load_primary_inventory(
    db: Database, user: dict[str, Any]
) -> Optional[pd.DataFrame]:
    """Latest cleaned منبع اصلی + ledger (canonical warehouse quantities)."""
    frame = load_extract_frame(db, PRIMARY_INVENTORY_TYPE, user)
    return inventory_with_ledger(db, frame)


def resolve_warehouse_remaining(
    db: Database, frames: dict[str, Optional[pd.DataFrame]]
) -> tuple[pd.DataFrame, str]:
    """Plant/warehouse remaining from منبع اصلی (+ ledger). Never site stock."""
    inv = inventory_with_ledger(db, frames.get(PRIMARY_INVENTORY_TYPE))
    rem = remaining(inv)
    return rem, PRIMARY_INVENTORY_LABEL


def resolve_remaining(
    db: Database, frames: dict[str, Optional[pd.DataFrame]]
) -> tuple[pd.DataFrame, str]:
    """On-site remaining preferred; else canonical منبع اصلی (+ ledger).

    Intentional product split:
      • remaining / critical reports → site_stock_entries when present
      • all other warehouse facts → resolve_warehouse_remaining / load_primary_inventory
    """
    rows = db.site_stock_as_remaining_rows()
    if rows:
        day = db.get_latest_site_stock_date() or "—"
        return remaining(pd.DataFrame(rows)), f"موجودی روزانه سایت ({day})"
    inv = inventory_with_ledger(db, frames.get(PRIMARY_INVENTORY_TYPE))
    rem = remaining(inv)
    return rem, PRIMARY_INVENTORY_LABEL


def frames_completeness(frames: dict[str, Optional[pd.DataFrame]]) -> dict[str, bool]:
    return {k: v is not None and not getattr(v, "empty", True) for k, v in frames.items()}


def resolve_primary_inventory_path(
    db: Database,
    *,
    bale_user_id: str | int | None = None,
    session_inventory_path: str | None = None,
) -> str | None:
    """Path to the newest on-disk cleaned منبع اصلی.

    Order: user's latest extract → plant-wide latest → session slot (last resort).
    """
    candidates: list[str] = []
    if bale_user_id is not None:
        own = db.get_latest_extracted(bale_user_id, PRIMARY_INVENTORY_TYPE)
        if own and own.get("clean_path"):
            candidates.append(str(own["clean_path"]))
    any_row = db.get_latest_extracted_any(PRIMARY_INVENTORY_TYPE)
    if any_row and any_row.get("clean_path"):
        path = str(any_row["clean_path"])
        if path not in candidates:
            candidates.append(path)
    if session_inventory_path:
        path = str(session_inventory_path)
        if path not in candidates:
            candidates.append(path)
    for path in candidates:
        if Path(path).is_file():
            return path
    return None
