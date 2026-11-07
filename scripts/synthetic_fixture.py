"""Isolated representative database for smokes that need an owner and report data.

Synthetic rows only (no copy of data/bot.db, no tokens). Inventory covers the three
tundish sections so site lists are non-empty. Monthly consumption is Tir and
Shahrivar 1405, so «ماه جاری» in Mehr/Aban has no data and still has a latest month.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

OWNER_ID = "901"


def _inventory() -> pd.DataFrame:
    zero = {
        "work_order": "",
        "casting_floor": 0,
        "billet_renovation": 0,
        "billet_patching": 0,
        "bloom_renovation": 0,
        "bloom_patching": 0,
        "slab_renovation": 0,
        "slab_patching": 0,
        "rate_group": "",
        "shared": "خیر",
        "critical_point": 10,
        "origin": "داخلی",
    }
    rows = [
        dict(
            category_code="1203", id="378112341203A", product_name="بتن بیلت نمونه",
            keyword="بتن بیلت", usage_location="بیلت", quantity=120, priority=1,
            contractor_or_company="شرکت", unit="Kg",
            **{**zero, "billet_renovation": 4, "billet_patching": 2},
        ),
        dict(
            category_code="1655", id="378800001655B", product_name="روکش بلوم نمونه",
            keyword="روکش بلوم", usage_location="بلوم", quantity=80, priority=1,
            contractor_or_company="پیمانکار", unit="Kg",
            **{**zero, "bloom_renovation": 3, "bloom_patching": 1},
        ),
        dict(
            category_code="1207", id="378700001207C", product_name="بتن اسلب نمونه",
            keyword="بتن اسلب", usage_location="اسلب", quantity=90, priority=1,
            contractor_or_company="پیمانکار", unit="Kg",
            **{**zero, "slab_renovation": 5, "slab_patching": 2},
        ),
    ]
    return pd.DataFrame(rows)


def _monthly() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "domain": "1203", "tundish_type": "تاندیش بیلت", "assignee_id": "",
            "assignee_name": "", "material_name": "بتن بیلت", "month": "1405-04",
            "quantity": 12, "unit": "Kg", "status": "", "notes": "",
            "category_code": "1203", "id": "378112341203A", "coefficient": 1,
            "work_order": "", "date": "1405-04-15", "request_return": "",
            "description": "نمونه",
        },
        {
            "domain": "1207", "tundish_type": "تاندیش اسلب", "assignee_id": "",
            "assignee_name": "", "material_name": "بتن اسلب", "month": "1405-06",
            "quantity": 8, "unit": "Kg", "status": "", "notes": "",
            "category_code": "1207", "id": "378700001207C", "coefficient": 1,
            "work_order": "", "date": "1405-06-10", "request_return": "",
            "description": "نمونه",
        },
    ])


def build_synthetic_db(path: Path):
    """Create ``path`` and fill it. Returns the open Database."""
    from config import UPLOAD_DIR
    from db.models import Database
    from excel.processor import write_clean_excel
    from services.main_source import persist_primary_frame
    from services.site_stock_lists import items_for_group

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    db.upsert_user(OWNER_ID, role="owner", display_name="مالک نمونه")
    db.upsert_user("902", role="manager", display_name="مدیر نمونه")
    persist_primary_frame(db, _inventory(), bale_user_id=OWNER_ID)
    for group in ("slab", "bloom", "billet"):
        if not items_for_group(db, group):
            raise RuntimeError(f"synthetic site list empty: {group}")

    session = db.get_or_create_session(OWNER_ID)
    dest = UPLOAD_DIR / OWNER_ID / str(session["id"]) / "cleaned" / "monthly_consumption.xlsx"
    frame = _monthly()
    write_clean_excel(frame, dest, "monthly_consumption")
    db.save_extracted(
        bale_user_id=OWNER_ID,
        session_id=int(session["id"]),
        file_type="monthly_consumption",
        raw_path=str(dest),
        clean_path=str(dest),
        row_count=int(len(frame)),
        columns=list(frame.columns),
    )
    db.store_file_slot(OWNER_ID, "monthly_consumption", str(dest))
    _seed_tonnage(db)
    return db


def _seed_tonnage(db) -> None:
    """Three months with casting tonnage so هدف اصلی scenario navigation has history."""
    for month, slab, bloom, billet in ((4, 1000, 800, 1200), (5, 1100, 900, 1300), (6, 1050, 850, 1250)):
        total = slab + bloom + billet
        db.upsert_main_goal_production(
            period_key=f"m:1405-{month:02d}",
            period_label=f"1405/{month:02d}",
            year=1405,
            month=month,
            sort_key=f"1405-{month:02d}",
            slab_tons=slab,
            bloom_tons=bloom,
            billet_tons=billet,
            total_tons=total,
            bale_user_id=OWNER_ID,
            actor_display_name="مالک نمونه",
            source="synthetic",
            source_type="manual",
            report_tab="casting",
            source_status="recorded",
            created_at_tehran="2026-09-01 12:00",
            jalali_date="1405/06/10",
        )
