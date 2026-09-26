"""Persian reply keyboards for request-driven upload flow and tundish analytics."""
from __future__ import annotations

from config import FILE_TYPES, TUNDISH_TYPES
from bot.bale_api import BaleClient


BTN_TANK = FILE_TYPES["tank_consumption"]["button"]
BTN_INV = FILE_TYPES["product_inventory"]["button"]
BTN_MONTHLY = FILE_TYPES["monthly_consumption"]["button"]
BTN_STATUS = "📋 وضعیت فایل‌ها"
BTN_GENERATE = "✅ تولید گزارش PDF"
BTN_RESET = "🔄 شروع مجدد"
BTN_HELP = "❓ راهنما"
BTN_USERS = "👥 لیست کاربران"
BTN_CANCEL_PENDING = "✖️ انصراف از آپلود"

# Analytics / reports submenu
BTN_ANALYTICS = "📊 گزارش‌ها / تحلیل تاندیش"
BTN_DAILY = "📈 مصرف روزانه مواد"
BTN_SUGGEST = "🛒 پیشنهاد درخواست مواد"
BTN_PERIOD = "📅 گزارش مصرف بازه‌ای"
BTN_REMAINING = "⚠️ موجودی و مواد بحرانی"
BTN_FORECAST = "🔮 پیش‌بینی نیاز تاندیش"
BTN_ANALYTICS_PDF = "📄 PDF کامل تحلیل"
BTN_TUNDISH_FILTER = "🔎 فیلتر نوع تاندیش"
BTN_ALL_TUNDISHES = "همه تاندیش‌ها"
BTN_TUNDISH_SLAB = TUNDISH_TYPES["slab"]
BTN_TUNDISH_BLOOM = TUNDISH_TYPES["bloom"]
BTN_TUNDISH_BILLET = TUNDISH_TYPES["billet"]
BTN_BACK_MAIN = "⬅️ بازگشت به منوی اصلی"

# Date-range presets
BTN_RANGE_TODAY = "امروز"
BTN_RANGE_7 = "۷ روز"
BTN_RANGE_30 = "۳۰ روز"
BTN_RANGE_CUSTOM = "بازه سفارشی"
BTN_BACK_ANALYTICS = "⬅️ بازگشت به تحلیل"


def button_to_file_type(text: str) -> str | None:
    mapping = {
        BTN_TANK: "tank_consumption",
        BTN_INV: "product_inventory",
        BTN_MONTHLY: "monthly_consumption",
        FILE_TYPES["tank_consumption"]["label_fa"]: "tank_consumption",
        FILE_TYPES["product_inventory"]["label_fa"]: "product_inventory",
        FILE_TYPES["monthly_consumption"]["label_fa"]: "monthly_consumption",
    }
    return mapping.get((text or "").strip())


def main_menu(is_manager: bool = False) -> dict:
    rows = [
        [BTN_TANK],
        [BTN_INV],
        [BTN_MONTHLY],
        [BTN_STATUS, BTN_GENERATE],
        [BTN_ANALYTICS],
        [BTN_RESET, BTN_HELP],
    ]
    if is_manager:
        rows.append([BTN_USERS])
    return BaleClient.reply_keyboard(rows)


def analytics_menu() -> dict:
    rows = [
        [BTN_DAILY],
        [BTN_SUGGEST],
        [BTN_PERIOD],
        [BTN_REMAINING],
        [BTN_FORECAST],
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


def cancel_pending_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_CANCEL_PENDING], [BTN_HELP]])
