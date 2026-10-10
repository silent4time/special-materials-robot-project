"""Persian reply keyboards for upload flow, main-source file submenu, site stock, and analytics."""
from __future__ import annotations

from config import ASSISTANT_ENABLED, FILE_TYPES, SITE_STOCK_GROUPS, TUNDISH_TYPES
from bot.bale_api import BaleClient


BTN_TANK = FILE_TYPES["tank_consumption"]["button"]
BTN_INV = FILE_TYPES["product_inventory"]["button"]
BTN_MONTHLY = FILE_TYPES["monthly_consumption"]["button"]
BTN_STATUS = "📋 وضعیت فایل‌ها"
BTN_GENERATE = "📊 گزارش کلی مواد"
BTN_RESET = "🔄 شروع مجدد"
BTN_HELP = "❓ راهنما"
BTN_USERS = "👥 کاربران"
BTN_USERS_ADD = "➕ اضافه کردن کاربر"
BTN_USERS_EDIT = "✏️ اصلاح نقش کاربر"
BTN_USERS_DELETE = "🗑 حذف کاربر"
BTN_USERS_LIST = "📋 لیست کاربران"
BTN_BACK_USERS = "⬅️ بازگشت"
BTN_INVITE_CONFIRM = "✅ تأیید ساخت لینک"
BTN_INVITE_CANCEL = "✖️ انصراف"

# Bot settings (owner/manager)
BTN_BOT_SETTINGS = "⚙️ تنظیمات ربات"
BTN_SET_INVITE = "📝 متن دعوت‌نامه کاربران"
BTN_SET_WELCOME = "👋 پیام خوشامدگویی"
BTN_SET_LOGO = "🖼 لوگوی ربات"
BTN_SET_LETTERHEAD = "📄 سربرگ PDF"
BTN_SETTINGS_UPLOAD_LETTERHEAD = "📄 آپلود سربرگ PDF"
BTN_SETTINGS_CLEAR_LETTERHEAD = "🗑 حذف سربرگ"
BTN_SET_STOCK_GROUP = "📣 گروه گزارش موجودی روزانه"
BTN_SETTINGS_CLEAR_STOCK_GROUP = "🗑 حذف گروه گزارش موجودی"
# یادآور گزارش‌های ورودی الزامی (owner/manager)
BTN_SET_REMINDERS = "🔔 یادآور گزارش‌های الزامی"
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

# آپلود فایل (main-menu section) + فایل منبع اصلی submenu
BTN_UPLOAD_MENU = "📤 آپلود فایل"
BTN_WAREHOUSE_STOCK = "📥 موجودی انبار"  # Excel → product_inventory (refreshes منبع اصلی)
BTN_MAIN_SOURCE_FILE = "📦 فایل منبع اصلی"  # submenu opener, not upload
BTN_BACK_UPLOAD = "⬅️ بازگشت به آپلود فایل"

# منبع اصلی (legacy labels + edit/upload actions)
BTN_INV_MENU = "📦 منبع اصلی"  # legacy → opens main-source submenu
BTN_INV_UPLOAD = "📥 ورود فایل اکسل منبع اصلی"
BTN_INV_EDIT = "✏️ ویرایش منبع اصلی"  # legacy → same flattened main-source menu
BTN_INV_EDIT_RECORD = "✏️ ویرایش رکورد"
BTN_INV_ADD_RECORD = "➕ اضافه کردن رکورد"
BTN_INV_ADD_CATEGORY = "➕ اضافه کردن کد دسته بندی"
BTN_INV_LIST_CATEGORIES = "📋 لیست کدهای دسته بندی"
BTN_INV_DOWNLOAD = "📥 دانلود فایل منبع اصلی (اکسل)"
BTN_BACK_INV_EDIT = "⬅️ بازگشت به ویرایش منبع اصلی"  # legacy → main_source_file_menu

