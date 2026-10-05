"""User activity logging — Persian one-liners for گزارش فعالیت کاربران."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Short tundish labels for site-stock phrases
TUNDISH_SHORT_FA = {
    "slab": "اسلب",
    "bloom": "بلوم",
    "billet": "بیلت",
}

# action_key → Persian phrase after «کاربر {name} با آیدی {id} »
ACTION_PHRASES: dict[str, str] = {
    "upload_product_inventory": "فایل منبع اصلی را آپلود کرد",
    "upload_monthly_consumption": "فایل مصرف ماهیانه مواد را آپلود کرد",
    "site_stock_saved": "موجودی اقلام سایت ({group}) را ثبت کرد",
    "material_request_created": "درخواست مواد را ایجاد کرد",
    "material_request_confirmed": "درخواست مواد را تأیید کرد",
    "material_request_cancelled": "درخواست مواد را لغو کرد",
    "warehouse_return_confirmed": "برگشت به انبار را تأیید کرد",
    "report_daily": "گزارش مصرف روزانه مواد را گرفت",
    "report_period": "گزارش مصرف بازه‌ای را گرفت",
    "report_remaining": "گزارش موجودی و مواد بحرانی را گرفت",
    "report_surplus": "گزارش مواد مازاد را گرفت",
    "report_forecast": "گزارش پیش‌بینی نیاز تاندیش را گرفت",
    "report_suggest": "پیشنهاد درخواست مواد را گرفت",
    "report_monthly_summary": "گزارش خلاصه مصرف ماهیانه را گرفت",
    "report_inbound": "گزارش ورودی به انبار را گرفت",
    "report_critical_items": "گزارش اقلام بحرانی را گرفت",
    "report_main_goal": "گزارش هدف اصلی را گرفت",
    "critical_tundish_counts_saved": "تعداد تاندیش ماهانه اقلام بحرانی را ثبت کرد",
    "tundish_report_saved": "گزارش تاندیش بعد از ریخته‌گری ({group}) را ثبت کرد",
    "tundish_report_settings": "تنظیمات گزارش تاندیش بعد از ریخته‌گری را تغییر داد",
    "report_full_pdf": "PDF کامل تحلیل را گرفت",
    "report_generate_pdf": "گزارش کلی مواد را گرفت",
    "report_user_activity": "گزارش فعالیت کاربران را گرفت",
    "report_assistant_asked": "از دستیار هوشمند پرسش کرد",
    "settings_letterhead": "سربرگ PDF را تغییر داد",
    "settings_logo": "لوگوی ربات را تغییر داد",
    "settings_invite": "تنظیمات دعوت‌نامه را تغییر داد",
    "settings_stock_group": "گروه گزارش موجودی روزانه را تغییر داد",
    "user_add": "کاربر جدید دعوت کرد",
    "user_edit": "نقش کاربر را ویرایش کرد",
    "user_delete": "کاربر را حذف (غیرفعال) کرد",
}


def resolve_display_name(user: dict | None, **details: Any) -> str:
    """Prefer display_name / actor_display_name; fallback username or بدون‌نام."""
    user = user or {}
    uid = str(user.get("bale_user_id") or details.get("bale_user_id") or "").strip()

    def meaningful(value: object) -> str:
        text = str(value or "").strip()
        return text if text and text != uid else ""

    name = meaningful(user.get("display_name"))
    if not name:
        name = meaningful(details.get("actor_display_name") or details.get("display_name"))
    if not name:
        name = meaningful(user.get("username") or details.get("username"))
    return name or "بدون‌نام"


def format_action_phrase(action_key: str, **details: Any) -> str:
    """Build the action phrase portion (after name + id)."""
    template = ACTION_PHRASES.get(action_key)
    if not template:
        return details.get("phrase") or action_key
    group = details.get("group") or details.get("tundish_group") or ""
    if "{group}" in template:
        short = TUNDISH_SHORT_FA.get(str(group), str(group) or "—")
        return template.format(group=short)
    try:
        return template.format(**{k: v for k, v in details.items() if isinstance(v, (str, int, float))})
    except (KeyError, ValueError):
        return template


def format_activity_line(
    name: str,
    bale_user_id: str | int,
    action_key: str,
    **details: Any,
) -> str:
    """Persian one-liner: کاربر {name} با آیدی {id} {action_phrase}"""
    phrase = format_action_phrase(action_key, **details)
    return f"کاربر {name} با آیدی {bale_user_id} {phrase}"


def log_activity(db: Any, user: dict | None, action_key: str, **details: Any) -> None:
    """Persist an activity row. Never raises — logging must not break main flow."""
    try:
        user = user or {}
        uid = str(user.get("bale_user_id") or details.get("bale_user_id") or "").strip()
        if not uid or not action_key:
            return
        name = resolve_display_name(user, **details)
        message_fa = format_activity_line(name, uid, action_key, **details)
        # Drop non-serializable / redundant keys from details snapshot
        snap = {
            k: v
            for k, v in details.items()
            if k not in {"db", "user", "message"}
            and isinstance(v, (str, int, float, bool, type(None)))
        }
        db.insert_user_activity(
            bale_user_id=uid,
            display_name=name,
            action_key=action_key,
            message_fa=message_fa,
            details=snap or None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("log_activity failed action_key=%s", action_key)
