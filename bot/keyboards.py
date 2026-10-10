"""Persian reply keyboards (phase-1 cleanup 1405-07-18).

Menu tree (role-filtered through ``services.permissions``)::

    منوی اصلی
    ├─ 📥 موجودی روزانه سایت      ├─ 🧾 گزارش تاندیش
    ├─ 🛒 درخواست مواد            ├─ ↩️ برگشت به انبار
    ├─ 📊 گزارش‌ها                ├─ 🎯 هدف اصلی
    ├─ 📤 ورود فایل‌ها → 📦 منبع اصلی
    └─ ⚙️ تنظیمات (مالک/مدیر)     └─ ❓ راهنما

Every keyboard carries a private ``_menu`` key (stripped before sending) so the
bot knows where the user is; the uniform last row «⬅️ بازگشت» (one level up via
``MENU_PARENT``) + «🏠 منوی اصلی» works everywhere. Multi-step flows use
«✖️ انصراف». Old button labels stay usable through ``LEGACY_ALIASES`` (renamed)
and ``REMOVED_HINTS`` (removed features → short hint).
"""
from __future__ import annotations

from config import FILE_TYPES, SITE_STOCK_GROUPS, TUNDISH_TYPES
from bot.bale_api import BaleClient
from services import permissions as perm

# ---------------------------------------------------------------------------
# Uniform navigation
# ---------------------------------------------------------------------------
BTN_BACK = "⬅️ بازگشت"
BTN_HOME = "🏠 منوی اصلی"
BTN_CANCEL = "✖️ انصراف"
NAV_ROW = [BTN_BACK, BTN_HOME]

# menu key → parent menu key (``None`` = top). Flow keyboards use key "flow" and
# return to the menu that opened them.
MENU_PARENT: dict[str, str | None] = {
    "main": None,
    "site_stock": "main",
    "tundish_report": "main",
    "reports": "main",
    "critical": "reports",
    "inbound_history": "reports",
    "main_goal": "main",
    "mg_inputs": "main_goal",
    "mg_history": "main_goal",
    "files": "main",
    "main_source": "files",
    "settings": "main",
    "users": "settings",
    "tr_settings": "settings",
    "tr_section_settings": "tr_settings",
    "reminders": "settings",
    "stock_group": "settings",
    "appearance": "settings",
    "appearance_item": "appearance",
    "role_perms": "settings",
    "letterhead": "appearance",
    "material_request": "main",
    "warehouse_return": "main",
}


def _kb(rows: list[list[str]], menu: str | None = None, *, nav: bool = True) -> dict:
    """Reply keyboard + optional uniform nav row + private ``_menu`` marker."""
    rows = [list(r) for r in rows if r]
    if nav:
        rows.append(list(NAV_ROW))
    markup = BaleClient.reply_keyboard(rows)
    if menu:
        markup["_menu"] = menu
    return markup


def _pairs(buttons: list[str]) -> list[list[str]]:
    """Two-column layout."""
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def keyboard_texts(markup: dict | None) -> list[str]:
    """All button texts of a reply keyboard (for tests / dispatch checks)."""
    out: list[str] = []
    for row in (markup or {}).get("keyboard") or []:
        for b in row:
            out.append(b.get("text") if isinstance(b, dict) else str(b))
    return out


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
BTN_TANK = FILE_TYPES["tank_consumption"]["button"]  # «📥 موجودی روزانه سایت»
BTN_SITE_STOCK = BTN_TANK
BTN_TR_MENU = "🧾 گزارش تاندیش"
BTN_MATERIAL_REQUEST = "🛒 درخواست مواد"
BTN_WAREHOUSE_RETURN = "↩️ برگشت به انبار"
BTN_ANALYTICS = "📊 گزارش‌ها"
BTN_MAIN_GOAL = "🎯 هدف اصلی"
BTN_MG_MENU = BTN_MAIN_GOAL
BTN_UPLOAD_MENU = "📤 ورود فایل‌ها"
BTN_BOT_SETTINGS = "⚙️ تنظیمات"
BTN_HELP = "❓ راهنما"

# ---------------------------------------------------------------------------
# 📤 ورود فایل‌ها + 📦 منبع اصلی
# ---------------------------------------------------------------------------
BTN_WAREHOUSE_STOCK = "📥 به‌روزرسانی موجودی انبار"  # stock update (product_inventory)
BTN_MONTHLY = "📥 مصرف ماهیانه"
BTN_MAIN_SOURCE_FILE = "📦 منبع اصلی"  # submenu opener
BTN_INV_MENU = BTN_MAIN_SOURCE_FILE
BTN_FULL_REPLACE = "📦 جایگزینی کامل منبع اصلی"
BTN_FULL_REPLACE_CONFIRM = "✅ تأیید جایگزینی کامل"
BTN_INV_UPLOAD = BTN_FULL_REPLACE
BTN_INV = BTN_FULL_REPLACE
BTN_INV_DOWNLOAD = "📄 دانلود اکسل منبع اصلی"
BTN_INV_ADD_RECORD = "➕ افزودن رکورد"
BTN_INV_EDIT_RECORD = "✏️ ویرایش رکورد"
BTN_INV_ADD_CATEGORY = "➕ افزودن کد دسته"
BTN_INV_LIST_CATEGORIES = "📋 کدهای دسته"
BTN_CANCEL_PENDING = BTN_CANCEL
BTN_FILE_GUIDE = "❓ راهنمای تهیهٔ فایل"

# ---------------------------------------------------------------------------
# 📊 گزارش‌ها
# ---------------------------------------------------------------------------
BTN_DAILY = "📈 مصرف روزانه مواد"
BTN_COMPREHENSIVE = "📊 گزارش جامع"
BTN_GENERATE = BTN_COMPREHENSIVE
BTN_ANALYTICS_PDF = BTN_COMPREHENSIVE
BTN_PERIOD = "📅 گزارش مصرف بازه‌ای"
BTN_REMAINING = "⚠️ پوشش کوتاه‌مدت موجودی سایت"
BTN_CRITICAL_ITEMS = "🚨 اقلام بحرانی (نیاز ۳/۶ ماه)"
BTN_N_TUNDISH = "🧮 نیاز مواد برای N تاندیش"
BTN_SURPLUS = "📦 گزارش مواد مازاد"
BTN_INBOUND = "📄 گزارش اقلام ورودی به انبار"
BTN_MONTHLY_SUMMARY = "📄 دریافت خلاصه مصرف ماهیانه"
BTN_CRITICAL_COUNTS = "📝 ثبت تعداد تاندیش ماهانه"
BTN_CRITICAL_REPORT = "📄 تولید گزارش اقلام بحرانی"

# section step (مصرف روزانه / گزارش جامع / مصرف بازه‌ای) + N-tundish
BTN_SEC_ALL = "همه بخش‌ها"
BTN_SEC_SLAB = "اسلب"
BTN_SEC_BLOOM = "بلوم"
BTN_SEC_BILLET = "بیلت"
SECTION_STEP_BUTTONS = {
    BTN_SEC_ALL: None,
    BTN_SEC_SLAB: "slab",
    BTN_SEC_BLOOM: "bloom",
    BTN_SEC_BILLET: "billet",
}
BTN_NT_WITH = "🔧 با نوسازی"
BTN_NT_WITHOUT = "🩹 بدون نوسازی"

