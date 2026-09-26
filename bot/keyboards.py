"""Persian reply keyboards for request-driven upload flow."""
from __future__ import annotations

from config import FILE_TYPES
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
        [BTN_RESET, BTN_HELP],
    ]
    if is_manager:
        rows.append([BTN_USERS])
    return BaleClient.reply_keyboard(rows)


def cancel_pending_menu() -> dict:
    return BaleClient.reply_keyboard([[BTN_CANCEL_PENDING], [BTN_HELP]])
