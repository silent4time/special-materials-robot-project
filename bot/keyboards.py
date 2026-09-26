"""Persian reply keyboards for upload flow, warehouse inventory submenu, site stock, and analytics."""
from __future__ import annotations

from config import FILE_TYPES, SITE_STOCK_GROUPS, TUNDISH_TYPES
from bot.bale_api import BaleClient


BTN_TANK = FILE_TYPES["tank_consumption"]["button"]
BTN_INV = FILE_TYPES["product_inventory"]["button"]
BTN_MONTHLY = FILE_TYPES["monthly_consumption"]["button"]
BTN_STATUS = "📋 وضعیت فایل‌ها"
BTN_GENERATE = "✅ تولید گزارش PDF"
BTN_RESET = "🔄 شروع مجدد"
BTN_HELP = "❓ راهنما"
BTN_USERS = "👥 کاربران"
BTN_USERS_ADD = "➕ اضافه کردن کاربر"
BTN_USERS_EDIT = "✏️ اصلاح نقش کاربر"
BTN_USERS_DELETE = "🗑 حذف کاربر"
BTN_USERS_LIST = "📋 لیست کاربران"
BTN_BACK_USERS = "⬅️ بازگشت"

# Bot settings (owner/manager)
BTN_BOT_SETTINGS = "⚙️ تنظیمات ربات"
BTN_SET_INVITE = "📝 متن دعوت‌نامه کاربران"
BTN_SET_WELCOME = "👋 پیام خوشامدگویی"
BTN_SET_LOGO = "🖼 لوگوی ربات"
BTN_SET_LETTERHEAD = "📄 سربرگ PDF"
BTN_SETTINGS_UPLOAD_LETTERHEAD = "📄 آپلود سربرگ PDF"
BTN_SETTINGS_CLEAR_LETTERHEAD = "🗑 حذف سربرگ"
BTN_BACK_BOT_SETTINGS = "⬅️ بازگشت به تنظیمات ربات"
BTN_SETTINGS_VIEW = "👁 مشاهده"
BTN_SETTINGS_EDIT_TEXT = "✏️ ویرایش متن"
BTN_SETTINGS_SET_IMAGE = "🖼 تنظیم تصویر"
BTN_SETTINGS_CLEAR_IMAGE = "🗑 حذف تصویر"
BTN_CANCEL_PENDING = "✖️ انصراف از آپلود"

# Role pick buttons (Persian labels from config.ROLES)
BTN_ROLE_OWNER = "مالک"
BTN_ROLE_MANAGER = "مدیر"
BTN_ROLE_OFFICER = "کاردان مسئول"
BTN_ROLE_TECH = "تکنسین"

ROLE_BUTTON_TO_KEY = {
    BTN_ROLE_OWNER: "owner",
    BTN_ROLE_MANAGER: "manager",
    BTN_ROLE_OFFICER: "responsible_officer",
    BTN_ROLE_TECH: "technician",
}

# موجودی انبار submenu
BTN_INV_MENU = "📦 موجودی انبار"
BTN_INV_UPLOAD = "📥 ورود فایل اکسل"
BTN_INV_ADD_CATEGORY = "➕ اضافه کردن کد دسته بندی"
BTN_INV_LIST_CATEGORIES = "📋 لیست کدهای دسته بندی"

# موجودی روزانه سایت — interactive entry (not Excel primary path)
BTN_SITE_STOCK = BTN_TANK  # «📥 موجودی روزانه سایت»
BTN_SITE_SLAB = SITE_STOCK_GROUPS["slab"]  # موجودی مواد اسلب
BTN_SITE_BLOOM = SITE_STOCK_GROUPS["bloom"]  # موجودی مواد بلوم
BTN_SITE_BILLET = SITE_STOCK_GROUPS["billet"]  # موجودی مواد بیلت
BTN_SITE_SKIP = "⏭ رد کردن این قلم"
BTN_SITE_CANCEL = "✖️ انصراف از ورود موجودی"
BTN_BACK_SITE = "⬅️ بازگشت به گروه‌های سایت"

# تنظیمات اقلام سایت / تخصیص به گروه (non-technician)
BTN_CATALOG_SETTINGS = "⚙️ تنظیمات اقلام سایت / تخصیص به گروه"
BTN_CATALOG_LIST = "📋 لیست اقلام و تخصیص‌ها"
BTN_CATALOG_UNASSIGNED = "📭 اقلام بدون گروه"
BTN_CATALOG_SEED = "🔄 همگام‌سازی از موجودی انبار"
BTN_CATALOG_ASSIGN_SLAB = "تخصیص به اسلب"
BTN_CATALOG_ASSIGN_BLOOM = "تخصیص به بلوم"
BTN_CATALOG_ASSIGN_BILLET = "تخصیص به بیلت"
BTN_CATALOG_UNASSIGN = "حذف تخصیص"
BTN_BACK_CATALOG = "⬅️ بازگشت به تنظیمات اقلام"

