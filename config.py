"""Application configuration loaded from environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()
ADMIN_BALE_USER_ID = os.getenv("ADMIN_BALE_USER_ID", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "nasoz_bot").strip().lstrip("@")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "bot.db")))
UPLOAD_DIR = BASE_DIR / "uploads"
BOT_ASSETS_DIR = BASE_DIR / "data" / "bot_assets"
REPORT_DIR = BASE_DIR / "reports"
FONTS_DIR = BASE_DIR / "fonts"
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT", "25"))
# Materials with days_of_cover = remaining / avg_daily below this are critical
CRITICAL_DAYS = float(os.getenv("CRITICAL_DAYS", "3"))
# Surplus: days_of_cover above this threshold (also max(CRITICAL_DAYS*3, 10))
SURPLUS_COVER_DAYS = float(os.getenv("SURPLUS_COVER_DAYS", "10"))
SURPLUS_FORECAST_DAYS = float(os.getenv("SURPLUS_FORECAST_DAYS", "30"))
BALE_API_BASE = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}" if BALE_BOT_TOKEN else ""

# File type keys used across bot / excel / pdf / db
# Internal keys kept stable for session slots; Persian labels updated for UI.
FILE_TYPES = {
    "tank_consumption": {
        "key": "tank_consumption",
        "label_fa": "موجودی روزانه سایت",
        "button": "📥 موجودی روزانه سایت",
        "filename_hint": "daily_site_stock",
    },
    "product_inventory": {
        "key": "product_inventory",
        "label_fa": "موجودی انبار",
        "button": "📥 موجودی انبار",
        "filename_hint": "warehouse_inventory",
    },
    "monthly_consumption": {
        "key": "monthly_consumption",
        "label_fa": "مصرف ماهیانه مواد",
        "button": "📥 مصرف ماهیانه مواد",
        "filename_hint": "monthly_consumption",
    },
}

ROLES = {
    "owner": "مالک",
    "manager": "مدیر",
    "responsible_officer": "کاردان مسئول",
    "technician": "تکنسین",
}

# Roles that can manage users and see full admin menus
ADMIN_ROLES = frozenset({"owner", "manager"})
# Roles that see all report rows (plant-wide; no domain/scope filter)
FULL_DATA_ROLES = frozenset({"owner", "manager", "responsible_officer"})


TUNDISH_TYPES = {
    "slab": "تاندیش اسلب",
    "bloom": "تاندیش بلوم",
    "billet": "تاندیش بیلت",
}
TUNDISH_TYPE_LABELS = list(TUNDISH_TYPES.values())

# Daily site stock groups (موجودی روزانه سایت) — technician entry sections
SITE_STOCK_GROUPS = {
    "slab": "موجودی مواد اسلب",
    "bloom": "موجودی مواد بلوم",
    "billet": "موجودی مواد بیلت",
}
SITE_STOCK_GROUP_KEYS = frozenset(SITE_STOCK_GROUPS.keys())
# Roles that may configure catalog / group assignments (not technicians)
CATALOG_ADMIN_ROLES = frozenset({"owner", "manager", "responsible_officer"})

# Files that still require domain/assignee for RBAC row filtering
RBAC_SCOPED_FILE_TYPES = frozenset({"tank_consumption", "monthly_consumption"})

# Default warehouse category allowlist (inventory ∩ monthly real samples).
# Seeded on DB bootstrap; users can still add more via the bot menu.
DEFAULT_CATEGORY_CODES: list[str] = [
    "1203",
    "1207",
    "1274",
    "1451",
    "1473",
    "1581",
    "1603",
    "1604",
    "1623",
    "1626",
    "1628",
    "1633",
    "1634",
    "1637",
    "1638",
    "1640",
    "1643",
    "1655",
    "1656",
    "1658",
    "1662",
    "1664",
    "1667",
    "1672",
    "1674",
    "1676",
    "1712",
    "1714",
    "1716",
]

REQUIRED_COLUMNS = {
    "tank_consumption": [
        "domain",
        "tundish_type",
        "assignee_id",
        "assignee_name",
        "tundish_id",
        "material_name",
        "quantity",
        "unit",
        "date",
        "notes",
    ],
    # Warehouse inventory (موجودی انبار): 3 raw columns + extracted id/priority
    "product_inventory": [
        "category_code",
        "id",
        "item_code_desc",
        "product_name",
        "quantity",
        "priority",
    ],
    "monthly_consumption": [
        "domain",
        "tundish_type",
        "assignee_id",
        "assignee_name",
        "material_name",
        "month",
        "quantity",
        "unit",
        "status",
        "notes",
    ],
}


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    BOT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