# Month/year range presets (primary UX for time-based reports; «ماه جاری» first)
BTN_MY_CURRENT = "ماه جاری"
BTN_MY_3 = "۳ ماه اخیر"
BTN_MY_YTD = "از ابتدای سال"
BTN_MY_CUSTOM = "بازه سفارشی ماه"
BTN_MY_DAY_ADV = "بازه روزانه (پیشرفته)"
BTN_MY_TYPED = "ورود دستی بازه"
BTN_RANGE_TODAY = "امروز"
BTN_RANGE_7 = "۷ روز"
BTN_RANGE_30 = "۳۰ روز"
BTN_RANGE_CUSTOM = "بازه سفارشی"

# ---------------------------------------------------------------------------
# 🎯 هدف اصلی
# ---------------------------------------------------------------------------
BTN_MG_INPUTS = "📥 ثبت ورودی ماه"
BTN_MG_REPORT_REQ = "📊 درخواست گزارش از سابقه"
BTN_MG_HISTORY = "🗂 ماه‌های ذخیره‌شده"
BTN_MG_DELETE = "🗑 حذف یک ماه از سابقه"
BTN_MG_SCN_TARGET = "🎯 سناریو ۱: تناژ هدف"
BTN_MG_SCN_FORECAST = "🔮 سناریو ۲: پیش‌بینی ماه‌های آینده"
BTN_MG_RECENT = "📜 آخرین گزارش‌های هدف اصلی"
BTN_MG_BULK = "📦 آپلود گروهی"
BTN_MG_BULK_DONE = "✅ پایان آپلود گروهی"
BTN_MG_BULK_STOP = "✖️ توقف آپلود گروهی (ماه‌های ذخیره‌شده می‌مانند)"
BTN_MG_INPUT_PROD = "📸 آپلود عکس آمار تولید"
BTN_MG_INPUT_PROD_XLSX = "📤 آپلود اکسل آمار تولید"
BTN_MG_INPUT_BILLET = "📤 اکسل مصرف تاندیش بیلت"
BTN_MG_INPUT_BLOOM = "📤 اکسل مصرف تاندیش بلوم"
BTN_MG_INPUT_SLAB = "📤 اکسل مصرف تاندیش اسلب"
BTN_MG_INPUT_CORRECT = "✏️ اصلاح دستی تولید"
BTN_MG_CANCEL = BTN_CANCEL
BTN_MG_BACK = BTN_BACK  # kept name; nav handles «one level up»
BTN_MG_ADD_SECTION = "➕ افزودن بخش دیگر"
BTN_MG_COMPUTE = "✅ محاسبه سناریو"
BTN_MG_SEC_BILLET = "بیلت"
BTN_MG_SEC_BLOOM = "بلوم"
BTN_MG_SEC_SLAB = "اسلب"
BTN_MG_SEC_TOTAL = "کل (همه بخش‌ها)"
MG_SECTION_BUTTONS = {
    BTN_MG_SEC_BILLET: "billet",
    BTN_MG_SEC_BLOOM: "bloom",
    BTN_MG_SEC_SLAB: "slab",
    BTN_MG_SEC_TOTAL: "total",
}
BTN_MG_P1 = "۱ ماه"
BTN_MG_P3 = "۳ ماه"
BTN_MG_P6 = "۶ ماه"
BTN_MG_P12 = "۱۲ ماه"
BTN_MG_SKIP_TARGET = "⏭ بدون تناژ هدف"
BTN_MG_RANGE_3 = "۳ ماهه"
BTN_MG_RANGE_6 = "۶ ماهه"
BTN_MG_RANGE_12 = "یکساله"
BTN_MG_RANGE_CUSTOM = "بازه ورودی کاربر"
BTN_MG_CONFIRM_PARTIAL = "✅ ادامه با دادهٔ موجود"

# ---------------------------------------------------------------------------
# ⚙️ تنظیمات (owner/manager)
# ---------------------------------------------------------------------------
BTN_USERS = "👥 کاربران"
BTN_USER_ACTIVITY = "📋 فعالیت کاربران"
BTN_ROLE_PERMS = "🔐 دسترسی نقش‌ها"
BTN_TR_SETTINGS = "🧾 اقلام فرم گزارش تاندیش"
BTN_SET_REMINDERS = "🔔 یادآورها"
BTN_SET_STOCK_GROUP = "📣 گروه گزارش موجودی"
BTN_APPEARANCE = "🎨 ظاهر"
BTN_SET_INVITE = "📝 متن دعوت‌نامه کاربران"
BTN_SET_WELCOME = "👋 پیام خوشامدگویی"
BTN_SET_LOGO = "🖼 لوگوی ربات"
BTN_SET_LETTERHEAD = "📄 سربرگ PDF"
BTN_SETTINGS_UPLOAD_LETTERHEAD = "📤 آپلود سربرگ PDF"
BTN_SETTINGS_CLEAR_LETTERHEAD = "🗑 حذف سربرگ"
BTN_SETTINGS_CLEAR_STOCK_GROUP = "🗑 حذف گروه گزارش موجودی"
BTN_SETTINGS_VIEW = "👁 مشاهده"
BTN_SETTINGS_EDIT_TEXT = "✏️ ویرایش متن"
BTN_SETTINGS_SET_IMAGE = "🖼 تنظیم تصویر"
BTN_SETTINGS_CLEAR_IMAGE = "🗑 حذف تصویر"
BTN_BACK_BOT_SETTINGS = BTN_BACK

BTN_USERS_ADD = "➕ اضافه کردن کاربر"
BTN_USERS_EDIT = "✏️ اصلاح نقش کاربر"
BTN_USERS_DELETE = "🗑 حذف کاربر"
BTN_USERS_LIST = "📋 لیست کاربران"
BTN_BACK_USERS = BTN_BACK
BTN_INVITE_CONFIRM = "✅ تأیید ساخت لینک"
BTN_INVITE_CANCEL = BTN_CANCEL
BTN_INVITE_ENTER = "ورود به ربات"

BTN_ROLE_OWNER = "مالک"
BTN_ROLE_MANAGER = "مدیر"
BTN_ROLE_OFFICER = "کاردان مسئول"
BTN_ROLE_TECH = "تکنسین"
BTN_ROLE_SHIFT = "👷 مسئول شیفت"
ROLE_BUTTON_TO_KEY = {
    BTN_ROLE_OWNER: "owner",
    BTN_ROLE_MANAGER: "manager",
    BTN_ROLE_OFFICER: "responsible_officer",
    BTN_ROLE_TECH: "technician",
    BTN_ROLE_SHIFT: "shift_supervisor",
    "مسئول شیفت": "shift_supervisor",
}

# یادآور گزارش‌های الزامی
BTN_RM_STATUS = "👁 وضعیت یادآور"
BTN_RM_ENABLE = "✅ فعال‌سازی یادآور"
BTN_RM_DISABLE = "⏸ غیرفعال‌سازی یادآور"
BTN_RM_ROLES = "👥 نقش‌های دریافت‌کننده"
BTN_RM_USERS = "👤 کاربران دریافت‌کننده"
BTN_RM_SCHEDULE = "📅 زمان‌بندی یادآور"
BTN_RM_SEND_NOW = "📨 ارسال یادآور همین حالا"
BTN_RM_BACK = "⬅️ بازگشت به یادآور"
RM_CHECK_ON = "✅ "
RM_CHECK_OFF = "⬜ "