# Analytics / reports submenu
BTN_ANALYTICS = "📊 گزارش‌ها / تحلیل تاندیش"
BTN_DAILY = "📈 مصرف روزانه مواد"
BTN_SUGGEST = "🛒 پیشنهاد درخواست مواد"
BTN_PERIOD = "📅 گزارش مصرف بازه‌ای"
BTN_REMAINING = "⚠️ موجودی و مواد بحرانی"
BTN_SURPLUS = "📦 گزارش مواد مازاد"
BTN_FORECAST = "🔮 پیش‌بینی نیاز تاندیش"
BTN_ANALYTICS_PDF = "📄 PDF کامل تحلیل"
BTN_MONTHLY_SUMMARY = "📥 دریافت خلاصه مصرف ماهیانه"
BTN_INBOUND = "📥 گزارش ورودی به انبار"
BTN_TUNDISH_FILTER = "🔎 فیلتر نوع تاندیش"
BTN_ALL_TUNDISHES = "همه تاندیش‌ها"
BTN_TUNDISH_SLAB = TUNDISH_TYPES["slab"]
BTN_TUNDISH_BLOOM = TUNDISH_TYPES["bloom"]
BTN_TUNDISH_BILLET = TUNDISH_TYPES["billet"]
BTN_BACK_MAIN = "⬅️ بازگشت به منوی اصلی"

# Material request workflow (owner / manager / officer)
BTN_MATERIAL_REQUEST = "🛒 درخواست مواد"
BTN_WAREHOUSE_RETURN = "↩️ برگشت به انبار"
BTN_MR_CONFIRM_ALL = "✅ تأیید همه"
BTN_MR_EDIT = "✏️ اصلاح"
BTN_MR_CANCEL = "✖️ انصراف"
BTN_MR_BACK_REVIEW = "⬅️ بازگشت به بررسی"
BTN_MR_HISTORY = "📜 تاریخچه درخواست‌ها"
BTN_MR_DAYS_7 = "۷ روز"
BTN_MR_DAYS_14 = "۱۴ روز"
BTN_MR_DAYS_30 = "۳۰ روز"

# Date-range presets (day-level — advanced for period reports)
BTN_RANGE_TODAY = "امروز"
BTN_RANGE_7 = "۷ روز"
BTN_RANGE_30 = "۳۰ روز"
BTN_RANGE_CUSTOM = "بازه سفارشی"
BTN_BACK_ANALYTICS = "⬅️ بازگشت به تحلیل"

# Month/year range presets (primary UX for all time-based reports)
BTN_MY_CURRENT = "ماه جاری"
BTN_MY_3 = "۳ ماه اخیر"
BTN_MY_YTD = "از ابتدای سال"
BTN_MY_CUSTOM = "بازه سفارشی ماه"
BTN_MY_DAY_ADV = "بازه روزانه (پیشرفته)"
BTN_MY_TYPED = "ورود دستی بازه"

# Map site-stock group button label → internal key
SITE_GROUP_BUTTONS = {
    BTN_SITE_SLAB: "slab",
    BTN_SITE_BLOOM: "bloom",
    BTN_SITE_BILLET: "billet",
}

ASSIGN_GROUP_BUTTONS = {
    BTN_CATALOG_ASSIGN_SLAB: "slab",
    BTN_CATALOG_ASSIGN_BLOOM: "bloom",
    BTN_CATALOG_ASSIGN_BILLET: "billet",
}


def button_to_file_type(text: str) -> str | None:
    """Map upload buttons to file types.

    Note: BTN_TANK / موجودی روزانه سایت is **interactive entry** now and is
    intentionally NOT mapped here (handled by site-stock submenu).
    """
    mapping = {
        BTN_INV_UPLOAD: "product_inventory",
        BTN_MONTHLY: "monthly_consumption",
        FILE_TYPES["product_inventory"]["label_fa"]: "product_inventory",
        FILE_TYPES["monthly_consumption"]["label_fa"]: "monthly_consumption",
        # legacy button texts (pre-redesign) — monthly / warehouse only
        "📥 موجودی محصولات": "product_inventory",
        "📥 مصرف ماهانه مواد": "monthly_consumption",
        # legacy tank Excel button text kept for rare old clients wanting file path:
        # intentionally omitted so «موجودی روزانه سایت» opens interactive flow
    }
    return mapping.get((text or "").strip())