# موجودی روزانه سایت — interactive entry (not Excel primary path)
BTN_SITE_STOCK = BTN_TANK  # «📥 موجودی روزانه سایت»
BTN_SITE_SLAB = SITE_STOCK_GROUPS["slab"]  # موجودی مواد اسلب
BTN_SITE_BLOOM = SITE_STOCK_GROUPS["bloom"]  # موجودی مواد بلوم
BTN_SITE_BILLET = SITE_STOCK_GROUPS["billet"]  # موجودی مواد بیلت
BTN_SITE_SKIP = "⏭ رد کردن این قلم"
BTN_SITE_CANCEL = "✖️ انصراف از ورود موجودی"
BTN_SITE_CONFIRM = "تأیید و ثبت"
BTN_BACK_SITE = "⬅️ بازگشت به گروه‌های سایت"

# تنظیمات اقلام سایت / تخصیص به گروه (non-technician)
BTN_CATALOG_SETTINGS = "⚙️ تنظیمات اقلام سایت / تخصیص به گروه"
BTN_CATALOG_LIST = "📋 لیست اقلام و تخصیص‌ها"
BTN_CATALOG_UNASSIGNED = "📭 اقلام بدون گروه"
BTN_CATALOG_SEED = "🔄 همگام‌سازی از منبع اصلی"
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
BTN_INBOUND = "📥 گزارش اقلام ورودی به انبار"
BTN_INBOUND_LEGACY = "📥 گزارش ورودی به انبار"  # old keyboards still on clients
BTN_CRITICAL_ITEMS = "🚨 اقلام بحرانی"
BTN_CRITICAL_COUNTS = "📝 ثبت تعداد تاندیش ماهانه"
BTN_CRITICAL_REPORT = "📄 تولید گزارش اقلام بحرانی"
BTN_BACK_CRITICAL = "⬅️ بازگشت به اقلام بحرانی"
BTN_MAIN_GOAL = "🎯 گزارش هدف اصلی"
BTN_MG_MENU = BTN_MAIN_GOAL
BTN_MG_START = "📤 آپلود ۴ فایل یک ماه"
BTN_MG_START_LEGACY = "📤 آپلود چهار فایل هدف اصلی"  # old keyboards still on clients
BTN_MG_BULK = "📦 آپلود گروهی چند ماه (تشخیص خودکار)"
BTN_MG_BULK_DONE = "✅ پایان آپلود گروهی"
BTN_MG_HISTORY = "🗂 ماه‌های ذخیره‌شده"
BTN_MG_DELETE = "🗑 حذف یک ماه از سابقه"
BTN_MG_SCN_TARGET = "🎯 سناریو ۱: تناژ هدف"
BTN_MG_SCN_FORECAST = "🔮 سناریو ۲: پیش‌بینی ماه‌های آینده"
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
BTN_MG_RECENT = "📜 آخرین گزارش‌های هدف اصلی"
BTN_MG_CANCEL = "✖️ انصراف از گزارش هدف اصلی"
BTN_MG_SKIP_TARGET = "⏭ بدون تناژ هدف"
BTN_MG_BACK = "⬅️ بازگشت به هدف اصلی"
BTN_MG_INPUTS = "📥 ثبت ورودی (عکس تولید / اکسل تاندیش)"
BTN_MG_INPUT_PROD = "📸 آپلود عکس آمار تولید"
BTN_MG_INPUT_PROD_XLSX = "📄 آپلود اکسل آمار تولید"
BTN_MG_INPUT_BILLET = "📤 اکسل مصرف تاندیش بیلت"
BTN_MG_INPUT_BLOOM = "📤 اکسل مصرف تاندیش بلوم"
BTN_MG_INPUT_SLAB = "📤 اکسل مصرف تاندیش اسلب"
BTN_MG_INPUT_CORRECT = "✏️ اصلاح دستی تولید"
BTN_MG_REPORT_REQ = "📊 درخواست گزارش از سابقه"
BTN_MG_RANGE_3 = "۳ ماهه"
BTN_MG_RANGE_6 = "۶ ماهه"
BTN_MG_RANGE_12 = "یکساله"
BTN_MG_RANGE_CUSTOM = "بازه ورودی کاربر"
BTN_MG_CONFIRM_PARTIAL = "✅ ادامه با دادهٔ موجود"
BTN_USER_ACTIVITY = "📋 گزارش فعالیت کاربران"
BTN_REPORT_ASSISTANT = "🤖 دستیار هوشمند"
BTN_END_ASSISTANT = "پایان گفتگو"
BTN_TUNDISH_FILTER = "🔎 فیلتر نوع تاندیش"
BTN_ALL_TUNDISHES = "همه تاندیش‌ها"
BTN_TUNDISH_SLAB = TUNDISH_TYPES["slab"]
BTN_TUNDISH_BLOOM = TUNDISH_TYPES["bloom"]
BTN_TUNDISH_BILLET = TUNDISH_TYPES["billet"]
BTN_BACK_MAIN = "⬅️ بازگشت به منوی اصلی"
BTN_BACK_PREV = "⬅️ بازگشت به منوی قبل"  # alias for site-stock menus

