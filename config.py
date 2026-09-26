"""Application configuration loaded from environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()
ADMIN_BALE_USER_ID = os.getenv("ADMIN_BALE_USER_ID", "").strip()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "bot.db")))
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
FONTS_DIR = BASE_DIR / "fonts"
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT", "25"))
BALE_API_BASE = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}" if BALE_BOT_TOKEN else ""

# File type keys used across bot / excel / pdf / db
FILE_TYPES = {
    "tank_consumption": {
        "key": "tank_consumption",
        "label_fa": "مقدار مصرفی هر تانک",
        "button": "📥 مقدار مصرفی هر تانک",
        "filename_hint": "tank_consumption",
    },
    "product_inventory": {
        "key": "product_inventory",
        "label_fa": "موجودی محصولات",
        "button": "📥 موجودی محصولات",
        "filename_hint": "product_inventory",
    },
    "monthly_consumption": {
        "key": "monthly_consumption",
        "label_fa": "مصرف ماهانه مواد",
        "button": "📥 مصرف ماهانه مواد",
        "filename_hint": "monthly_consumption",
    },
}

ROLES = {
    "manager": "مدیر",
    "responsible_officer": "کاردان مسئول",
    "technician": "تکنسین",
}

REQUIRED_COLUMNS = {
    "tank_consumption": [
        "domain",
        "assignee_id",
        "assignee_name",
        "tank_id",
        "material_name",
        "quantity",
        "unit",
        "date",
        "notes",
    ],
    "product_inventory": [
        "domain",
        "assignee_id",
        "assignee_name",
        "product_name",
        "quantity",
        "unit",
        "location",
        "date",
        "notes",
    ],
    "monthly_consumption": [
        "domain",
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
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
