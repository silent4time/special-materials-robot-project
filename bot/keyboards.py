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
BTN_INBOUND = "📥 گزارش ورودی به انبار"
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
        return BaleClient.reply_keyboard([[BTN_SITE_STOCK], [BTN_HELP]])

    is_manager = bool(user) if isinstance(user, bool) else role in {"owner", "manager"}
    rows = [
        [BTN_UPLOAD_MENU],
        [BTN_SITE_STOCK],
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
