"""Concise per-menu help (❓ راهنما), filtered by the user's permissions."""
from __future__ import annotations

from bot import keyboards as kb
from services import permissions as perm

_SECTIONS: list[tuple[str, str]] = [
    (
        perm.SITE_STOCK,
        f"{kb.BTN_SITE_STOCK}\n"
        "• بخش اسلب / بلوم / بیلت را بزنید؛ لیست اقلام هر بخش از منبع اصلی ساخته می‌شود.\n"
        "• مقدار هر قلم را بفرستید یا «⏭ رد کردن» بزنید؛ در پایان «تأیید و ثبت».",
    ),
    (
        perm.TUNDISH_REPORT,
        f"{kb.BTN_TR_MENU}\n"
        "• بخش را انتخاب کنید؛ ورود مرحله‌ای یا «📋 ورود سریع از متن».\n"
        "• پیش از ثبت خلاصه نمایش داده می‌شود و قابل اصلاح است.",
    ),
    (
        perm.MATERIAL_REQUEST,
        f"{kb.BTN_MATERIAL_REQUEST}\n"
        "• تعداد روز پوشش را بدهید (پیش‌فرض ۱ روز)؛ نرخ مصرف = میانگین روزانهٔ ۳ ماه کامل گذشته.\n"
        f"• «{kb.BTN_MR_DRAFT}» فقط پیش‌نویس می‌دهد؛ «✅ تأیید همه» از منبع اصلی کسر می‌کند.",
    ),
    (
        perm.WAREHOUSE_RETURN,
        f"{kb.BTN_WAREHOUSE_RETURN}\n• پیشنهاد مواد مازاد سایت؛ پس از تأیید به منبع اصلی افزوده می‌شود.",
    ),
    (
        perm.REPORTS,
        f"{kb.BTN_ANALYTICS}\n"
        "• همهٔ گزارش‌ها PDF (A4، سربرگ) + اکسل هستند؛ گزارش‌های زمانی ابتدا بازهٔ ماه را می‌پرسند (پیش‌فرض «ماه جاری»).\n"
        f"• «{kb.BTN_REMAINING}»: روزهای پوشش موجودی سایت با نرخ مصرف بازه.\n"
        f"• «{kb.BTN_CRITICAL_ITEMS}»: نیاز ۳/۶ ماه بر مبنای ۳ ماه کامل گذشته.\n"
        f"• «{kb.BTN_N_TUNDISH}»: بخش + تعداد تاندیش + با/بدون نوسازی (مثال: «اسلب ۴»).",
    ),
    (
        perm.MAIN_GOAL,
        f"{kb.BTN_MAIN_GOAL}\n"
        f"• «{kb.BTN_MG_INPUTS}»: عکس/اکسل آمار تولید، اکسل مصرف تاندیش هر بخش، یا «{kb.BTN_MG_BULK}».\n"
        f"• «{kb.BTN_MG_HISTORY}»: ماه‌های ذخیره‌شده و بخش‌های ناقص؛ سناریو ۱ و ۲ از همین سابقه.",
    ),
    (
        perm.FILE_INPUTS,
        f"{kb.BTN_UPLOAD_MENU}\n"
        f"• «{kb.BTN_WAREHOUSE_STOCK}»: موجودی شناسه‌های موجود به‌روز می‌شود؛ شناسهٔ جدید فقط با کد ۴ رقمی موجود (نه 1800).\n"
        f"• «{kb.BTN_MONTHLY}»: فایل مصرف ماهیانه.\n"
        f"• «{kb.BTN_MAIN_SOURCE_FILE}»: جایگزینی کامل، دانلود اکسل، افزودن/ویرایش رکورد و کدهای دسته.\n"
        f"• کنار هر ورودی فایل دکمهٔ «{kb.BTN_FILE_GUIDE}» هست.",
    ),
    (
        perm.SETTINGS,
        f"{kb.BTN_BOT_SETTINGS} (مالک/مدیر)\n"
        "• کاربران، فعالیت کاربران، دسترسی نقش‌ها، اقلام فرم گزارش تاندیش، یادآورها، "
        "گروه گزارش موجودی (در گروه: /set_stock_group)، ظاهر (دعوت/خوشامد/لوگو/سربرگ).",
    ),
]

_FOOTER = (
    f"ناوبری: «{kb.BTN_BACK}» یک مرحله به عقب، «{kb.BTN_HOME}» و /reset همهٔ کارهای نیمه‌تمام را پاک می‌کنند؛ "
    f"در مراحل چندمرحله‌ای «{kb.BTN_CANCEL}» بزنید."
)


def help_text_for(user: dict | None) -> str:
    lines = ["❓ راهنمای ربات مواد نسوز تاندیش"]
    shown = [body for feat, body in _SECTIONS if perm.can(user, feat)]
    if not shown:
        lines.append("شما در سیستم ثبت نشده‌اید؛ از مدیر لینک دعوت بگیرید.")
        return "\n\n".join(lines)
    lines.extend(shown)
    if (user or {}).get("role") == "technician":
        lines.append("نقش شما (تکنسین): ثبت موجودی روزانه سایت و گزارش تاندیش.")
    lines.append(_FOOTER)
    return "\n\n".join(lines)


HELP_TEXT = help_text_for({"role": "owner", "active": 1})
