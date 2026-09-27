"""Load analytics frames from DB extracts without BotApp / bale_api."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from analytics.tundish import apply_inventory_ledger, remaining
from db.models import Database
from excel.processor import process_file

logger = logging.getLogger(__name__)

FILE_TYPES = ("tank_consumption", "product_inventory", "monthly_consumption")


def letterhead_path(db: Database) -> Path | None:
    raw = db.get_setting("letterhead_pdf")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_file() else None


def _load_extract_frame(
    db: Database,
    file_type: str,
    user: dict[str, Any],
) -> Optional[pd.DataFrame]:
    """Prefer caller's latest extract, else plant-wide latest."""
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
    frames: dict[str, Optional[pd.DataFrame]] = {}
    for ft in FILE_TYPES:
        frames[ft] = _load_extract_frame(db, ft, user)
    return frames


def inventory_with_ledger(
    db: Database, frame: pd.DataFrame | None
) -> pd.DataFrame | None:
    if frame is None:
        return None
    sums = db.inventory_ledger_sums()
    return apply_inventory_ledger(frame, sums.get("by_id"), sums.get("by_name"))


def resolve_remaining(
    db: Database, frames: dict[str, Optional[pd.DataFrame]]
) -> tuple[pd.DataFrame, str]:
    """Prefer site stock (same as BotApp._resolve_remaining)."""
    rows = db.site_stock_as_remaining_rows()
    if rows:
        day = db.get_latest_site_stock_date() or "—"
        return remaining(pd.DataFrame(rows)), f"موجودی روزانه سایت ({day})"
    inv = inventory_with_ledger(db, frames.get("product_inventory"))
    rem = remaining(inv)
    return rem, "موجودی انبار"


def frames_completeness(frames: dict[str, Optional[pd.DataFrame]]) -> dict[str, bool]:
    return {k: v is not None and not getattr(v, "empty", True) for k, v in frames.items()}