# ---------------------------------------------------------------------------
# موجودی روزانه سایت
# ---------------------------------------------------------------------------
BTN_SITE_SLAB = SITE_STOCK_GROUPS["slab"]
BTN_SITE_BLOOM = SITE_STOCK_GROUPS["bloom"]
BTN_SITE_BILLET = SITE_STOCK_GROUPS["billet"]
BTN_SITE_SKIP = "⏭ رد کردن این قلم"
BTN_SITE_CANCEL = BTN_CANCEL  # inline (callback ss|x) + reply; routed by on_cancel
BTN_SITE_CONFIRM = "تأیید و ثبت"
BTN_BACK_SITE = "⬅️ بازگشت به گروه‌های سایت"
SITE_GROUP_BUTTONS = {
    BTN_SITE_SLAB: "slab",
    BTN_SITE_BLOOM: "bloom",
    BTN_SITE_BILLET: "billet",
}

# ---------------------------------------------------------------------------
# درخواست مواد / برگشت به انبار
# ---------------------------------------------------------------------------
BTN_MR_CONFIRM_ALL = "✅ تأیید همه"
BTN_MR_EDIT = "✏️ اصلاح"
BTN_MR_DRAFT = "📄 پیش‌نویس PDF/اکسل"
BTN_MR_CANCEL = BTN_CANCEL
BTN_MR_BACK_REVIEW = "⬅️ بازگشت به بررسی"
BTN_MR_HISTORY = "📜 تاریخچه درخواست‌ها"
BTN_MR_DAYS_DEFAULT = "۱ روز (پیش‌فرض)"

# ---------------------------------------------------------------------------
# Legacy names kept so older imports keep working (values = new labels)
# ---------------------------------------------------------------------------
BTN_BACK_MAIN = BTN_HOME
BTN_BACK_PREV = BTN_BACK
BTN_BACK_ANALYTICS = BTN_BACK
BTN_BACK_UPLOAD = BTN_BACK
BTN_BACK_CRITICAL = BTN_BACK
BTN_BACK_INV_EDIT = BTN_BACK
BTN_INBOUND_LEGACY = "📥 گزارش ورودی به انبار"
BTN_INV_EDIT = "✏️ ویرایش منبع اصلی"

# Renamed labels → current label (old keyboards still on clients / typed text)
LEGACY_ALIASES: dict[str, str] = {
    # main menu
    "🧾 گزارش تاندیش بعد از ریخته‌گری": BTN_TR_MENU,
    "📊 گزارش‌ها / تحلیل تاندیش": BTN_ANALYTICS,
    "🎯 گزارش هدف اصلی": BTN_MAIN_GOAL,
    "📤 آپلود فایل": BTN_UPLOAD_MENU,
    "⚙️ تنظیمات ربات": BTN_BOT_SETTINGS,
    "🔄 شروع مجدد": BTN_HOME,
    # nav
    "⬅️ بازگشت به منوی اصلی": BTN_HOME,
    "بازگشت به منوی اصلی": BTN_HOME,
    "بازگشت به منو": BTN_HOME,
    "⬅️ بازگشت به منوی قبل": BTN_BACK,
    "⬅️ بازگشت به تحلیل": BTN_ANALYTICS,
    "⬅️ بازگشت به آپلود فایل": BTN_UPLOAD_MENU,
    "⬅️ بازگشت به تنظیمات ربات": BTN_BOT_SETTINGS,
    "⬅️ بازگشت به اقلام بحرانی": BTN_CRITICAL_ITEMS,
    "⬅️ بازگشت به هدف اصلی": BTN_MAIN_GOAL,
    "⬅️ بازگشت به ویرایش منبع اصلی": BTN_MAIN_SOURCE_FILE,
    "⬅️ بازگشت به گزارش تاندیش": BTN_TR_MENU,
    "⬅️ بازگشت به تنظیمات گزارش تاندیش": BTN_TR_SETTINGS,
    # cancels → one «✖️ انصراف»
    "✖️ انصراف از آپلود": BTN_CANCEL,
    "انصراف از آپلود": BTN_CANCEL,
    "✖️ انصراف از گزارش هدف اصلی": BTN_CANCEL,
    "✖️ انصراف از گزارش تاندیش": BTN_CANCEL,
    "انصراف": BTN_CANCEL,
    "✖️ انصراف از ورود موجودی": BTN_CANCEL,
    "راهنما": BTN_HELP,
    # files
    "📥 موجودی انبار": BTN_WAREHOUSE_STOCK,
    "📦 موجودی انبار": BTN_WAREHOUSE_STOCK,
    "📥 موجودی محصولات": BTN_WAREHOUSE_STOCK,
    "📥 مواد مصرفی": BTN_WAREHOUSE_STOCK,
    "📦 مواد مصرفی": BTN_WAREHOUSE_STOCK,
    "مواد مصرفی": BTN_WAREHOUSE_STOCK,
    FILE_TYPES["monthly_consumption"]["button"]: BTN_MONTHLY,  # «📥 مصرف ماهیانه مواد»
    "📥 مصرف ماهانه مواد": BTN_MONTHLY,
    FILE_TYPES["monthly_consumption"]["label_fa"]: BTN_MONTHLY,
    "📦 فایل منبع اصلی": BTN_MAIN_SOURCE_FILE,
    "✏️ ویرایش منبع اصلی": BTN_MAIN_SOURCE_FILE,
    "📥 ورود فایل اکسل منبع اصلی": BTN_FULL_REPLACE,
    FILE_TYPES["product_inventory"]["button"]: BTN_FULL_REPLACE,  # «📥 منبع اصلی»
    FILE_TYPES["product_inventory"]["label_fa"]: BTN_FULL_REPLACE,
    "📥 ورود فایل اکسل": BTN_FULL_REPLACE,
    "📥 دانلود فایل منبع اصلی (اکسل)": BTN_INV_DOWNLOAD,
    "➕ اضافه کردن رکورد": BTN_INV_ADD_RECORD,
    "➕ اضافه کردن کد دسته بندی": BTN_INV_ADD_CATEGORY,
    "📋 لیست کدهای دسته بندی": BTN_INV_LIST_CATEGORIES,
    # reports
    "📊 گزارش کلی مواد": BTN_COMPREHENSIVE,
    "📄 PDF کامل تحلیل": BTN_COMPREHENSIVE,
    "⚠️ موجودی و مواد بحرانی": BTN_REMAINING,
    "🚨 اقلام بحرانی": BTN_CRITICAL_ITEMS,
    "📥 گزارش اقلام ورودی به انبار": BTN_INBOUND,
    BTN_INBOUND_LEGACY: BTN_INBOUND,
    "📥 دریافت خلاصه مصرف ماهیانه": BTN_MONTHLY_SUMMARY,
    "📋 گزارش فعالیت کاربران": BTN_USER_ACTIVITY,
    # main goal
    "📥 ثبت ورودی (عکس تولید / اکسل تاندیش)": BTN_MG_INPUTS,
    "📦 آپلود گروهی چند ماه (تشخیص خودکار)": BTN_MG_BULK,
    "📄 آپلود اکسل آمار تولید": BTN_MG_INPUT_PROD_XLSX,
    # settings
    "⚙️ تنظیمات گزارش تاندیش": BTN_TR_SETTINGS,
    "🔔 یادآور گزارش‌های الزامی": BTN_SET_REMINDERS,
    "📣 گروه گزارش موجودی روزانه": BTN_SET_STOCK_GROUP,
    "📄 آپلود سربرگ PDF": BTN_SETTINGS_UPLOAD_LETTERHEAD,
}

