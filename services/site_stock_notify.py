"""Notify a configured Bale group after daily site-stock is saved.

Used by the bot (and optionally the web dashboard). Never raises into the
caller — missing config or API errors are logged and skipped so submit flow
stays intact.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from bot.activity import TUNDISH_SHORT_FA
from bot.jalali import format_date, tehran_now
from config import SITE_STOCK_GROUPS, SITE_STOCK_REPORT_GROUP_ID

logger = logging.getLogger(__name__)

SETTING_KEY = "site_stock_report_group_id"


def resolve_report_group_id(db: Any) -> Optional[str]:
    """Prefer DB bot_settings, then env ``SITE_STOCK_REPORT_GROUP_ID``."""
    try:
        raw = db.get_setting(SETTING_KEY) if db is not None else None
    except Exception:  # noqa: BLE001
        logger.exception("resolve_report_group_id: get_setting failed")
        raw = None
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    env = (SITE_STOCK_REPORT_GROUP_ID or "").strip()
    return env or None


def build_site_stock_report_text(
    *,
    registrar_name: str,
    tundish_group: str,
    entry_date: str,
    saved: int,
    items_total: int,
    values: Mapping[str, Any] | None = None,
    id_to_item: Mapping[str, Mapping[str, Any]] | None = None,
    include_item_lines: bool = True,
    max_item_lines: int = 8,
) -> str:
    """Persian summary for the report group (readable, compact)."""
    label = SITE_STOCK_GROUPS.get(tundish_group, tundish_group)
    short = TUNDISH_SHORT_FA.get(str(tundish_group), str(tundish_group) or "—")
    now = tehran_now()
    time_s = f"{now.hour:02d}:{now.minute:02d}"
    values = values or {}
    total_qty = 0.0
    for qty in values.values():
        try:
            total_qty += float(qty)
        except (TypeError, ValueError):
            pass

    lines = [
        "📦 موجودی روزانه سایت ثبت شد",
        "",
        f"گروه: {label} ({short})",
        f"تاریخ موجودی: {format_date(entry_date)}",
        f"زمان ثبت: {format_date(now)} ساعت {time_s}",
        f"ثبت‌کننده: {registrar_name or 'کاربر بدون نام'}",
        f"تعداد اقلام: {int(saved)} از {int(items_total)}",
    ]
    if values:
        lines.append(f"مجموع مقادیر: {total_qty:g}")

    if include_item_lines and values and len(values) <= max_item_lines:
        lines.append("")
        lines.append("اقلام:")
        id_to_item = id_to_item or {}
        for iid, qty in values.items():
            it = id_to_item.get(iid) or {"id": iid, "name_desc": iid}
            name = str(it.get("name_desc") or it.get("id") or iid).strip() or str(iid)
            try:
                qty_s = f"{float(qty):g}"
            except (TypeError, ValueError):
                qty_s = str(qty)
            lines.append(f"• {name}: {qty_s}")
    elif values and len(values) > max_item_lines:
        lines.append("")
        lines.append(f"(جزئیات {len(values)} قلم — در صورت تولید، فایل پیوست شده است)")

    return "\n".join(lines)


def notify_site_stock_saved(
    client: Any,
    db: Any,
    *,
    registrar_name: str,
    tundish_group: str,
    entry_date: str,
    saved: int,
    items_total: int,
    values: Mapping[str, Any] | None = None,
    id_to_item: Mapping[str, Mapping[str, Any]] | None = None,
    attachment_path: Path | str | None = None,
) -> bool:
    """Send summary (+ optional PDF/xlsx) to the configured group.

    Returns True if a message was sent. Failures are logged; never raised.
    """
    chat_id = resolve_report_group_id(db)
    if not chat_id:
        logger.info(
            "site-stock group report skipped (no %s / SITE_STOCK_REPORT_GROUP_ID)",
            SETTING_KEY,
        )
        return False
    if client is None:
        logger.warning("site-stock group report skipped: no Bale client")
        return False
    if int(saved) <= 0 and not (values or {}):
        logger.info("site-stock group report skipped: nothing saved")
        return False
    try:
        text = build_site_stock_report_text(
            registrar_name=registrar_name,
            tundish_group=tundish_group,
            entry_date=entry_date,
            saved=saved,
            items_total=items_total,
            values=values,
            id_to_item=id_to_item,
        )
        client.send_message(chat_id, text)
    except Exception:  # noqa: BLE001
        logger.exception("site-stock group sendMessage failed chat_id=%s", chat_id)
        return False

    if attachment_path:
        path = Path(attachment_path)
        if path.is_file():
            try:
                client.send_document(
                    chat_id,
                    path,
                    caption=f"جدول موجودی — {SITE_STOCK_GROUPS.get(tundish_group, tundish_group)}",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "site-stock group sendDocument failed chat_id=%s path=%s",
                    chat_id,
                    path,
                )
    return True