# Material request workflow (owner / manager / officer)
BTN_MATERIAL_REQUEST = "🛒 درخواست مواد"
BTN_WAREHOUSE_RETURN = "↩️ برگشت به انبار"
BTN_MR_CONFIRM_ALL = "✅ تأیید همه"
BTN_MR_EDIT = "✏️ اصلاح"
BTN_MR_CANCEL = "✖️ انصراف"
BTN_MR_BACK_REVIEW = "⬅️ بازگشت به بررسی"
BTN_MR_HISTORY = "📜 تاریخچه درخواست‌ها"
BTN_MR_DAYS_DEFAULT = "۱ روز (پیش‌فرض)"

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

# Reply/nav button labels that must NOT be parsed as quantities while awaiting site-stock entry
SITE_STOCK_RESERVED_TEXTS = frozenset(
    {
        BTN_SITE_STOCK,
        BTN_SITE_SKIP,
        BTN_SITE_CANCEL,
        BTN_SITE_CONFIRM,
        BTN_BACK_SITE,
        BTN_BACK_MAIN,
        BTN_BACK_PREV,
        *SITE_GROUP_BUTTONS.keys(),
    }
)

# Cancel / help / back labels that must NOT be parsed as category codes or record fields.
# Includes typed aliases: prompts may quote « انصراف از آپلود» / «بازگشت به منو» without emoji.
PENDING_CANCEL_TEXTS = frozenset(
    {
        BTN_CANCEL_PENDING,
        "انصراف از آپلود",
    }
)
PENDING_INPUT_RESERVED_TEXTS = frozenset(
    {
        BTN_CANCEL_PENDING,
        BTN_HELP,
        BTN_BACK_MAIN,
        BTN_BACK_PREV,
        BTN_BACK_UPLOAD,
        BTN_RESET,
        *PENDING_CANCEL_TEXTS,
        "بازگشت به منو",
        "بازگشت به منوی اصلی",
        "راهنما",
    }
)


def normalize_pending_text(text: str | None) -> str:
    """Strip so prompt-quoted labels (leading space) match keyboard buttons."""
    return (text or "").strip()


def is_pending_cancel_text(text: str | None) -> bool:
    return normalize_pending_text(text) in PENDING_CANCEL_TEXTS


def is_pending_reserved_text(text: str | None) -> bool:
    return normalize_pending_text(text) in PENDING_INPUT_RESERVED_TEXTS


ASSIGN_GROUP_BUTTONS = {
    BTN_CATALOG_ASSIGN_SLAB: "slab",
    BTN_CATALOG_ASSIGN_BLOOM: "bloom",
    BTN_CATALOG_ASSIGN_BILLET: "billet",
}


