"""Default invite/welcome templates and formatting helpers for bot settings."""
from __future__ import annotations

from auth.rbac import role_label

# Placeholders documented in settings UI:
#   {role_fa}  — Persian role label
#   {role}     — alias of {role_fa}
#   {name}     — display name (welcome only)

DEFAULT_INVITE_TEXT = (
    "سلام؛\n"
    "\n"
    "شما برای استفاده از سامانهٔ مدیریت مواد ویژه تاندیش دعوت شده‌اید.\n"
    "\n"
    "نقش تعریف‌شده برای شما: {role_fa}\n"
    "\n"
    "لطفاً برای فعال‌سازی حساب و شروع کار، دکمهٔ زیر را بزنید. "
    "این لینک شخصی است و تا ۷ روز معتبر می‌باشد.\n"
    "\n"
    "با احترام\n"
    "مدیریت سیستم مواد تاندیش"
)

DEFAULT_WELCOME_TEXT = (
    "سلام{name_suffix}! به بازوی «گزارش مواد / تاندیش» خوش آمدید.\n"
    "\n"
    "نقش شما: {role_fa}\n"
    "از منو: موجودی انبار / مصرف ماهیانه / موجودی روزانه سایت را انتخاب کنید.\n"
    "موجودی روزانه سایت تعاملی است (سه گروه اسلب/بلوم/بیلت).\n"
    "از «تنظیمات اقلام سایت / تخصیص به گروه» اقلام را به گروه تخصیص دهید.\n"
    "از «گزارش‌ها / تحلیل تاندیش» برای تحلیل‌ها استفاده کنید."
)

TECHNICIAN_WELCOME_TIP = (
    "برای نقش تکنسین فقط ورود «موجودی روزانه سایت» فعال است.\n"
    "از منو یکی از گروه‌های اسلب / بلوم / بیلت را انتخاب و مقادیر را یکی‌یکی بفرستید."
)

PLACEHOLDER_HINT_INVITE = (
    "متن دعوت‌نامه (قالب).\n"
    "جای‌نگهدارها:\n"
    "• {role_fa} یا {role} — برچسب فارسی نقش\n"
    "دکمه «ورود به ربات» جداگانه به‌صورت لینک اینلاین ارسال می‌شود؛ "
    "نیازی به گذاشتن URL در متن نیست."
)

PLACEHOLDER_HINT_WELCOME = (
    "متن خوشامدگویی (قالب).\n"
    "جای‌نگهدارها:\n"
    "• {role_fa} یا {role} — برچسب فارسی نقش\n"
    "• {name} — نام نمایشی کاربر\n"
    "اگر قالب خالی باشد، متن پیش‌فرض فعلی استفاده می‌شود."
)


def _safe_format(template: str, **kwargs: str) -> str:
    """Format with str.format_map but leave unknown braces alone when possible."""
    class _Map(dict):
        def __missing__(self, key: str) -> str:  # type: ignore[override]
            return "{" + key + "}"

    try:
        return template.format_map(_Map(**kwargs))
    except (ValueError, IndexError):
        # malformed braces — return raw
        return template


def format_invite_text(template: str | None, role: str) -> str:
    body = (template or "").strip() or DEFAULT_INVITE_TEXT
    role_fa = role_label(role)
    return _safe_format(body, role_fa=role_fa, role=role_fa).strip()


def format_welcome_text(template: str | None, user: dict) -> str:
    role = user.get("role") or ""
    role_fa = role_label(role)
    name = str(user.get("display_name") or "").strip()
    name_suffix = f" {name}" if name else ""

    body = (template or "").strip()
    if not body:
        # Legacy default path (matches previous _welcome_text)
        if role == "technician":
            return (
                "سلام! به بازوی «گزارش مواد / تاندیش» خوش آمدید.\n\n"
                f"نقش شما: {role_fa}\n"
                + TECHNICIAN_WELCOME_TIP
            )
        return _safe_format(
            DEFAULT_WELCOME_TEXT,
            role_fa=role_fa,
            role=role_fa,
            name=name,
            name_suffix=name_suffix,
            scope_line="",
        ).strip()

    text = _safe_format(
        body,
        role_fa=role_fa,
        role=role_fa,
        name=name,
        name_suffix=name_suffix,
        scope_line="",
    ).strip()
    if not text:
        return format_welcome_text(None, user)
    if role == "technician" and "موجودی روزانه سایت" not in text:
        text = text + "\n\n" + TECHNICIAN_WELCOME_TIP
    return text