_N_TUNDISH_HINT = (
    f"برای نیاز مواد از «{BTN_ANALYTICS}» ← «{BTN_N_TUNDISH}» "
    f"یا «{BTN_MAIN_GOAL}» ← «{BTN_MG_SCN_FORECAST}» استفاده کنید."
)
_CATALOG_HINT = (
    "این منو حذف شد: اقلام سایت/درخواست مواد پس از هر تغییر منبع اصلی خودکار همگام می‌شوند "
    "و لیست موجودی روزانه هر بخش از منبع اصلی ساخته می‌شود."
)
_FILTER_HINT = (
    "فیلتر نوع تاندیش حذف شد؛ گزارش‌های «مصرف روزانه»، «گزارش جامع» و «مصرف بازه‌ای» "
    "خودشان مرحلهٔ اختیاری «بخش: همه/اسلب/بلوم/بیلت» دارند."
)
# Removed features → hint (button stays answerable for old keyboards)
REMOVED_HINTS: dict[str, str] = {
    "🔮 پیش‌بینی نیاز تاندیش": "«🔮 پیش‌بینی نیاز تاندیش» حذف شد.\n" + _N_TUNDISH_HINT,
    "🛒 پیشنهاد درخواست مواد": (
        "«🛒 پیشنهاد درخواست مواد» حذف شد. در «🛒 درخواست مواد» پس از ساخت لیست، "
        f"دکمهٔ «{BTN_MR_DRAFT}» همان پیش‌نویس را بدون ثبت می‌دهد."
    ),
    "📤 آپلود ۴ فایل یک ماه": (
        f"این دکمه حذف شد. از «{BTN_MAIN_GOAL}» ← «{BTN_MG_INPUTS}» فایل‌ها را تکی "
        f"یا با «{BTN_MG_BULK}» بفرستید."
    ),
    "📤 آپلود چهار فایل هدف اصلی": (
        f"این دکمه حذف شد. از «{BTN_MAIN_GOAL}» ← «{BTN_MG_INPUTS}» فایل‌ها را تکی "
        f"یا با «{BTN_MG_BULK}» بفرستید."
    ),
    "🔎 فیلتر نوع تاندیش": _FILTER_HINT,
    "همه تاندیش‌ها": _FILTER_HINT,
    TUNDISH_TYPES["slab"]: _FILTER_HINT,
    TUNDISH_TYPES["bloom"]: _FILTER_HINT,
    TUNDISH_TYPES["billet"]: _FILTER_HINT,
    "⚙️ تنظیمات اقلام سایت / تخصیص به گروه": _CATALOG_HINT,
    "📋 لیست اقلام و تخصیص‌ها": _CATALOG_HINT,
    "📭 اقلام بدون گروه": _CATALOG_HINT,
    "🔄 همگام‌سازی از منبع اصلی": _CATALOG_HINT,
    "تخصیص به اسلب": _CATALOG_HINT,
    "تخصیص به بلوم": _CATALOG_HINT,
    "تخصیص به بیلت": _CATALOG_HINT,
    "حذف تخصیص": _CATALOG_HINT,
    "⬅️ بازگشت به تنظیمات اقلام": _CATALOG_HINT,
    "🤖 دستیار هوشمند": "دستیار هوشمند از ربات حذف شد.",
    "پایان گفتگو": "دستیار هوشمند از ربات حذف شد.",
    "📋 وضعیت فایل‌ها": "وضعیت فایل‌ها با دستور /status نمایش داده می‌شود.",
}


def canonical(text: str | None) -> str:
    """Map an old/renamed label to the current one (unknown text unchanged)."""
    s = (text or "").strip()
    return LEGACY_ALIASES.get(s, s)


# Reply/nav labels that must NOT be parsed as quantities / codes / record fields
SITE_STOCK_RESERVED_TEXTS = frozenset(
    {
        BTN_SITE_STOCK,
        BTN_SITE_SKIP,
        BTN_SITE_CANCEL,
        BTN_SITE_CONFIRM,
        BTN_BACK_SITE,
        BTN_BACK,
        BTN_HOME,
        BTN_CANCEL,
        *SITE_GROUP_BUTTONS.keys(),
    }
)
PENDING_CANCEL_TEXTS = frozenset({BTN_CANCEL, "✖️ انصراف از آپلود", "انصراف از آپلود"})
PENDING_INPUT_RESERVED_TEXTS = frozenset(
    {
        BTN_CANCEL,
        BTN_HELP,
        BTN_HOME,
        BTN_BACK,
        BTN_FILE_GUIDE,
        *PENDING_CANCEL_TEXTS,
        *LEGACY_ALIASES.keys(),
        "راهنما",
    }
)


def normalize_pending_text(text: str | None) -> str:
    """Strip so prompt-quoted labels (leading space) match keyboard buttons."""
    return (text or "").strip()


def is_pending_cancel_text(text: str | None) -> bool:
    return canonical(normalize_pending_text(text)) == BTN_CANCEL


def is_pending_reserved_text(text: str | None) -> bool:
    t = normalize_pending_text(text)
    return t in PENDING_INPUT_RESERVED_TEXTS or canonical(t) in PENDING_INPUT_RESERVED_TEXTS


def button_to_file_type(text: str) -> str | None:
    """Upload buttons that select a file slot (after ``canonical``).

    «📦 جایگزینی کامل منبع اصلی» is NOT mapped: it goes through a warning +
    confirm step first (handlers.on_full_replace_*).
    """
    t = canonical(text)
    return {
        BTN_WAREHOUSE_STOCK: "product_inventory",
        BTN_MONTHLY: "monthly_consumption",
    }.get(t)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def _role(user) -> str | None:
    if isinstance(user, dict):
        return user.get("role")
    if isinstance(user, str):
        return user
    if user is True:
        return "manager"
    if user is False:
        return "responsible_officer"
    return None


def _u(user) -> dict:
    """Normalise legacy role/bool arguments to a user-like dict for ``perm.can``."""
    if isinstance(user, dict):
        return user
    role = _role(user)
    return {"role": role, "active": 1} if role else {}


def main_menu(user: dict | str | bool | None = None) -> dict:
    """Two-column main menu filtered by permissions (technician: stock + tundish report)."""
    u = _u(user)
    rows: list[list[str]] = []
    for pair in (
        [(perm.SITE_STOCK, BTN_SITE_STOCK), (perm.TUNDISH_REPORT, BTN_TR_MENU)],
        [(perm.MATERIAL_REQUEST, BTN_MATERIAL_REQUEST), (perm.WAREHOUSE_RETURN, BTN_WAREHOUSE_RETURN)],
        [(perm.REPORTS, BTN_ANALYTICS), (perm.MAIN_GOAL, BTN_MAIN_GOAL)],
        [((perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT), BTN_UPLOAD_MENU)],
        [(perm.SETTINGS, BTN_BOT_SETTINGS)],
    ):
        row = [
            label for feat, label in pair
            if (perm.can_any(u, feat) if isinstance(feat, tuple) else perm.can(u, feat))
        ]
        if row:
            rows.append(row)
    # «❓ راهنما» shares the settings row when it exists
    if rows and rows[-1] == [BTN_BOT_SETTINGS]:
        rows[-1].append(BTN_HELP)
    else:
        rows.append([BTN_HELP])
    return _kb(rows, "main", nav=False)


