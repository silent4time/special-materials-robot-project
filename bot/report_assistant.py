"""Local report-only AI assistant via self-hosted Ollama HTTP API.

Never calls cloud LLM APIs. Context is read-only summaries from the bot DB /
extracted report data. Off-scope topics must be refused in Persian.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import CRITICAL_DAYS, OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT

logger = logging.getLogger(__name__)

OLLAMA_DOWN_FA = (
    "دستیار محلی در دسترس نیست؛ Ollama را روی سرور بررسی کنید."
)

# Soft cap so prompts stay small even with noisy extracts
CONTEXT_MAX_CHARS = 3500
CONTEXT_GROUP_LINES = 12
CONTEXT_CRITICAL_LINES = 8

SYSTEM_PROMPT_FA = """تو دستیار گزارش‌های بازوی مواد تاندیش هستی.
فقط دربارهٔ گزارش‌ها و اعداد گزارش‌محور که در «زمینه» آمده صحبت کن و توضیح بده:
خلاصه مصرف ماهیانه، ورودی انبار، مصرف روزانه/بازه‌ای، موجودی و مواد بحرانی، مواد مازاد، پیش‌بینی، پیشنهاد درخواست، فعالیت کاربران، و مفاهیم PDF کامل تحلیل.

قوانین سخت:
۱) فقط از زمینهٔ داده‌شده پاسخ بده؛ اگر چیزی در زمینه نیست صریحاً بگو داده در زمینه نیست.
۲) هیچ عملی انجام نده و پیشنهاد اجرای عملیات نده (آپلود، ویرایش موجودی، موجودی سایت، درخواست مواد، برگشت انبار، مدیریت کاربر، تنظیمات ربات، سربرگ، سامانه خارجی).
۳) موضوعات خارج از گزارش را مؤدبانه به فارسی رد کن و بگو فقط دربارهٔ گزارش‌ها می‌توانی کمک کنی.
۴) عدد یا واقعیت از خودت نساز.
۵) پاسخ کوتاه، روشن و به فارسی باشد.
۶) هرگز توکن، رمز، مسیر محرمانه یا تنظیمات حساس نپرس و فاش نکن.
"""

# Phrases that strongly suggest off-scope write/admin actions (pre-filter + tests)
OFF_SCOPE_HINTS = (
    "آپلود",
    "آپلود کن",
    "حذف کاربر",
    "اضافه کردن کاربر",
    "دعوت کاربر",
    "تغییر نقش",
    "موجودی سایت را ثبت",
    "ثبت موجودی سایت",
    "درخواست مواد بده",
    "ثبت درخواست مواد",
    "برگشت به انبار را تأیید",
    "سربرگ",
    "لوگوی ربات",
    "تنظیمات ربات",
    "حذف فایل",
    "ویرایش موجودی انبار",
    "کسر از انبار",
    "openai",
    "chatgpt",
    "gpt-4",
    "claude",
)


def is_likely_off_scope(user_text: str) -> bool:
    """Heuristic for obvious write/admin/cloud asks (not the only guard — prompt is)."""
    text = (user_text or "").strip().casefold()
    if not text:
        return False
    return any(h.casefold() in text for h in OFF_SCOPE_HINTS)


def refuse_off_scope_fa() -> str:
    return (
        "فقط می‌توانم دربارهٔ گزارش‌ها و اعداد گزارش‌محور داخل ربات صحبت کنم. "
        "برای آپلود، ویرایش موجودی، درخواست مواد، مدیریت کاربر یا تنظیمات از منوی اصلی استفاده کنید."
    )


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_FA


def truncate_context(text: str, max_chars: int = CONTEXT_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n… (کوتاه‌شده)"


def format_user_payload(user_text: str, context: str) -> str:
    ctx = truncate_context(context) or "(هیچ زمینهٔ گزارشی در دسترس نیست.)"
    q = (user_text or "").strip() or "(بدون متن)"
    return (
        "## زمینه گزارش‌ها (فقط خواندنی — از دیتابیس/استخراج محلی)\n"
        f"{ctx}\n\n"
        "## پرسش کاربر\n"
        f"{q}"
    )


def build_chat_messages(user_text: str, context: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": format_user_payload(user_text, context)},
    ]


def build_report_context(db: Any, user: dict[str, Any] | None = None) -> str:
    """Build a short read-only Persian context string. Never raises; never includes secrets."""
    lines: list[str] = [
        "منبع: داده‌های محلی بازوی مواد تاندیش (فقط خواندنی).",
        f"آستانه مواد بحرانی: پوشش کمتر از {CRITICAL_DAYS} روز.",
    ]
    try:
        uid = str((user or {}).get("bale_user_id") or "")
        if uid:
            session = db.get_or_create_session(uid)
            done = db.session_completeness(session)
            lines.append(
                "وضعیت فایل‌های جلسه: "
                + "، ".join(
                    f"{k}={'✓' if done.get(k) else '✗'}"
                    for k in ("product_inventory", "monthly_consumption", "tank_consumption")
                )
            )

        # Recent activity count
        try:
            acts = db.list_user_activities(newest_first=True, limit=200)
            lines.append(f"تعداد فعالیت‌های اخیر ثبت‌شده (حداکثر ۲۰۰): {len(acts)}")
        except Exception:  # noqa: BLE001
            logger.debug("assistant context: activity count failed", exc_info=True)

        # Inbound delta count (latest vs previous inventory)
        if uid:
            try:
                from excel.inbound import compute_inbound_delta
                from excel.processor import process_file

                latest = db.get_latest_extracted(uid, "product_inventory")
                previous = db.get_previous_extracted(uid, "product_inventory")
                if latest and previous and latest.get("clean_path") and previous.get("clean_path"):
                    cur, _ = process_file(str(latest["clean_path"]), "product_inventory", user or {"role": "owner", "bale_user_id": uid})
                    prev, _ = process_file(str(previous["clean_path"]), "product_inventory", user or {"role": "owner", "bale_user_id": uid})
                    inbound = compute_inbound_delta(cur, prev)
                    lines.append(f"تعداد اقلام ورودی انبار (دلتا نسبت به موجودی قبلی): {len(inbound)}")
                elif latest and latest.get("clean_path"):
                    lines.append("ورودی انبار: پایه مقایسه (موجودی قبلی) موجود نیست.")
                else:
                    lines.append("ورودی انبار: موجودی انبار استخراج‌شده نیست.")
            except Exception:  # noqa: BLE001
                logger.debug("assistant context: inbound failed", exc_info=True)
                lines.append("ورودی انبار: محاسبه ممکن نشد.")

        # Critical / remaining snapshot (site stock preferred)
        try:
            from analytics.tundish import critical_materials, daily_rates, remaining
            import pandas as pd

            rem_df = None
            rem_src = "—"
            site_rows = db.site_stock_as_remaining_rows()
            if site_rows:
                rem_df = remaining(pd.DataFrame(site_rows))
                rem_src = "موجودی روزانه سایت"
            elif uid:
                latest = db.get_latest_extracted(uid, "product_inventory")
                if latest and latest.get("clean_path"):
                    from excel.processor import process_file

                    inv, _ = process_file(
                        str(latest["clean_path"]),
                        "product_inventory",
                        user or {"role": "owner", "bale_user_id": uid},
                    )
                    rem_df = remaining(inv)
                    rem_src = "موجودی انبار"
            rates = pd.DataFrame()
            if uid:
                monthly = db.get_latest_extracted(uid, "monthly_consumption")
                tank = db.get_latest_extracted(uid, "tank_consumption")
                mdf = tdf = None
                from excel.processor import process_file

                if monthly and monthly.get("clean_path"):
                    try:
                        mdf, _ = process_file(
                            str(monthly["clean_path"]),
                            "monthly_consumption",
                            user or {"role": "owner", "bale_user_id": uid},
                        )
                    except Exception:  # noqa: BLE001
                        mdf = None
                if tank and tank.get("clean_path"):
                    try:
                        tdf, _ = process_file(
                            str(tank["clean_path"]),
                            "tank_consumption",
                            user or {"role": "owner", "bale_user_id": uid},
                        )
                    except Exception:  # noqa: BLE001
                        tdf = None
                rates = daily_rates(tdf, mdf)
            if rem_df is not None and not rem_df.empty:
                lines.append(f"تعداد اقلام موجودی ({rem_src}): {len(rem_df)}")
                crit = critical_materials(rates, rem_df, CRITICAL_DAYS)
                lines.append(f"تعداد مواد بحرانی: {len(crit)}")
                if not crit.empty:
                    lines.append("نمونه مواد بحرانی (نام | پوشش روز):")
                    for _, row in crit.head(CONTEXT_CRITICAL_LINES).iterrows():
                        name = str(row.get("material_name") or "—")[:40]
                        cover = row.get("days_of_cover")
                        try:
                            cover_s = f"{float(cover):.1f}"
                        except (TypeError, ValueError):
                            cover_s = "—"
                        lines.append(f"  • {name} | {cover_s}")
            else:
                lines.append("موجودی/بحرانی: داده کافی نیست.")
        except Exception:  # noqa: BLE001
            logger.debug("assistant context: critical failed", exc_info=True)
            lines.append("موجودی/بحرانی: محاسبه ممکن نشد.")

        # Monthly summary totals by group (short)
        if uid:
            try:
                from excel.monthly_summary import aggregate_monthly_detail, load_monthly_detail

                latest = db.get_latest_extracted(uid, "monthly_consumption")
                path = None
                if latest:
                    for key in ("raw_path", "clean_path"):
                        p = latest.get(key)
                        if p:
                            from pathlib import Path

                            if Path(str(p)).exists():
                                path = Path(str(p))
                                break
                if path is not None:
                    detail = load_monthly_detail(path)
                    data = aggregate_monthly_detail(detail)
                    items = getattr(data, "items", None)
                    group_order = list(getattr(data, "group_order", []) or [])
                    if items is not None and not items.empty and "شرح" in items.columns:
                        lines.append("جمع مصرف ماهیانه بر اساس گروه (شرح) — نمونه:")
                        shown = 0
                        for desc in group_order:
                            sub = items[items["شرح"] == desc]
                            if sub.empty:
                                continue
                            qty = float(sub["مقدار"].sum()) if "مقدار" in sub.columns else 0.0
                            lines.append(f"  • {str(desc)[:50]}: {qty:g}")
                            shown += 1
                            if shown >= CONTEXT_GROUP_LINES:
                                break
                    else:
                        lines.append("خلاصه مصرف ماهیانه: ردیف گروهی خالی است.")
                else:
                    lines.append("خلاصه مصرف ماهیانه: فایل مصرف ماهیانه یافت نشد.")
            except Exception:  # noqa: BLE001
                logger.debug("assistant context: monthly failed", exc_info=True)
                lines.append("خلاصه مصرف ماهیانه: محاسبه ممکن نشد.")
    except Exception:  # noqa: BLE001
        logger.exception("build_report_context failed")
        lines.append("خطا در ساخت زمینه؛ فقط پرسش‌های کلی دربارهٔ نوع گزارش‌ها ممکن است.")

    return truncate_context("\n".join(lines))


def chat(user_text: str, context: str, *, base_url: str | None = None, model: str | None = None) -> str:
    """Call local Ollama /api/chat. Returns Persian reply or friendly down message.

    Never raises to callers for transport/model errors.
    """
    text = (user_text or "").strip()
    if not text:
        return "لطفاً پرسش خود را دربارهٔ گزارش‌ها بنویسید."
    if is_likely_off_scope(text):
        return refuse_off_scope_fa()

    url_base = (base_url or OLLAMA_BASE_URL).rstrip("/")
    model_name = model or OLLAMA_MODEL
    payload = {
        "model": model_name,
        "messages": build_chat_messages(text, context),
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 512,
        },
    }
    try:
        with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
            resp = client.post(f"{url_base}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        message = (data or {}).get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            # Some servers return top-level response
            content = str((data or {}).get("response") or "").strip()
        if not content:
            logger.warning("Ollama empty response model=%s", model_name)
            return OLLAMA_DOWN_FA
        return content
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ollama chat failed: %s", exc)
        return OLLAMA_DOWN_FA