def main_menu(user: dict | str | bool | None = None) -> dict:
    """Return the main menu for a role (technicians only enter daily site stock).

    ``bool`` is accepted for compatibility with older callers where ``True``
    meant manager access. New callers should pass the user record.
    """
    role = user.get("role") if isinstance(user, dict) else user if isinstance(user, str) else None
    if role == "technician":
        return BaleClient.reply_keyboard([[BTN_SITE_STOCK], [BTN_HELP]])

    is_manager = bool(user) if isinstance(user, bool) else role in {"owner", "manager"}
    rows = [
        [BTN_INV_MENU],
        [BTN_MONTHLY],
        [BTN_SITE_STOCK],
        [BTN_CATALOG_SETTINGS],
        [BTN_MATERIAL_REQUEST, BTN_WAREHOUSE_RETURN],
        [BTN_STATUS, BTN_GENERATE],
        [BTN_ANALYTICS],
        [BTN_RESET, BTN_HELP],
    ]
    if is_manager:
        rows.append([BTN_USERS])
        rows.append([BTN_BOT_SETTINGS])
    return BaleClient.reply_keyboard(rows)



def users_menu() -> dict:
    """Manager/owner submenu for interactive user management."""
    return BaleClient.reply_keyboard(
        [
            [BTN_USERS_ADD],
            [BTN_USERS_EDIT],
            [BTN_USERS_DELETE],
            [BTN_USERS_LIST],
            [BTN_BACK_USERS],
        ]
    )


def role_menu(include_owner: bool = False) -> dict:
    """Keyboard to pick a role when adding/editing a user."""
    rows: list[list[str]] = []
    if include_owner:
        rows.append([BTN_ROLE_OWNER])
    rows.extend(
        [
            [BTN_ROLE_MANAGER],
            [BTN_ROLE_OFFICER],
            [BTN_ROLE_TECH],
            [BTN_BACK_USERS],
        ]
    )
    return BaleClient.reply_keyboard(rows)


def inventory_menu() -> dict:
    """Submenu under موجودی انبار."""
    rows = [
        [BTN_INV_UPLOAD],
        [BTN_INV_ADD_CATEGORY],
        [BTN_INV_LIST_CATEGORIES],
        [BTN_BACK_MAIN],
    ]
    return BaleClient.reply_keyboard(rows)


def site_stock_menu() -> dict:
    """Submenu: three site-stock groups under موجودی روزانه سایت."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SITE_SLAB],
            [BTN_SITE_BLOOM],
            [BTN_SITE_BILLET],
            [BTN_BACK_MAIN],
        ]
    )


def site_stock_entry_menu() -> dict:
    """While entering quantities one-by-one."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SITE_SKIP],
            [BTN_SITE_CANCEL],
            [BTN_BACK_SITE],
        ]
    )


def catalog_settings_menu() -> dict:
    """Settings for catalog assignment (non-technician)."""
    return BaleClient.reply_keyboard(
        [
            [BTN_CATALOG_LIST],
            [BTN_CATALOG_UNASSIGNED],
            [BTN_CATALOG_SEED],
            [BTN_BACK_MAIN],
        ]
    )


def catalog_assign_menu() -> dict:
    """Choose group after picking an item."""
    return BaleClient.reply_keyboard(
        [
            [BTN_CATALOG_ASSIGN_SLAB],
            [BTN_CATALOG_ASSIGN_BLOOM],
            [BTN_CATALOG_ASSIGN_BILLET],
            [BTN_CATALOG_UNASSIGN],
            [BTN_BACK_CATALOG],
        ]
    )


def analytics_menu() -> dict:
    rows = [
        [BTN_DAILY],
        [BTN_SUGGEST],
        [BTN_PERIOD],
        [BTN_REMAINING],
        [BTN_SURPLUS],
        [BTN_INBOUND],
        [BTN_FORECAST],
        [BTN_MONTHLY_SUMMARY],
        [BTN_ANALYTICS_PDF],
        [BTN_TUNDISH_FILTER],
        [BTN_BACK_MAIN],
    ]
    return BaleClient.reply_keyboard(rows)


def tundish_filter_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_ALL_TUNDISHES],
            [BTN_TUNDISH_SLAB],
            [BTN_TUNDISH_BLOOM],
            [BTN_TUNDISH_BILLET],
            [BTN_BACK_ANALYTICS],
        ]
    )