def upload_files_menu(user: dict | None = None) -> dict:
    """«📤 ورود فایل‌ها»: stock update / monthly / منبع اصلی submenu."""
    rows: list[list[str]] = []
    if user is None or perm.can(user, perm.FILE_INPUTS):
        rows.append([BTN_WAREHOUSE_STOCK, BTN_MONTHLY])
    if user is None or perm.can_any(user, (perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT)):
        rows.append([BTN_MAIN_SOURCE_FILE])
    return _kb(rows, "files")


file_entry_menu = upload_files_menu


def main_source_file_menu(user: dict | None = None) -> dict:
    """«📦 منبع اصلی»: full replace / download / record + code edits.

    Edit buttons need MAIN_SOURCE_EDIT; download + code list need file inputs or edit.
    """
    if user is None or perm.can(user, perm.MAIN_SOURCE_EDIT):
        rows = [
            [BTN_FULL_REPLACE, BTN_INV_DOWNLOAD],
            [BTN_INV_ADD_RECORD, BTN_INV_EDIT_RECORD],
            [BTN_INV_ADD_CATEGORY, BTN_INV_LIST_CATEGORIES],
        ]
    else:
        rows = [[BTN_INV_DOWNLOAD, BTN_INV_LIST_CATEGORIES]]
    return _kb(rows, "main_source")


inventory_menu = main_source_file_menu
inventory_edit_menu = main_source_file_menu


def full_replace_confirm_menu() -> dict:
    return _kb([[BTN_FULL_REPLACE_CONFIRM], [BTN_CANCEL]], "flow", nav=False)


def cancel_pending_menu(*, guide: bool = False, nav: bool = False) -> dict:
    """While waiting for a file / typed value: cancel (+ file guide when relevant).

    ``nav=True`` adds the standard «⬅️ بازگشت / 🏠 منوی اصلی» row.
    """
    rows = [[BTN_CANCEL]]
    if guide:
        rows[0].append(BTN_FILE_GUIDE)
    rows.append([BTN_HELP])
    return _kb(rows, "flow", nav=nav)


def analytics_menu(user: dict | None = None) -> dict:
    """«📊 گزارش‌ها» (two-column, each report permission-checked)."""
    u = user
    items = [
        (perm.REPORT_DAILY, BTN_DAILY),
        (perm.REPORT_PERIOD, BTN_PERIOD),
        (perm.REPORT_COMPREHENSIVE, BTN_COMPREHENSIVE),
        (perm.REPORT_SHORT_COVER, BTN_REMAINING),
        (perm.REPORT_CRITICAL, BTN_CRITICAL_ITEMS),
        (perm.REPORT_N_TUNDISH, BTN_N_TUNDISH),
        (perm.REPORT_SURPLUS, BTN_SURPLUS),
        (perm.REPORT_INBOUND, BTN_INBOUND),
        (perm.REPORT_MONTHLY_SUMMARY, BTN_MONTHLY_SUMMARY),
    ]
    labels = [lbl for feat, lbl in items if u is None or perm.can(u, feat)]
    return _kb(_pairs(labels), "reports")


def inbound_history_menu(labels: list[str] | None = None) -> dict:
    return _kb([[lbl] for lbl in (labels or [])], "inbound_history")


def critical_items_menu() -> dict:
    return _kb([[BTN_CRITICAL_COUNTS, BTN_CRITICAL_REPORT]], "critical")


BTN_CRITICAL_RENO_WITH = "با نوسازی"
BTN_CRITICAL_RENO_WITHOUT = "بدون نوسازی"
CB_CRITICAL_RENO_PREFIX = "ci|reno|"


def critical_reno_inline_keyboard() -> dict:
    return BaleClient.inline_keyboard(
        [
            [
                {"text": f"🔧 {BTN_CRITICAL_RENO_WITH}", "callback_data": f"{CB_CRITICAL_RENO_PREFIX}with"},
                {"text": f"🩹 {BTN_CRITICAL_RENO_WITHOUT}", "callback_data": f"{CB_CRITICAL_RENO_PREFIX}without"},
            ]
        ]
    )


def section_step_menu() -> dict:
    """Optional step «بخش: همه/اسلب/بلوم/بیلت»."""
    return _kb([[BTN_SEC_ALL], [BTN_SEC_SLAB, BTN_SEC_BLOOM, BTN_SEC_BILLET], [BTN_CANCEL]], "flow")


def n_tundish_section_menu() -> dict:
    return _kb([[BTN_SEC_SLAB, BTN_SEC_BLOOM, BTN_SEC_BILLET], [BTN_CANCEL]], "flow")


def n_tundish_count_menu() -> dict:
    return _kb([["۱", "۲", "۳", "۴"], ["۵", "۶", "۸", "۱۰"], [BTN_CANCEL]], "flow")


def n_tundish_mode_menu() -> dict:
    return _kb([[BTN_NT_WITH, BTN_NT_WITHOUT], [BTN_CANCEL]], "flow")


def main_goal_menu() -> dict:
    return _kb(
        [
            [BTN_MG_INPUTS, BTN_MG_REPORT_REQ],
            [BTN_MG_SCN_TARGET, BTN_MG_SCN_FORECAST],
            [BTN_MG_HISTORY, BTN_MG_RECENT],
        ],
        "main_goal",
    )


def main_goal_inputs_menu() -> dict:
    return _kb(
        [
            [BTN_MG_INPUT_PROD, BTN_MG_INPUT_PROD_XLSX],
            [BTN_MG_INPUT_BILLET, BTN_MG_INPUT_BLOOM],
            [BTN_MG_INPUT_SLAB, BTN_MG_INPUT_CORRECT],
            [BTN_MG_BULK, BTN_FILE_GUIDE],
        ],
        "mg_inputs",
    )


def main_goal_range_menu() -> dict:
    return _kb(
        [[BTN_MG_RANGE_3, BTN_MG_RANGE_6], [BTN_MG_RANGE_12, BTN_MG_RANGE_CUSTOM], [BTN_CANCEL]],
        "flow",
    )


def main_goal_partial_confirm_menu() -> dict:
    return _kb([[BTN_MG_CONFIRM_PARTIAL], [BTN_CANCEL]], "flow")


def main_goal_upload_menu() -> dict:
    """While waiting for one main-goal file (photo / Excel)."""
    return _kb([[BTN_CANCEL, BTN_FILE_GUIDE]], "flow")


def main_goal_bulk_menu() -> dict:
    return _kb([[BTN_MG_BULK_DONE], [BTN_MG_BULK_STOP], [BTN_FILE_GUIDE]], "flow")


def main_goal_history_menu(*, can_delete: bool = False) -> dict:
    rows = [[BTN_MG_DELETE]] if can_delete else []
    return _kb(rows, "mg_history")


def main_goal_period_menu() -> dict:
    return _kb([[BTN_MG_P1, BTN_MG_P3], [BTN_MG_P6, BTN_MG_P12], [BTN_CANCEL]], "flow")


def main_goal_section_menu() -> dict:
    return _kb(
        [[BTN_MG_SEC_BILLET, BTN_MG_SEC_BLOOM, BTN_MG_SEC_SLAB], [BTN_MG_SEC_TOTAL], [BTN_CANCEL]],
        "flow",
    )


