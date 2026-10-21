#!/usr/bin/env python3
"""Smoke (temp DB only): report units (واحد) come from منبع اصلی by ID / 4-digit code;
input-file unit only for items missing from the main source. The plant export's «SH»
for 378471411121U / 378700009162X must show «No» (main source); files untouched."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import pandas as pd

    from analytics.tundish import daily_rates
    from services.unit_source import apply_units, maps_from_inventory

    inv = pd.DataFrame([
        {"category_code": "1473", "id": "378471411121U", "unit": "No"},
        {"category_code": "1473", "id": "378700009162X", "unit": "No"},
        {"category_code": "1603", "id": "378721562302U", "unit": "Kg"},
        {"category_code": "1700", "id": "A1", "unit": "No"},
        {"category_code": "1700", "id": "A2", "unit": "Kg"},  # code 1700 has 2 units → no code fallback
    ])
    maps = maps_from_inventory(inv)
    mon = pd.DataFrame([
        {"category_code": "1473", "id": "378471411121U", "material_name": "378471411121U - SOLWOOL", "unit": "SH",
         "quantity": 125, "tundish_type": "تاندیش بیلت", "month": 6, "date": "14050604"},
        {"category_code": "1473", "id": "378700009162X", "material_name": "378700009162X - فلت", "unit": "SH",
         "quantity": 75, "tundish_type": "تاندیش اسلب", "month": 6, "date": "14050608"},
        {"category_code": "1473", "id": None, "material_name": "ناشناس کد ۱۴۷۳", "unit": "SH",
         "quantity": 1, "tundish_type": "تاندیش اسلب", "month": 6, "date": "14050608"},  # by 4-digit code
        {"category_code": "1700", "id": "ZZ9", "material_name": "ZZ9 - نیست", "unit": "SET",
         "quantity": 2, "tundish_type": "تاندیش اسلب", "month": 6, "date": "14050608"},  # missing → own unit
    ])
    out = apply_units(mon, maps)
    assert list(out["unit"]) == ["No", "No", "No", "SET"], list(out["unit"])
    assert list(mon["unit"]) == ["SH", "SH", "SH", "SET"]  # input untouched
    r = daily_rates(None, out)
    assert "SH" not in set(r["unit"].astype(str)), r

    # real load path: a cleaned monthly extract + منبع اصلی in a temp DB
    from analytics.frames import load_extract_frame
    from db.models import Database
    from services import unit_source

    db = Database()
    db.upsert_user("5201", "owner", display_name="مالک")
    user = db.get_user("5201")
    orig_maps = unit_source.main_source_maps
    unit_source.main_source_maps = lambda _db: maps  # isolate from the pipeline's catalog format
    try:
        from analytics import frames as fr

        orig = fr.process_file
        fr.process_file = lambda path, ft, u: (mon.copy(), {})
        db.get_latest_extracted_any = lambda ft: {"id": 1, "clean_path": __file__, "raw_path": None}
        f = load_extract_frame(db, "monthly_consumption", user)
        assert list(f["unit"]) == ["No", "No", "No", "SET"], list(f["unit"])
    finally:
        unit_source.main_source_maps = orig_maps
        fr.process_file = orig

    # monthly summary (reads the plant file directly)
    from excel.monthly_summary import aggregate_monthly_detail

    detail = pd.DataFrame([{"کد دسته بندی": "1473", "کد کالا": "378471411121U", "شرح": "SOLWOOL",
                            "مقدار": 125, "ضریب": 0.568, "سفارش کار": "1102030000", "تاریخ": "14050604",
                            "ماه": 6, "واحد": "SH", "درخواستی-برگشتی": "درخواستی"}])
    d2 = apply_units(detail, maps, unit_col="واحد", id_col="کد کالا", code_col="کد دسته بندی", name_col="شرح")
    data = aggregate_monthly_detail(d2)
    assert set(data.items["واحد"]) == {"No"}, data.items
    print("smoke_unit_main_source OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