def button_to_file_type(text: str) -> str | None:
    """Map upload buttons to file types.

    Note: BTN_TANK / موجودی روزانه سایت is **interactive entry** now and is
    intentionally NOT mapped here (handled by site-stock submenu).
    BTN_WAREHOUSE_STOCK / BTN_INV / BTN_INV_UPLOAD all select product_inventory
    upload. Submenu openers (BTN_UPLOAD_MENU, BTN_MAIN_SOURCE_FILE, BTN_INV_MENU)
    are intentionally NOT mapped.
    """
    mapping = {
        BTN_WAREHOUSE_STOCK: "product_inventory",
        BTN_INV: "product_inventory",
        BTN_INV_UPLOAD: "product_inventory",
        BTN_MONTHLY: "monthly_consumption",
        FILE_TYPES["product_inventory"]["label_fa"]: "product_inventory",
        FILE_TYPES["monthly_consumption"]["label_fa"]: "monthly_consumption",
        # legacy button texts (pre-redesign / pre-rename) — monthly / warehouse only
        "📥 موجودی محصولات": "product_inventory",
        "📥 موجودی انبار": "product_inventory",
        "📦 موجودی انبار": "product_inventory",
        "📥 مصرف ماهانه مواد": "monthly_consumption",
        # Consumable materials uploads that use the warehouse cleaned format
        # still refresh منبع اصلی (see handlers.on_document + looks_like_product_inventory).
        "📥 مواد مصرفی": "product_inventory",
        "📦 مواد مصرفی": "product_inventory",
        "مواد مصرفی": "product_inventory",
        # legacy short upload label (pre file-entry menu wording)
        "📥 ورود فایل اکسل": "product_inventory",
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
        return BaleClient.reply_keyboard([[BTN_SITE_STOCK], [BTN_TR_MENU], [BTN_HELP]])

    is_manager = bool(user) if isinstance(user, bool) else role in {"owner", "manager"}
    rows = [
        [BTN_UPLOAD_MENU],
        [BTN_SITE_STOCK],
        [BTN_TR_MENU],
        [BTN_CATALOG_SETTINGS],
        [BTN_MATERIAL_REQUEST, BTN_WAREHOUSE_RETURN],
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


def invite_confirm_menu() -> dict:
    """Confirm/cancel before creating a role invite link."""
    return BaleClient.reply_keyboard(
        [
            [BTN_INVITE_CONFIRM],
            [BTN_INVITE_CANCEL],
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


def upload_files_menu() -> dict:
    """Main-menu section «آپلود فایل»: warehouse / monthly / main-source file."""
    return BaleClient.reply_keyboard(
        [
            [BTN_WAREHOUSE_STOCK],
            [BTN_MONTHLY],
            [BTN_MAIN_SOURCE_FILE],
            [BTN_BACK_MAIN],
        ]
    )


def main_source_file_menu() -> dict:
    """Flattened «فایل منبع اصلی» submenu (Excel + download + add/edit + categories)."""
    return BaleClient.reply_keyboard(
        [
            [BTN_INV_UPLOAD],
            [BTN_INV_DOWNLOAD],
            [BTN_INV_ADD_RECORD],
            [BTN_INV_EDIT_RECORD],
            [BTN_INV_ADD_CATEGORY],
            [BTN_INV_LIST_CATEGORIES],
            [BTN_BACK_UPLOAD],
        ]
    )


def inventory_menu() -> dict:
    """Alias of main_source_file_menu (legacy name)."""
    return main_source_file_menu()


def file_entry_menu() -> dict:
    """Upload picker after document-without-pending (no site stock)."""
    return upload_files_menu()


def inventory_edit_menu() -> dict:
    """Alias — edit actions are flattened into main_source_file_menu."""
    return main_source_file_menu()


def site_stock_menu() -> dict:
    """Submenu: three site-stock groups under موجودی روزانه سایت."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SITE_SLAB],
            [BTN_SITE_BLOOM],
            [BTN_SITE_BILLET],
            [BTN_BACK_PREV],  # «بازگشت به منوی قبل» → main_menu
        ]
    )


def site_stock_entry_menu() -> dict:
    """Reply keyboard while the inline site-stock editor is open (guided + edits)."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SITE_SKIP],
            [BTN_SITE_CANCEL],
            [BTN_BACK_SITE],
        ]
    )


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
        [BTN_CRITICAL_ITEMS],
        [BTN_MAIN_GOAL],
        [BTN_SURPLUS],
        [BTN_INBOUND],
        [BTN_FORECAST],
        [BTN_MONTHLY_SUMMARY],
        [BTN_USER_ACTIVITY],
        [BTN_GENERATE],
        [BTN_ANALYTICS_PDF],
        [BTN_TUNDISH_FILTER],
        [BTN_BACK_MAIN],
    ]
    # دستیار هوشمند — only when ASSISTANT_ENABLED=1 (default off)
    if ASSISTANT_ENABLED:
        # Insert before PDF full analytics
        pdf_idx = next(i for i, r in enumerate(rows) if r == [BTN_ANALYTICS_PDF])
        rows.insert(pdf_idx, [BTN_REPORT_ASSISTANT])
    return BaleClient.reply_keyboard(rows)



def inbound_history_menu(labels: list[str] | None = None) -> dict:
    """Earlier stored inbound reports (one button per stock upload) + back."""
    rows = [[lbl] for lbl in (labels or [])]
    rows.append([BTN_BACK_ANALYTICS])
    return BaleClient.reply_keyboard(rows)


def critical_items_menu() -> dict:
    """Submenu under گزارش‌ها for اقلام بحرانی (owner/manager/officer)."""
    return BaleClient.reply_keyboard(
        [
            [BTN_CRITICAL_COUNTS],
            [BTN_CRITICAL_REPORT],
            [BTN_BACK_ANALYTICS],
        ]
    )


BTN_CRITICAL_RENO_WITH = "با نوسازی"
BTN_CRITICAL_RENO_WITHOUT = "بدون نوسازی"
CB_CRITICAL_RENO_PREFIX = "ci|reno|"


def critical_reno_inline_keyboard() -> dict:
    """Inline choice «با نوسازی» / «بدون نوسازی» before building اقلام بحرانی."""
    return BaleClient.inline_keyboard(
        [
            [
                {"text": f"🔧 {BTN_CRITICAL_RENO_WITH}", "callback_data": f"{CB_CRITICAL_RENO_PREFIX}with"},
                {"text": f"🩹 {BTN_CRITICAL_RENO_WITHOUT}", "callback_data": f"{CB_CRITICAL_RENO_PREFIX}without"},
            ]
        ]
    )


def main_goal_menu() -> dict:
    """Submenu under گزارش‌ها for گزارش هدف اصلی (owner/manager/officer)."""
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_INPUTS],
            [BTN_MG_REPORT_REQ],
            [BTN_MG_START],
            [BTN_MG_BULK],
            [BTN_MG_HISTORY],
            [BTN_MG_SCN_TARGET],
            [BTN_MG_SCN_FORECAST],
            [BTN_MG_RECENT],
            [BTN_BACK_ANALYTICS],
        ]
    )


