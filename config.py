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
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
BOT_ASSETS_DIR = BASE_DIR / "data" / "bot_assets"
REPORT_DIR = Path(os.getenv("REPORT_DIR", str(BASE_DIR / "reports")))
FONTS_DIR = BASE_DIR / "fonts"
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT", "25"))
# Materials with days_of_cover = remaining / avg_daily below this are critical
CRITICAL_DAYS = float(os.getenv("CRITICAL_DAYS", "3"))
# اقلام بحرانی — a merged rate cell crossing several 4-digit codes (e.g. one
# nozzle rate over 1710/1712/…/1718): "pooled" = ONE shared need for the group
# (each code keeps its own stock row; group subtotal row = combined stock vs
# shared need). "per_code" = every code gets the full rate separately.
CRITICAL_SHARED_MERGE_MODE = "pooled"
# Surplus: days_of_cover above this threshold (also max(CRITICAL_DAYS*3, 10))
SURPLUS_COVER_DAYS = float(os.getenv("SURPLUS_COVER_DAYS", "10"))
SURPLUS_FORECAST_DAYS = float(os.getenv("SURPLUS_FORECAST_DAYS", "30"))
# Bale group chat_id for auto site-stock reports (negative id). DB setting overrides this.
SITE_STOCK_REPORT_GROUP_ID = os.getenv("SITE_STOCK_REPORT_GROUP_ID", "").strip()
BALE_API_BASE = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}" if BALE_BOT_TOKEN else ""

# Web dashboard (separate process; does not require BALE_BOT_TOKEN)
WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "").strip() or "change-me-in-production"
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
WEB_ADMIN_USERNAME = os.getenv("WEB_ADMIN_USERNAME", "").strip()
WEB_ADMIN_PASSWORD = os.getenv("WEB_ADMIN_PASSWORD", "").strip()

# دستیار هوشمند — local report-only (Ollama or compatible — no cloud LLM)
# Default OFF: menu hidden; /assistant replies «فعلاً غیرفعال» unless explicitly enabled.
ASSISTANT_ENABLED = os.getenv("ASSISTANT_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b"
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "60"))

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
        "label_fa": "منبع اصلی",
        "button": "📥 منبع اصلی",
        "filename_hint": "warehouse_inventory",
    },
    "monthly_consumption": {
        "key": "monthly_consumption",
        "label_fa": "مصرف ماهیانه مواد",
        "button": "📥 مصرف ماهیانه مواد",
        "filename_hint": "monthly_consumption",
    },
}


def clean_excel_sheet_name(file_type: str) -> str:
    """User-facing Excel sheet title for cleaned extracts (max 31 chars).

    منبع اصلی is stored and re-exported as the single sheet «ریز اطلاعات».
    Category-summary sheets (کل موجودی and empty extras) are never written.
    """
    if file_type == "product_inventory":
        return "ریز اطلاعات"
    label = FILE_TYPES.get(file_type, {}).get("label_fa") or "Sheet1"
    return str(label)[:31]

ROLES = {
    "owner": "مالک",
    "manager": "مدیر",
    "responsible_officer": "کاردان مسئول",
    "technician": "تکنسین",
    "shift_supervisor": "مسئول شیفت",
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
SITE_STOCK_TUNDISH_GROUP_KEYS = SITE_STOCK_GROUP_KEYS
# Legacy keys of the short-lived separate «سطح ریخته گری» groups (1b934b5, removed by
# user decision 1405-07-18). Not offered in bot/web; the DB CHECK still accepts them
# so the earlier schema upgrade stays harmless and any old rows remain readable.
SITE_STOCK_LEGACY_GROUP_KEYS = ("cast_slab", "cast_bloom", "cast_billet")
# SQL list for CHECK(tundish_group IN (...)) on site-stock tables (order stable).
SITE_STOCK_GROUP_SQL = ",".join(
    f"'{k}'" for k in (*SITE_STOCK_GROUPS, *SITE_STOCK_LEGACY_GROUP_KEYS)
)
# Roles that may configure catalog / group assignments (not technicians)
CATALOG_ADMIN_ROLES = frozenset({"owner", "manager", "responsible_officer"})

# Files that still require domain/assignee for RBAC row filtering
RBAC_SCOPED_FILE_TYPES = frozenset({"tank_consumption", "monthly_consumption"})

# Reserved: warehouse category 1800 = surplus items (اقلام مازاد).
# Always kept on inventory upload and always flagged in surplus_materials.
SURPLUS_CATEGORY_CODE = "1800"
SURPLUS_CATEGORY_LABEL = "اقلام مازاد"

# Optional labels applied when seeding DEFAULT_CATEGORY_CODES.
DEFAULT_CATEGORY_LABELS: dict[str, str] = {
    SURPLUS_CATEGORY_CODE: SURPLUS_CATEGORY_LABEL,
}

# Default warehouse category allowlist (inventory ∩ monthly real samples).
# Seeded on DB bootstrap; users can still add more via the bot menu.
# Includes SURPLUS_CATEGORY_CODE so 1800 rows are not dropped as wrong_category.
DEFAULT_CATEGORY_CODES: list[str] = [
    "1203",
    "1207",
    "1237",
    "1271",
    "1272",
    "1274",
    "1450",
    "1451",
    "1473",
    "1581",
    "1603",
    "1604",
    "1605",
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
    "1668",
    "1670",
    "1672",
    "1674",
    "1676",
    "1678",
    "1710",
    "1712",
    "1714",
    "1716",
    "1717",
    "1718",
    "1721",
    "1722",
    "1723",
    "1724",
    "1725",
    "1726",
    "1727",
    "1745",
    "1746",
    SURPLUS_CATEGORY_CODE,
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
    # منبع اصلی: sheet «ریز اطلاعات» only. Legacy 3/7-col uploads still load;
    # columns they lack stay blank. Newer template columns are stored as-is
    # (no business rules for نوسازی / پچینگ / نقطه بحرانی).
    "product_inventory": [
        "category_code",
        "id",
        "product_name",
        "work_order",
        "usage_location",
        "keyword",
        "quantity",
        "priority",
        "contractor_or_company",
        "origin",
        "shared",
        "critical_point",
        "unit",
        "casting_floor",  # سطح ریخته گری (was other_areas / سایر نواحی)
        "billet_renovation",
        "billet_patching",
        "bloom_renovation",
        "bloom_patching",
        "slab_renovation",
        "slab_patching",
        # Importer-written: shared-need group of a merge crossing several codes.
        "rate_group",
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
        # Retained for monthly summary (soft-missing → empty); backward-compatible
        "category_code",
        "id",
        "coefficient",
        "work_order",
        "date",
        "request_return",
        "description",
    ],
}


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    BOT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
