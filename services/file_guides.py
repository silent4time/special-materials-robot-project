"""«❓ راهنمای تهیهٔ فایل» — ONE editable place for every file/photo input guide.

Bot: the «❓ راهنمای تهیهٔ فایل» button next to each file/photo input.
Web: the same text is shown under each upload form (``guide_html``).

Fill ``SYSTEM_STEPS`` when the export path in the plant system is known; until then
the placeholder ``PLACEHOLDER_FA`` is shown + the required file shape (from code).
Not used for «📦 منبع اصلی» (that file is maintained inside the bot itself).
"""
from __future__ import annotations

from html import escape

PLACEHOLDER_FA = "راهنمای مسیر سیستم به‌زودی اضافه می‌شود."

# kind → title
TITLES: dict[str, str] = {
    "product_inventory": "📥 به‌روزرسانی موجودی انبار (Excel)",
    "monthly_consumption": "📥 مصرف ماهیانه (Excel)",
    "mg_production_photo": "📸 عکس آمار تولید (هدف اصلی)",
    "mg_production_xlsx": "📤 اکسل آمار تولید (هدف اصلی)",
    "mg_consumption": "📤 اکسل مصرف تاندیش بیلت/بلوم/اسلب (هدف اصلی)",
    "mg_sequence": "📤 اکسل لاگ سکوئنس تاندیش",
    "mg_bulk": "📦 آپلود گروهی (هدف اصلی)",
}

# kind → step-by-step export instructions in the plant system (None → placeholder).
SYSTEM_STEPS: dict[str, list[str] | None] = {
    "mg_production_photo": [
        "سامانهٔ آمار تولید otsteel.ksc.ir/productionstatistics را باز کنید.",
        "تب «ریخته‌گری» را انتخاب کنید (تب «کوره» پذیرفته نمی‌شود).",
        "تاریخ شروع و پایان را روی ماه مورد نظر تنظیم کنید.",
        "دکمهٔ «جستجو» را بزنید.",
        "از جدول نتیجه (همراه با فیلتر تاریخ) تصویر صفحه (اسکرین‌شات) بگیرید و به‌صورت عکس بفرستید.",
    ],
    "product_inventory": None,
    "monthly_consumption": None,
    "mg_production_xlsx": None,
    "mg_consumption": None,
    "mg_sequence": None,
    "mg_bulk": None,
}

# kind → required file shape (kept in sync with the parsers in code)
SHAPES: dict[str, list[str]] = {
    "product_inventory": [
        "فرمت: .xlsx — شیت «ریز اطلاعات» (یا اولین شیت).",
        "ستون‌های لازم: کد کالا (شناسه)، محصول/شرح کالا، مقدار، واحد؛ کد دسته‌بندی از ۴ رقم اول شناسه.",
        "فقط شناسه‌های موجود به‌روز می‌شوند؛ شناسهٔ جدید فقط اگر کد ۴رقمی‌اش در منبع اصلی باشد و ۱۸۰۰ نباشد.",
    ],
    "monthly_consumption": [
        "فرمت: .xlsx",
        "ستون‌های لازم: ماده (نام ماده)، ماه، مقدار، واحد؛ اختیاری: نوع تاندیش، کد کالا، کد دسته‌بندی، وضعیت، توضیحات.",
    ],
    "mg_production_photo": [
        "عکس (Photo یا فایل تصویری) از تب «ریخته‌گری»؛ نگاشت CCM: ۱و۲ = اسلب، ۳ = بلوم، ۴و۵ = بیلت.",
        "پس از OCR اعداد قابل اصلاح دستی‌اند («✏️ اصلاح دستی تولید»).",
    ],
    "mg_production_xlsx": [
        "فرمت: .xlsx — سطرهای CCM/PRODUCT با تناژ هر ماشین؛ ماه شمسی در نام فایل/شیت/سربرگ (مثل «شهریور ۱۴۰۵»).",
    ],
    "mg_consumption": [
        "فرمت: .xlsx — جدول مصرف مواد تاندیش بخش (ردیف‌های تاندیش + ستون‌های مواد).",
        "یا لاگ سکوئنس تاندیش (ستون‌های ماشین، تاندیش، تعداد ذوب، مدت، شرود/نازل/تیوب).",
        "ماه شمسی در نام فایل بیاید، مثلاً «مصرف تاندیش بیلت شهریور ۱۴۰۵.xlsx».",
    ],
    "mg_sequence": [
        "فرمت: .xlsx — ستون‌های ماشین، تاندیش، تاندیشکار، تعداد ذوب، مدت سکوئنس، شرود، نازل، تیوب.",
    ],
    "mg_bulk": [
        "هر تعداد فایل و هر ترتیب: عکس آمار تولید (تب ریخته‌گری) یا اکسل آمار تولید، "
        "اکسل مصرف تاندیش بیلت/بلوم/اسلب، اکسل لاگ سکوئنس تاندیش.",
        "نوع فایل و ماه خودکار تشخیص داده می‌شود؛ ماه شمسی را در نام فایل بنویسید.",
    ],
}
SHAPES["mg_bulk"] = SHAPES["mg_bulk"] + ["—"] + SHAPES["mg_production_xlsx"] + SHAPES["mg_consumption"][:1]

GENERAL_FA = (
    "راهنمای تهیهٔ فایل برای ورودی‌های فایل/عکس:\n"
    + "\n".join(f"• {t}" for t in TITLES.values())
    + "\n\nهنگام انتظار برای هر فایل، همین دکمه راهنمای همان فایل را نشان می‌دهد."
)


def kinds() -> list[str]:
    return list(TITLES)


def guide_text(kind: str | None) -> str:
    """Plain-text guide for the bot (general list when kind unknown)."""
    if not kind or kind not in TITLES:
        return GENERAL_FA
    steps = SYSTEM_STEPS.get(kind)
    lines = [f"❓ راهنمای تهیهٔ فایل — {TITLES[kind]}", ""]
    if steps:
        lines.append("مسیر در سیستم:")
        lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    else:
        lines.append(f"مسیر در سیستم: {PLACEHOLDER_FA}")
    lines.append("")
    lines.append("شکل فایل لازم:")
    lines += [f"• {s}" for s in SHAPES.get(kind, []) if s != "—"]
    return "\n".join(lines)


def guide_html(kind: str) -> str:
    """Small HTML block for web upload forms."""
    steps = SYSTEM_STEPS.get(kind)
    parts = [f"<details class=\"file-guide\"><summary>❓ راهنمای تهیهٔ فایل — {escape(TITLES.get(kind, kind))}</summary>"]
    if steps:
        parts.append("<ol>" + "".join(f"<li>{escape(s)}</li>" for s in steps) + "</ol>")
    else:
        parts.append(f"<p class=\"muted\">مسیر در سیستم: {escape(PLACEHOLDER_FA)}</p>")
    parts.append("<ul>" + "".join(f"<li>{escape(s)}</li>" for s in SHAPES.get(kind, []) if s != "—") + "</ul>")
    parts.append("</details>")
    return "".join(parts)
