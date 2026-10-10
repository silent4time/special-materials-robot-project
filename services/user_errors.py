"""Simple user-facing errors with a tracking code (phase 3 item 19d).

Users see a short Persian line + «کد پیگیری»; the full traceback goes only to the
log, tagged with the same code so the admin can find it (`rg E-1A2B3C data/bot.log`).
Validation errors written for users (Persian ValueError / ExcelValidationError)
keep their own text — those are instructions, not crashes.
"""
from __future__ import annotations

import logging
import re
import secrets

logger = logging.getLogger("user_errors")
_FA = re.compile(r"[\u0600-\u06FF]")


def new_code() -> str:
    return "E-" + secrets.token_hex(3).upper()


def is_user_message(exc: BaseException) -> bool:
    try:
        from excel.processor import ExcelValidationError
    except Exception:  # noqa: BLE001
        ExcelValidationError = ()  # type: ignore[assignment]
    if isinstance(exc, ExcelValidationError):
        return True
    return isinstance(exc, (ValueError, KeyError, PermissionError)) and bool(_FA.search(str(exc)))


def error_fa(context: str, exc: BaseException | None = None, *, log: logging.Logger | None = None) -> str:
    """Persian line for the user; logs the exception with a tracking code."""
    if exc is not None and is_user_message(exc):
        text = str(exc).strip().strip("'\"")
        return f"{context}: {text}"
    code = new_code()
    (log or logger).error("[%s] %s", code, context, exc_info=exc if exc is not None else None)
    return (
        f"⚠️ {context}.\n"
        f"کد پیگیری: {code}\n"
        "لطفاً دوباره تلاش کنید؛ اگر تکرار شد این کد را به مدیر بدهید."
    )


EXPIRED_FA = (
    "⏱ عملیات نیمه‌تمام قبلی شما به‌خاطر راه‌اندازی مجدد ربات منقضی شد و چیزی از آن ذخیره نشد.\n"
    "لطفاً از منوی زیر دوباره شروع کنید."
)