def main_goal_targets_confirm_menu() -> dict:
    return _kb([[BTN_MG_COMPUTE], [BTN_MG_ADD_SECTION], [BTN_CANCEL]], "flow")


def main_goal_horizon_menu() -> dict:
    return _kb([[BTN_MG_P3, BTN_MG_P6, BTN_MG_P12], [BTN_CANCEL]], "flow")


def main_goal_cancel_menu() -> dict:
    return _kb([[BTN_CANCEL]], "flow")


def main_goal_target_menu() -> dict:
    return _kb([[BTN_MG_SKIP_TARGET], [BTN_CANCEL]], "flow")


def date_range_menu() -> dict:
    return _kb([[BTN_RANGE_TODAY, BTN_RANGE_7, BTN_RANGE_30], [BTN_RANGE_CUSTOM]], "flow")


def month_year_range_menu(*, include_day_advanced: bool = False) -> dict:
    """Month/year presets — «ماه جاری» is the first/default button."""
    rows = [
        [BTN_MY_CURRENT],
        [BTN_MY_3, BTN_MY_YTD],
        [BTN_MY_CUSTOM, BTN_MY_TYPED],
    ]
    if include_day_advanced:
        rows.append([BTN_MY_DAY_ADV])
    return _kb(rows, "flow")


def year_picker_menu(years: list[int]) -> dict:
    labels = [str(y) for y in years]
    return _kb(_pairs(labels), "flow")


def month_picker_menu() -> dict:
    from bot.jalali import PERSIAN_MONTH_NAMES

    names = [PERSIAN_MONTH_NAMES[i] for i in range(1, 13)]
    return _kb([names[i : i + 3] for i in range(0, 12, 3)], "flow")


def material_request_days_menu() -> dict:
    return _kb([[BTN_MR_DAYS_DEFAULT], [BTN_MR_HISTORY], [BTN_CANCEL]], "material_request")


def material_request_review_menu(*, draft: bool = True) -> dict:
    """Proposal review (درخواست مواد / برگشت به انبار) + standard «⬅️ بازگشت / 🏠 منوی اصلی»."""
    rows = [[BTN_MR_CONFIRM_ALL, BTN_MR_EDIT]]
    if draft:
        rows.append([BTN_MR_DRAFT])
    rows.append([BTN_CANCEL])
    return _kb(rows, "flow", nav=True)


def material_request_edit_menu() -> dict:
    return _kb([[BTN_MR_BACK_REVIEW], [BTN_CANCEL]], "flow", nav=False)


def warehouse_return_review_menu() -> dict:
    return material_request_review_menu(draft=False)


def warehouse_return_edit_menu() -> dict:
    return material_request_edit_menu()


def site_stock_menu() -> dict:
    return _kb([[BTN_SITE_SLAB, BTN_SITE_BLOOM], [BTN_SITE_BILLET]], "site_stock")


def site_stock_entry_menu() -> dict:
    return _kb([[BTN_SITE_SKIP], [BTN_SITE_CANCEL], [BTN_BACK_SITE]], "flow", nav=False)


def bot_settings_menu(user: dict | None = None) -> dict:
    """«⚙️ تنظیمات» (owner/manager)."""
    items = [
        (perm.USERS, BTN_USERS),
        (perm.USER_ACTIVITY, BTN_USER_ACTIVITY),
        (perm.ROLE_PERMISSIONS, BTN_ROLE_PERMS),
        (perm.TUNDISH_REPORT_SETTINGS, BTN_TR_SETTINGS),
        (perm.REMINDERS, BTN_SET_REMINDERS),
        (perm.STOCK_GROUP, BTN_SET_STOCK_GROUP),
        (perm.APPEARANCE, BTN_APPEARANCE),
    ]
    labels = [lbl for feat, lbl in items if user is None or perm.can(user, feat)]
    return _kb(_pairs(labels), "settings")


def appearance_menu() -> dict:
    """«🎨 ظاهر»: invite / welcome / logo / letterhead."""
    return _kb([[BTN_SET_INVITE, BTN_SET_WELCOME], [BTN_SET_LOGO, BTN_SET_LETTERHEAD]], "appearance")


def bot_settings_letterhead_menu() -> dict:
    return _kb(
        [[BTN_SETTINGS_VIEW], [BTN_SETTINGS_UPLOAD_LETTERHEAD, BTN_SETTINGS_CLEAR_LETTERHEAD]],
        "letterhead",
    )


def bot_settings_stock_group_menu() -> dict:
    return _kb([[BTN_SETTINGS_VIEW, BTN_SETTINGS_CLEAR_STOCK_GROUP]], "stock_group")


def bot_settings_item_menu(*, include_text: bool = True) -> dict:
    rows: list[list[str]] = [[BTN_SETTINGS_VIEW] + ([BTN_SETTINGS_EDIT_TEXT] if include_text else [])]
    rows.append([BTN_SETTINGS_SET_IMAGE, BTN_SETTINGS_CLEAR_IMAGE])
    return _kb(rows, "appearance_item")


def users_menu() -> dict:
    return _kb([[BTN_USERS_ADD, BTN_USERS_EDIT], [BTN_USERS_DELETE, BTN_USERS_LIST]], "users")


def invite_confirm_menu() -> dict:
    return _kb([[BTN_INVITE_CONFIRM], [BTN_CANCEL]], "flow")


def role_menu(include_owner: bool = False) -> dict:
    rows: list[list[str]] = []
    if include_owner:
        rows.append([BTN_ROLE_OWNER])
    rows.extend([[BTN_ROLE_MANAGER, BTN_ROLE_OFFICER], [BTN_ROLE_TECH, BTN_ROLE_SHIFT]])
    return _kb(rows, "flow")


def reminder_settings_menu(enabled: bool) -> dict:
    return _kb(
        [
            [BTN_RM_STATUS, BTN_RM_DISABLE if enabled else BTN_RM_ENABLE],
            [BTN_RM_ROLES, BTN_RM_USERS],
            [BTN_RM_SCHEDULE, BTN_RM_SEND_NOW],
        ],
        "reminders",
    )


def reminder_roles_menu(selected: list[str] | set[str]) -> dict:
    from config import ROLES

    sel = set(selected or [])
    rows = [[(RM_CHECK_ON if key in sel else RM_CHECK_OFF) + label] for key, label in ROLES.items()]
    rows.append([BTN_RM_BACK, BTN_HOME])
    return _kb(rows, "flow", nav=False)


def reminder_back_menu() -> dict:
    return _kb([[BTN_RM_BACK, BTN_HOME]], "flow", nav=False)


def invite_url_button(url: str) -> dict:
    return BaleClient.inline_url_keyboard(BTN_INVITE_ENTER, url)


_BTN_TEXT_LIMIT = 60
_QTY_PLACEHOLDER = "…"

def item_display_name(item: dict | None = None, *, name_desc: str | None = None, item_id: str | None = None) -> str:
    """شرح کالا without leading catalog id (e.g. ``378…L - شرح`` → ``شرح``)."""
    if item:
        name_desc = item.get("name_desc") if name_desc is None else name_desc
        item_id = item.get("id") if item_id is None else item_id
    s = (name_desc or "").strip()
    iid = (item_id or "").strip()
    if not s:
        return iid or "—"
    if iid and s.startswith(iid):
        rest = s[len(iid):].lstrip(" \t-–—:")
        if rest:
            return rest.strip()
    # Generic leading code + separator
    import re
    m = re.match(r"^[A-Za-z0-9._]+\s*[-–—:]\s*(.+)$", s)
    if m:
        return m.group(1).strip() or s
    return s




