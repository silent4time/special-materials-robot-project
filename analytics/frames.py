"""Shared frame loaders for bot + web.

Architectural rule (منبع اصلی):
  Cleaned ``product_inventory`` (latest extract in ``extracted_datasets``) is the
  canonical master table for warehouse / plant report facts.

  Cleaned columns: category_code, id, product_name, keyword, usage_location, quantity, priority.

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
    """Factory-wide newest extract (phase 2 item 16: one shared منبع اصلی / data set).

    ``user`` is kept for call-site compatibility; the caller's own older upload is
    only a fallback when no plant-wide row exists (cannot happen in practice).
    """
    row = db.get_latest_extracted_any(file_type)
    if row:
        return row
    uid = (user or {}).get("bale_user_id")
    return db.get_latest_extracted(uid, file_type) if uid else None


def load_extract_frame(
    db: Database,
    file_type: str,
    user: dict[str, Any],
) -> Optional[pd.DataFrame]:
    """Load a cleaned extract frame: factory-wide latest (own latest = disk fallback)."""
    uid = user.get("bale_user_id")
    candidates: list[dict] = []
    any_row = db.get_latest_extracted_any(file_type)
    if any_row:
        candidates.append(any_row)
    if uid:
        own = db.get_latest_extracted(uid, file_type)
        if own and (not candidates or own.get("id") != candidates[0].get("id")):
            candidates.append(own)
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


def has_interactive_site_stock(db: Database) -> bool:
    """True when at least one interactive daily site-stock row exists."""
    return bool(db.get_latest_site_stock_date())


def resolve_extract_path(
    db: Database,
    file_type: str,
    *,
    bale_user_id: str | int | None = None,
    session_path: str | None = None,
) -> str | None:
    """Newest on-disk cleaned path for any extract type.

    Order for non-inventory: plant-wide latest → session (if on disk) → user latest.
    For ``product_inventory`` prefer canonical latest extract over a stale session
    slot (same rule as ``resolve_primary_inventory_path``).
    """
    if file_type == PRIMARY_INVENTORY_TYPE:
        return resolve_primary_inventory_path(
            db,
            bale_user_id=bale_user_id,
            session_inventory_path=session_path,
        )

    candidates: list[str] = []
    any_row = db.get_latest_extracted_any(file_type)
    if any_row and any_row.get("clean_path"):
        candidates.append(str(any_row["clean_path"]))
    if session_path and str(session_path) not in candidates:
        candidates.append(str(session_path))
    if bale_user_id is not None:
        own = db.get_latest_extracted(bale_user_id, file_type)
        if own and own.get("clean_path"):
            path = str(own["clean_path"])
            if path not in candidates:
                candidates.append(path)
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


def data_completeness(
    db: Database,
    user: dict[str, Any] | None = None,
    *,
    session: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """DB-aware completeness for bot + web report gates.

    Keys:
      - product_inventory / monthly_consumption / tank_consumption: on-disk
        cleaned Excel extract (factory-wide latest; session / own = fallback).
      - site_stock: interactive ``site_stock_entries`` rows present.

    Excel upload remains an optional refresh path. Interactive site stock does
    not invent a tank Excel frame; ``missing_files_for_goal`` treats it as a
    remaining/critical source separately.
    """
    uid = (user or {}).get("bale_user_id")
    session = session or {}
    session_cols = {
        "tank_consumption": "tank_path",
        "product_inventory": "inventory_path",
        "monthly_consumption": "monthly_path",
    }
    out: dict[str, bool] = {}
    for ft_key, col in session_cols.items():
        path = resolve_extract_path(
            db,
            ft_key,
            bale_user_id=uid,
            session_path=session.get(col),
        )
        out[ft_key] = bool(path)
    out["site_stock"] = has_interactive_site_stock(db)
    return out


def completeness_status_lines(
    db: Database,
    user: dict[str, Any] | None = None,
    *,
    session: dict[str, Any] | None = None,
) -> list[str]:
    """Persian status lines distinguishing Excel extract vs interactive site stock."""
    uid = (user or {}).get("bale_user_id")
    session = session or {}
    marks = {True: "✅", False: "⏳"}
    lines: list[str] = []
    tank_path = resolve_extract_path(
        db,
        "tank_consumption",
        bale_user_id=uid,
        session_path=session.get("tank_path"),
    )
    site_day = db.get_latest_site_stock_date()
    if tank_path:
        lines.append(f"{marks[True]} {FILE_TYPES['tank_consumption']['label_fa']} (Excel)")
    elif site_day:
        lines.append(
            f"{marks[True]} {FILE_TYPES['tank_consumption']['label_fa']} "
            f"(ورود تعاملی — آخرین ثبت {_jalali_day(site_day)})"
        )
    else:
        lines.append(f"{marks[False]} {FILE_TYPES['tank_consumption']['label_fa']}")
    for ft_key in ("product_inventory", "monthly_consumption"):
        col = "inventory_path" if ft_key == "product_inventory" else "monthly_path"
        path = resolve_extract_path(
            db, ft_key, bale_user_id=uid, session_path=session.get(col)
        )
        lines.append(f"{marks[bool(path)]} {FILE_TYPES[ft_key]['label_fa']}")
    return lines



def resolve_primary_inventory_path(
    db: Database,
    *,
    bale_user_id: str | int | None = None,
    session_inventory_path: str | None = None,
) -> str | None:
    """Path to the newest on-disk cleaned منبع اصلی.

    Phase 2 item 16: ONE factory-wide منبع اصلی — always the newest extract of any
    user (an edit by owner/manager/responsible_officer applies to everyone at once).
    Order: plant-wide latest → user's latest (disk fallback) → session slot.
    """
    candidates: list[str] = []
    any_row = db.get_latest_extracted_any(PRIMARY_INVENTORY_TYPE)
    if any_row and any_row.get("clean_path"):
        candidates.append(str(any_row["clean_path"]))
    if bale_user_id is not None:
        own = db.get_latest_extracted(bale_user_id, PRIMARY_INVENTORY_TYPE)
        if own and own.get("clean_path"):
            path = str(own["clean_path"])
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


def _jalali_day(value: object) -> str:
    """«2026-09-27» → «1405/07/05» for user-facing status lines."""
    try:
        from bot.jalali import format_date

        return format_date(value) or str(value)
    except Exception:  # noqa: BLE001
        return str(value)