def main_goal_inputs_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_INPUT_PROD],
            [BTN_MG_INPUT_PROD_XLSX],
            [BTN_MG_INPUT_BILLET],
            [BTN_MG_INPUT_BLOOM],
            [BTN_MG_INPUT_SLAB],
            [BTN_MG_INPUT_CORRECT],
            [BTN_MG_HISTORY],
            [BTN_MG_BACK],
        ]
    )


def main_goal_range_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_RANGE_3, BTN_MG_RANGE_6],
            [BTN_MG_RANGE_12],
            [BTN_MG_RANGE_CUSTOM],
            [BTN_MG_CANCEL],
            [BTN_MG_BACK],
        ]
    )


def main_goal_partial_confirm_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_CONFIRM_PARTIAL],
            [BTN_MG_CANCEL],
            [BTN_MG_BACK],
        ]
    )


def main_goal_upload_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_CANCEL],
            [BTN_MG_BACK],
            [BTN_BACK_ANALYTICS],
        ]
    )


def main_goal_bulk_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_BULK_DONE],
            [BTN_MG_CANCEL],
        ]
    )


def main_goal_history_menu(*, can_delete: bool = False) -> dict:
    rows = [[BTN_MG_START], [BTN_MG_BULK]]
    if can_delete:
        rows.append([BTN_MG_DELETE])
    rows.append([BTN_MG_BACK])
    return BaleClient.reply_keyboard(rows)