def _truncate_btn(text: str, limit: int = _BTN_TEXT_LIMIT) -> str:
    s = (text or "").strip() or "—"
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)] + "…"


def site_stock_inline_keyboard(
    items: list[dict],
    values: dict | None = None,
    *,
    group_key: str,
) -> dict:
    """Two-column inline keyboard: شرح کالا | تعداد, plus confirm/cancel.

    ``callback_data`` is compact: ``ss|{group}|{idx}|q`` / ``ss|{group}|{idx}|n`` /
    ``ss|ok`` / ``ss|x``.
    """
    values = values or {}
    group = (group_key or "").strip().lower()
    rows: list[list[dict[str, str]]] = []
    for idx, it in enumerate(items):
        name = _truncate_btn(item_display_name(it))
        iid = it.get("id")
        qty = values.get(iid) if iid is not None else None
        if qty is None:
            qty_label = _QTY_PLACEHOLDER
        else:
            try:
                qty_label = f"{float(qty):g}"
            except (TypeError, ValueError):
                qty_label = str(qty)
            qty_label = _truncate_btn(qty_label, 16)
        rows.append(
            [
                {"text": name, "callback_data": f"ss|{group}|{idx}|n"},
                {"text": qty_label, "callback_data": f"ss|{group}|{idx}|q"},
            ]
        )
    rows.append([{"text": f"✅ {BTN_SITE_CONFIRM}", "callback_data": "ss|ok"}])
    rows.append([{"text": BTN_SITE_CANCEL, "callback_data": "ss|x"}])
    return BaleClient.inline_keyboard(rows)



# ---------------------------------------------------------------------------
# 🧾 گزارش تاندیش (all roles enter; settings only under ⚙️ تنظیمات)
# ---------------------------------------------------------------------------
BTN_TR_SECTION = {
    "slab": "🧾 گزارش تاندیش اسلب",
    "bloom": "🧾 گزارش تاندیش بلوم",
    "billet": "🧾 گزارش تاندیش بیلت",
}
TR_SECTION_BUTTONS = {v: k for k, v in BTN_TR_SECTION.items()}
BTN_TR_RECENT = "📜 آخرین گزارش‌های تاندیش"
BTN_TR_BACK = BTN_BACK
BTN_TR_MODE_STEP = "✍️ ورود مرحله‌ای"
BTN_TR_MODE_TEXT = "📋 ورود سریع از متن"
BTN_TR_SKIP = "⏭ رد کردن (اختیاری)"
BTN_TR_PREV = "↩️ مرحله قبل"
BTN_TR_CANCEL = BTN_CANCEL
BTN_TR_CONFIRM = "✅ تأیید و ثبت گزارش"
BTN_TR_EDIT = "✏️ اصلاح یک مورد"

BTN_TRS_SECTION = {
    "slab": "⚙️ اقلام گزارش اسلب",
    "bloom": "⚙️ اقلام گزارش بلوم",
    "billet": "⚙️ اقلام گزارش بیلت",
}
TRS_SECTION_BUTTONS = {v: k for k, v in BTN_TRS_SECTION.items()}
BTN_TRS_ADD = "➕ افزودن قلم گزارش"
BTN_TRS_EDIT = "✏️ ویرایش قلم گزارش"
BTN_TRS_DELETE = "🗑 حذف قلم گزارش"
BTN_TRS_LINES = "🏭 خطوط / ماشین‌ها"
BTN_TRS_BACK = BTN_BACK
BTN_TRS_BACK_SECTION = "⬅️ بازگشت به اقلام بخش"
BTN_TRS_E_LABEL = "✏️ تغییر برچسب"
BTN_TRS_E_TYPE = "🔤 تغییر نوع"
BTN_TRS_E_OPTIONS = "📋 تغییر گزینه‌ها"
BTN_TRS_E_REQUIRED = "❗ اجباری / اختیاری"
BTN_TRS_E_ORDER = "🔢 تغییر ترتیب"
BTN_TRS_DELETE_CONFIRM = "🗑 تأیید حذف قلم"
BTN_TRS_TYPE_CHOICE = "انتخابی"
BTN_TRS_TYPE_NUMBER = "عددی"
BTN_TRS_TYPE_TEXT = "متنی"
BTN_TRS_REQUIRED = "اجباری"
BTN_TRS_OPTIONAL = "اختیاری"


def tundish_report_menu(user: dict | None = None) -> dict:
    """Three sections + recent (settings live under ⚙️ تنظیمات only)."""
    return _kb(
        [[BTN_TR_SECTION["slab"], BTN_TR_SECTION["bloom"]], [BTN_TR_SECTION["billet"], BTN_TR_RECENT]],
        "tundish_report",
    )


def tundish_report_mode_menu() -> dict:
    return _kb([[BTN_TR_MODE_STEP, BTN_TR_MODE_TEXT], [BTN_CANCEL]], "flow", nav=False)


def tundish_report_step_menu(options: list[str] | None = None, *, optional: bool = False, can_prev: bool = True) -> dict:
    rows: list[list[str]] = []
    opts = [_truncate_btn(o) for o in (options or [])]
    row: list[str] = []
    for o in opts:
        if len(o) > 18:
            if row:
                rows.append(row)
                row = []
            rows.append([o])
            continue
        row.append(o)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if optional:
        rows.append([BTN_TR_SKIP])
    rows.append([BTN_TR_PREV, BTN_CANCEL] if can_prev else [BTN_CANCEL])
    return _kb(rows, "flow", nav=False)


def tundish_report_confirm_menu() -> dict:
    return _kb([[BTN_TR_CONFIRM], [BTN_TR_EDIT, BTN_CANCEL]], "flow", nav=False)


def tundish_report_settings_menu() -> dict:
    return _kb(
        [[BTN_TRS_SECTION["slab"], BTN_TRS_SECTION["bloom"]], [BTN_TRS_SECTION["billet"]]],
        "tr_settings",
    )


def tundish_report_section_settings_menu() -> dict:
    return _kb([[BTN_TRS_ADD], [BTN_TRS_EDIT, BTN_TRS_DELETE], [BTN_TRS_LINES]], "tr_section_settings")


def tundish_report_item_edit_menu() -> dict:
    return _kb(
        [[BTN_TRS_E_LABEL, BTN_TRS_E_TYPE], [BTN_TRS_E_OPTIONS, BTN_TRS_E_REQUIRED], [BTN_TRS_E_ORDER], [BTN_TRS_BACK_SECTION, BTN_HOME]],
        "flow",
        nav=False,
    )


def tundish_report_type_menu() -> dict:
    return _kb([[BTN_TRS_TYPE_CHOICE, BTN_TRS_TYPE_NUMBER, BTN_TRS_TYPE_TEXT], [BTN_TRS_BACK_SECTION, BTN_HOME]], "flow", nav=False)


def tundish_report_required_menu() -> dict:
    return _kb([[BTN_TRS_REQUIRED, BTN_TRS_OPTIONAL], [BTN_TRS_BACK_SECTION, BTN_HOME]], "flow", nav=False)


def tundish_report_back_section_menu() -> dict:
    return _kb([[BTN_TRS_BACK_SECTION, BTN_HOME]], "flow", nav=False)


