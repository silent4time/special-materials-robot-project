"""Load analytics frames from DB extracts — thin re-export of shared analytics.frames.

Bot and web both use ``analytics.frames`` so منبع اصلی resolution stays in sync.
"""
from __future__ import annotations

from pathlib import Path

from analytics.frames import (  # noqa: F401 — public API for web routers
    EXTRACT_FILE_TYPES,
    PRIMARY_INVENTORY_LABEL,
    PRIMARY_INVENTORY_TYPE,
    completeness_status_lines,
    data_completeness,
    frames_completeness,
    has_interactive_site_stock,
    inventory_with_ledger,
    latest_extract_row,
    load_extract_frame,
    load_frames,
    load_primary_inventory,
    resolve_extract_path,
    resolve_primary_inventory_path,
    resolve_remaining,
    resolve_warehouse_remaining,
)
from db.models import Database

# Keep FILE_TYPES name for any caller that imported the old tuple of keys.
FILE_TYPES = EXTRACT_FILE_TYPES


def letterhead_path(db: Database) -> Path | None:
    raw = db.get_setting("letterhead_pdf")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_file() else None