def main_goal_period_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_P1, BTN_MG_P3],
            [BTN_MG_P6, BTN_MG_P12],
            [BTN_MG_CANCEL],
        ]
    )


def main_goal_section_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_SEC_BILLET, BTN_MG_SEC_BLOOM, BTN_MG_SEC_SLAB],
            [BTN_MG_SEC_TOTAL],
            [BTN_MG_CANCEL],
        ]
    )


def main_goal_targets_confirm_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_COMPUTE],
            [BTN_MG_ADD_SECTION],
            [BTN_MG_CANCEL],
        ]
    )


def main_goal_horizon_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_P3, BTN_MG_P6, BTN_MG_P12],
            [BTN_MG_CANCEL],
        ]
    )


def main_goal_cancel_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_MG_CANCEL]])


def main_goal_target_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_MG_SKIP_TARGET],
            [BTN_MG_CANCEL],
            [BTN_MG_BACK],
        ]
    )


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
    """Coverage days for درخواست مواد: type a number or use default ۱ روز."""
    return BaleClient.reply_keyboard(
        [
            [BTN_MR_DAYS_DEFAULT],
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



def report_assistant_menu() -> dict:
    """Conversation mode for local report-only assistant."""
    return BaleClient.reply_keyboard(
        [
            [BTN_END_ASSISTANT],
            [BTN_BACK_ANALYTICS],
        ]
    )


def cancel_pending_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_CANCEL_PENDING], [BTN_HELP]])