def date_range_menu() -> dict:
    rows = [
        [BTN_RANGE_TODAY, BTN_RANGE_7, BTN_RANGE_30],
        [BTN_RANGE_CUSTOM],
        [BTN_BACK_ANALYTICS],
    ]
    return BaleClient.reply_keyboard(rows)


def month_year_range_menu(*, include_day_advanced: bool = False) -> dict:
    """Primary month/year presets for analytics and monthly summary."""
    rows = [
        [BTN_MY_CURRENT, BTN_MY_3],
        [BTN_MY_YTD],
        [BTN_MY_CUSTOM, BTN_MY_TYPED],
    ]
    if include_day_advanced:
        rows.append([BTN_MY_DAY_ADV])
    rows.append([BTN_BACK_ANALYTICS])
    return BaleClient.reply_keyboard(rows)


def year_picker_menu(years: list[int]) -> dict:
    """Reply keyboard listing Jalali years (2 per row)."""
    labels = [str(y) for y in years]
    rows: list[list[str]] = []
    for i in range(0, len(labels), 2):
        rows.append(labels[i : i + 2])
    rows.append([BTN_BACK_ANALYTICS])
    return BaleClient.reply_keyboard(rows)


def month_picker_menu() -> dict:
    """Reply keyboard with 12 Persian month names (3 per row)."""
    from bot.jalali import PERSIAN_MONTH_NAMES

    names = [PERSIAN_MONTH_NAMES[i] for i in range(1, 13)]
    rows = [names[i : i + 3] for i in range(0, 12, 3)]
    rows.append([BTN_BACK_ANALYTICS])
    return BaleClient.reply_keyboard(rows)



def material_request_days_menu() -> dict:
    """Coverage-days presets for درخواست مواد (default ۷ روز)."""
    return BaleClient.reply_keyboard(
        [
            [BTN_MR_DAYS_7, BTN_MR_DAYS_14, BTN_MR_DAYS_30],
            [BTN_MR_HISTORY],
            [BTN_MR_CANCEL, BTN_BACK_MAIN],
        ]
    )


def material_request_review_menu() -> dict:
    """Confirm / edit / cancel after suggested list."""
    return BaleClient.reply_keyboard(
        [
            [BTN_MR_CONFIRM_ALL],
            [BTN_MR_EDIT],
            [BTN_MR_CANCEL],
            [BTN_BACK_MAIN],
        ]
    )


def material_request_edit_menu() -> dict:
    """While picking an item to edit."""
    return BaleClient.reply_keyboard(
        [
            [BTN_MR_BACK_REVIEW],
            [BTN_MR_CANCEL],
        ]
    )



def warehouse_return_review_menu() -> dict:
    """Confirm / edit / cancel for برگشت به انبار (shared labels)."""
    return material_request_review_menu()


def warehouse_return_edit_menu() -> dict:
    return material_request_edit_menu()


def cancel_pending_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_CANCEL_PENDING], [BTN_HELP]])



def bot_settings_menu() -> dict:
    """Owner/manager submenu for invite / welcome / logo / letterhead."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SET_INVITE],
            [BTN_SET_WELCOME],
            [BTN_SET_LOGO],
            [BTN_SET_LETTERHEAD],
            [BTN_BACK_MAIN],
        ]
    )


def bot_settings_letterhead_menu() -> dict:
    """Letterhead-specific actions: view / upload PDF / clear."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SETTINGS_VIEW],
            [BTN_SETTINGS_UPLOAD_LETTERHEAD],
            [BTN_SETTINGS_CLEAR_LETTERHEAD],
            [BTN_BACK_BOT_SETTINGS],
        ]
    )


def bot_settings_item_menu(*, include_text: bool = True) -> dict:
    """Per-item settings actions (view / edit text / set-clear image)."""
    rows: list[list[str]] = [[BTN_SETTINGS_VIEW]]
    if include_text:
        rows.append([BTN_SETTINGS_EDIT_TEXT])
    rows.extend(
        [
            [BTN_SETTINGS_SET_IMAGE],
            [BTN_SETTINGS_CLEAR_IMAGE],
            [BTN_BACK_BOT_SETTINGS],
        ]
    )
    return BaleClient.reply_keyboard(rows)


BTN_INVITE_ENTER = "ورود به ربات"


def invite_url_button(url: str) -> dict:
    """Inline URL button for invite deep links (forwardable invite message)."""
    return BaleClient.inline_url_keyboard(BTN_INVITE_ENTER, url)