def tundish_report_delete_confirm_menu() -> dict:
    return _kb([[BTN_TRS_DELETE_CONFIRM], [BTN_TRS_BACK_SECTION, BTN_HOME]], "flow", nav=False)


# Every keyboard builder (menu-walk smoke iterates these)
ALL_KEYBOARD_BUILDERS = (
    "main_menu", "upload_files_menu", "main_source_file_menu", "full_replace_confirm_menu",
    "cancel_pending_menu", "analytics_menu", "critical_items_menu", "section_step_menu",
    "n_tundish_section_menu", "n_tundish_count_menu", "n_tundish_mode_menu", "main_goal_menu",
    "main_goal_inputs_menu", "main_goal_range_menu", "main_goal_partial_confirm_menu",
    "main_goal_upload_menu", "main_goal_bulk_menu", "main_goal_history_menu", "main_goal_period_menu",
    "main_goal_section_menu", "main_goal_targets_confirm_menu", "main_goal_horizon_menu",
    "main_goal_cancel_menu", "main_goal_target_menu", "date_range_menu", "month_year_range_menu",
    "year_picker_menu", "month_picker_menu", "material_request_days_menu",
    "material_request_review_menu", "material_request_edit_menu", "warehouse_return_review_menu",
    "site_stock_menu", "site_stock_entry_menu", "bot_settings_menu", "appearance_menu",
    "bot_settings_letterhead_menu", "bot_settings_stock_group_menu", "bot_settings_item_menu",
    "users_menu", "invite_confirm_menu", "role_menu", "reminder_settings_menu",
    "reminder_roles_menu", "reminder_back_menu", "tundish_report_menu", "tundish_report_mode_menu",
    "tundish_report_step_menu", "tundish_report_confirm_menu", "tundish_report_settings_menu",
    "tundish_report_section_settings_menu", "tundish_report_item_edit_menu", "tundish_report_type_menu",
    "tundish_report_required_menu", "tundish_report_back_section_menu",
    "tundish_report_delete_confirm_menu", "inbound_history_menu",
)


# ---------------------------------------------------------------------------
# 🔐 دسترسی نقش‌ها (phase 2 item 18)
# ---------------------------------------------------------------------------
CB_ROLE_PERM_PREFIX = "rp|"
RP_RESET = "__reset__"
RP_ON = "✅ "
RP_OFF = "⬜ "
BTN_RP_ROLE_PREFIX = "🔐 "  # «🔐 مدیر» … one reply button per editable role


def role_perm_button(role: str) -> str:
    from config import ROLES

    return BTN_RP_ROLE_PREFIX + ROLES.get(role, role)


def role_perm_button_to_role(text: str) -> str | None:
    from services import permissions as _perm

    for r in _perm.EDITABLE_ROLES:
        if text == role_perm_button(r):
            return r
    return None


def role_perms_role_menu() -> dict:
    from services import permissions as _perm

    btns = [role_perm_button(r) for r in _perm.EDITABLE_ROLES]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return _kb(rows, "role_perms")


def role_perms_inline(role: str, rows: list[dict]) -> dict:
    """Inline checklist: one button per feature (tap = toggle) + reset to defaults."""
    kb_rows = [
        [{
            "text": (RP_ON if r["allowed"] else RP_OFF) + r["label"] + ("" if r["allowed"] == r["default"] else " •"),
            "callback_data": f"{CB_ROLE_PERM_PREFIX}{role}|{r['feature']}",
        }]
        for r in rows
    ]
    kb_rows.append([{"text": "↺ بازگشت به پیش‌فرض", "callback_data": f"{CB_ROLE_PERM_PREFIX}{role}|{RP_RESET}"}])
    return BaleClient.inline_keyboard(kb_rows)


def _button_features() -> dict[str, tuple[str, ...]]:
    P = perm
    m: dict[str, tuple[str, ...]] = {
        BTN_SITE_STOCK: (P.SITE_STOCK,),
        BTN_TR_MENU: (P.TUNDISH_REPORT,),
        BTN_MATERIAL_REQUEST: (P.MATERIAL_REQUEST,),
        BTN_WAREHOUSE_RETURN: (P.WAREHOUSE_RETURN,),
        BTN_ANALYTICS: P.REPORT_FEATURES,
        BTN_MAIN_GOAL: (P.MAIN_GOAL,),
        BTN_UPLOAD_MENU: (P.FILE_INPUTS, P.MAIN_SOURCE_EDIT),
        BTN_BOT_SETTINGS: P.SETTINGS_FEATURES,
        BTN_DAILY: (P.REPORT_DAILY,),
        BTN_PERIOD: (P.REPORT_PERIOD,),
        BTN_COMPREHENSIVE: (P.REPORT_COMPREHENSIVE,),
        BTN_REMAINING: (P.REPORT_SHORT_COVER,),
        BTN_CRITICAL_ITEMS: (P.REPORT_CRITICAL,),
        BTN_N_TUNDISH: (P.REPORT_N_TUNDISH,),
        BTN_SURPLUS: (P.REPORT_SURPLUS,),
        BTN_INBOUND: (P.REPORT_INBOUND,),
        BTN_MONTHLY_SUMMARY: (P.REPORT_MONTHLY_SUMMARY,),
        BTN_WAREHOUSE_STOCK: (P.FILE_INPUTS,),
        BTN_MONTHLY: (P.FILE_INPUTS,),
        BTN_MAIN_SOURCE_FILE: (P.FILE_INPUTS, P.MAIN_SOURCE_EDIT),
        BTN_INV_DOWNLOAD: (P.FILE_INPUTS, P.MAIN_SOURCE_EDIT),
        BTN_INV_LIST_CATEGORIES: (P.FILE_INPUTS, P.MAIN_SOURCE_EDIT),
        BTN_FULL_REPLACE: (P.MAIN_SOURCE_EDIT,),
        BTN_INV_ADD_RECORD: (P.MAIN_SOURCE_EDIT,),
        BTN_INV_EDIT_RECORD: (P.MAIN_SOURCE_EDIT,),
        BTN_INV_ADD_CATEGORY: (P.MAIN_SOURCE_EDIT,),
        BTN_USERS: (P.USERS,),
        BTN_USERS_ADD: (P.USERS,),
        BTN_USERS_EDIT: (P.USERS,),
        BTN_USERS_DELETE: (P.USERS,),
        BTN_USERS_LIST: (P.USERS,),
        BTN_USER_ACTIVITY: (P.USER_ACTIVITY,),
        BTN_ROLE_PERMS: (P.ROLE_PERMISSIONS,),
        BTN_TR_SETTINGS: (P.TUNDISH_REPORT_SETTINGS,),
        BTN_SET_REMINDERS: (P.REMINDERS,),
        BTN_SET_STOCK_GROUP: (P.STOCK_GROUP,),
        BTN_APPEARANCE: (P.APPEARANCE,),
        BTN_SET_INVITE: (P.APPEARANCE,),
        BTN_SET_WELCOME: (P.APPEARANCE,),
        BTN_SET_LOGO: (P.APPEARANCE,),
        BTN_SET_LETTERHEAD: (P.APPEARANCE,),
        BTN_MG_DELETE: (P.MAIN_GOAL_DELETE,),
    }
    return m


# canonical button label → features (any) required; checked centrally in
# handlers.handle_message before any flow sees the text (bot "handlers also check").
BUTTON_FEATURE: dict[str, tuple[str, ...]] = _button_features()