def bot_settings_menu() -> dict:
    """Owner/manager submenu for invite / welcome / logo / letterhead / stock group."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SET_INVITE],
            [BTN_SET_WELCOME],
            [BTN_SET_LOGO],
            [BTN_SET_LETTERHEAD],
            [BTN_SET_STOCK_GROUP],
            [BTN_SET_REMINDERS],
            [BTN_TR_SETTINGS],
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



def bot_settings_stock_group_menu() -> dict:
    """View / clear the Bale group used for daily site-stock reports."""
    return BaleClient.reply_keyboard(
        [
            [BTN_SETTINGS_VIEW],
            [BTN_SETTINGS_CLEAR_STOCK_GROUP],
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


def reminder_settings_menu(enabled: bool) -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_RM_STATUS],
            [BTN_RM_DISABLE if enabled else BTN_RM_ENABLE],
            [BTN_RM_ROLES, BTN_RM_USERS],
            [BTN_RM_SCHEDULE],
            [BTN_RM_SEND_NOW],
            [BTN_BACK_BOT_SETTINGS],
        ]
    )


def reminder_roles_menu(selected: list[str] | set[str]) -> dict:
    from config import ROLES

    sel = set(selected or [])
    rows = [
        [(RM_CHECK_ON if key in sel else RM_CHECK_OFF) + label]
        for key, label in ROLES.items()
    ]
    rows.append([BTN_RM_BACK])
    return BaleClient.reply_keyboard(rows)


def reminder_back_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_RM_BACK]])


BTN_INVITE_ENTER = "ورود به ربات"


def invite_url_button(url: str) -> dict:
    """Inline URL button for invite deep links (forwardable invite message)."""
    return BaleClient.inline_url_keyboard(BTN_INVITE_ENTER, url)


# ---------------------------------------------------------------------------
# گزارش تاندیش بعد از ریخته‌گری (all roles enter; owner/manager configure)
# ---------------------------------------------------------------------------
BTN_TR_MENU = "🧾 گزارش تاندیش بعد از ریخته‌گری"
BTN_TR_SECTION = {
    "slab": "🧾 گزارش تاندیش اسلب",
    "bloom": "🧾 گزارش تاندیش بلوم",
    "billet": "🧾 گزارش تاندیش بیلت",
}
TR_SECTION_BUTTONS = {v: k for k, v in BTN_TR_SECTION.items()}
BTN_TR_RECENT = "📜 آخرین گزارش‌های تاندیش"
BTN_TR_SETTINGS = "⚙️ تنظیمات گزارش تاندیش"
BTN_TR_BACK = "⬅️ بازگشت به گزارش تاندیش"
BTN_TR_MODE_STEP = "✍️ ورود مرحله‌ای"
BTN_TR_MODE_TEXT = "📋 ورود سریع از متن"
BTN_TR_SKIP = "⏭ رد کردن (اختیاری)"
BTN_TR_PREV = "↩️ مرحله قبل"
BTN_TR_CANCEL = "✖️ انصراف از گزارش تاندیش"
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
BTN_TRS_BACK = "⬅️ بازگشت به تنظیمات گزارش تاندیش"
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
    """Submenu: three sections + recent + (owner/manager) settings."""
    role = (user or {}).get("role")
    rows = [
        [BTN_TR_SECTION["slab"]],
        [BTN_TR_SECTION["bloom"]],
        [BTN_TR_SECTION["billet"]],
        [BTN_TR_RECENT],
    ]
    if role in {"owner", "manager"}:
        rows.append([BTN_TR_SETTINGS])
    rows.append([BTN_BACK_MAIN])
    return BaleClient.reply_keyboard(rows)


def tundish_report_mode_menu() -> dict:
    return BaleClient.reply_keyboard(
        [[BTN_TR_MODE_STEP], [BTN_TR_MODE_TEXT], [BTN_TR_CANCEL]]
    )


def tundish_report_step_menu(options: list[str] | None = None, *, optional: bool = False, can_prev: bool = True) -> dict:
    rows: list[list[str]] = []
    opts = [_truncate_btn(o) for o in (options or [])]
    # two buttons per row for short options, one per row for long ones
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
    nav = [BTN_TR_PREV, BTN_TR_CANCEL] if can_prev else [BTN_TR_CANCEL]
    rows.append(nav)
    return BaleClient.reply_keyboard(rows)


def tundish_report_confirm_menu() -> dict:
    return BaleClient.reply_keyboard(
        [[BTN_TR_CONFIRM], [BTN_TR_EDIT], [BTN_TR_CANCEL]]
    )


def tundish_report_settings_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_TRS_SECTION["slab"]],
            [BTN_TRS_SECTION["bloom"]],
            [BTN_TRS_SECTION["billet"]],
            [BTN_TR_BACK],
        ]
    )


def tundish_report_section_settings_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_TRS_ADD],
            [BTN_TRS_EDIT, BTN_TRS_DELETE],
            [BTN_TRS_LINES],
            [BTN_TRS_BACK],
        ]
    )


def tundish_report_item_edit_menu() -> dict:
    return BaleClient.reply_keyboard(
        [
            [BTN_TRS_E_LABEL, BTN_TRS_E_TYPE],
            [BTN_TRS_E_OPTIONS, BTN_TRS_E_REQUIRED],
            [BTN_TRS_E_ORDER],
            [BTN_TRS_BACK_SECTION],
        ]
    )


def tundish_report_type_menu() -> dict:
    return BaleClient.reply_keyboard(
        [[BTN_TRS_TYPE_CHOICE, BTN_TRS_TYPE_NUMBER, BTN_TRS_TYPE_TEXT], [BTN_TRS_BACK_SECTION]]
    )


def tundish_report_required_menu() -> dict:
    return BaleClient.reply_keyboard(
        [[BTN_TRS_REQUIRED, BTN_TRS_OPTIONAL], [BTN_TRS_BACK_SECTION]]
    )


def tundish_report_back_section_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_TRS_BACK_SECTION]])


def tundish_report_delete_confirm_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_TRS_DELETE_CONFIRM], [BTN_TRS_BACK_SECTION]])
