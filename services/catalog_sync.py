"""Automatic catalog sync from «منبع اصلی» (cleanup 1405-07-18, item 9).

Called after EVERY منبع اصلی change — stock upload / full replace (bot upload),
add / edit / delete record (bot + web via ``services.main_source``) — so the web
material-request form and bot pickers always list the current IDs with current names.

* every ID in the cleaned منبع اصلی → ``catalog_items`` (name + category updated);
* warehouse IDs no longer in منبع اصلی → deactivated (history rows keep their FK);
* daily-stock synthetic lines «SS:…» are managed by ``services.site_stock_lists``
  and never touched here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def sync_from_clean_path(db: Any, clean_path: str | Path | None) -> dict[str, int]:
    out = {"inserted": 0, "updated": 0, "deactivated": 0, "total": 0}
    if not clean_path or not Path(clean_path).exists():
        return out
    counts = db.seed_catalog_from_inventory_extract(clean_path, only_missing=False)
    out["inserted"] = int(counts.get("inserted", 0))
    out["updated"] = int(counts.get("updated", 0))
    out["total"] = int(counts.get("total_rows", 0))
    import pandas as pd

    df = pd.read_excel(clean_path, engine="openpyxl")
    ids = set()
    if df is not None and "id" in df.columns:
        ids = {str(v).strip() for v in df["id"].tolist() if str(v).strip() and str(v).strip().lower() != "nan"}
    if ids:
        for item in db.list_catalog_items(active_only=True):
            iid = str(item["id"]).strip()
            if iid.startswith("SS:") or iid in ids:
                continue
            db.deactivate_catalog_item(iid)
            out["deactivated"] += 1
    return out


def sync_after_change(db: Any, clean_path: str | Path | None) -> dict[str, int] | None:
    """Best-effort wrapper: never breaks the caller's save."""
    try:
        res = sync_from_clean_path(db, clean_path)
        logger.info("catalog sync from منبع اصلی: %s", res)
        return res
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog sync from منبع اصلی failed: %s", exc)
        return None


def note_fa(res: dict[str, int] | None) -> str:
    if not res:
        return ""
    return (
        f"\nاقلام فرم درخواست مواد همگام شد: +{res['inserted']} جدید، "
        f"{res['updated']} به‌روز، {res['deactivated']} غیرفعال."
    )
