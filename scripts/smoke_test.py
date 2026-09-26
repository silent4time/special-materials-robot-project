#!/usr/bin/env python3
"""Offline smoke test: RBAC filter + analytics + PDF generation without Bale network."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analytics.tundish import (
    critical_materials,
    daily_rates,
    forecast,
    period_consumption,
    remaining,
    suggest_requests,
)
from config import CRITICAL_DAYS, REQUIRED_COLUMNS
from db.models import Database
from excel.processor import extract_and_save_clean, process_session_files
from openpyxl import Workbook
from pdf.generator import generate_report
from scripts.make_samples import main as make_samples


def main() -> int:
    make_samples()
    db_path = ROOT / "data" / "smoke.db"
    if db_path.exists():
        db_path.unlink()
    db = Database(db_path)
    db.upsert_user("998", role="owner", display_name="مالک تست")
    db.upsert_user("999", role="manager", display_name="مدیر تست")
    db.upsert_user("1001", role="technician", display_name="علی رضایی", scope="خط-A")
    db.upsert_user("1002", role="responsible_officer", display_name="مریم احمدی", scope="خط-B")

    paths = {
        "tank_consumption": str(ROOT / "samples" / "01_tank_consumption.xlsx"),
        "product_inventory": str(ROOT / "samples" / "02_product_inventory.xlsx"),
        "monthly_consumption": str(ROOT / "samples" / "03_monthly_consumption.xlsx"),
    }

    for uid, label in [("998", "owner"), ("999", "manager"), ("1002", "officer"), ("1001", "tech")]:
        user = db.get_user(uid)
        frames, metas = process_session_files(paths, user)
        rates = daily_rates(frames.get("tank_consumption"), frames.get("monthly_consumption"))
        rem = remaining(frames.get("product_inventory"))
        crit = critical_materials(rates, rem, CRITICAL_DAYS)
        fc = forecast(rates, 7)
        sug = suggest_requests(rates, rem, 7)
        period = period_consumption(frames.get("tank_consumption"), date(2026, 9, 1), date(2026, 9, 7))
        analytics = {
            "start": date(2026, 9, 1),
            "end": date(2026, 9, 7),
            "days": 7,
            "critical_days": CRITICAL_DAYS,
            "daily_rates": rates,
            "period_consumption": period,
            "remaining": rem,
            "critical": crit,
            "forecast": fc,
            "suggest": sug,
        }
        out = ROOT / "reports" / f"smoke_{label}.pdf"
        generate_report(frames, metas, user, out, analytics=analytics)
        visible = {k: metas[k]["visible_rows"] for k in metas}
        print(label, "visible", visible, "crit", len(crit), "->", out)
        assert out.exists() and out.stat().st_size > 1000

    # technician must see fewer tank rows than manager
    mgr = db.get_user("999")
    tech = db.get_user("1001")
    mf, mm = process_session_files(paths, mgr)
    tf, tm = process_session_files(paths, tech)
    assert tm["tank_consumption"]["visible_rows"] < mm["tank_consumption"]["visible_rows"]
    assert tm["tank_consumption"]["visible_rows"] == 4  # علی رضایی rows
    off = db.get_user("1002")
    of, om = process_session_files(paths, off)
    assert om["tank_consumption"]["visible_rows"] == 4  # خط-B

    # analytics sanity on full (manager) data
    rates = daily_rates(mf["tank_consumption"], mf["monthly_consumption"])
    assert not rates.empty
    assert (rates["avg_daily"] > 0).any()
    rem = remaining(mf["product_inventory"])
    assert not rem.empty
    crit = critical_materials(rates, rem, CRITICAL_DAYS)
    # اسید (~11.75/day, rem 20) and کلر (~2.875/day, rem 8) should be critical with CRITICAL_DAYS=3
    crit_names = set(crit["material_name"].astype(str))
    assert "اسید سولفوریک" in crit_names
    sug = suggest_requests(rates, rem, 10)
    assert "suggest_qty" in sug.columns
    assert float(CRITICAL_DAYS) == 3.0

    # --- extract pipeline: drop extra ROWS + project columns; DB record ---
    wide_dir = ROOT / "uploads" / "_smoke_extract" / "1"
    wide_dir.mkdir(parents=True, exist_ok=True)
    wide_path = wide_dir / "product_inventory.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    # Extra columns + extra/invalid rows (wrong tundish, blank qty, blank type)
    headers = [
        "domain", "tundish_type", "assignee_id", "assignee_name", "product_name",
        "quantity", "unit", "location", "date", "notes",
        "extra_col_a", "warehouse_code",
    ]
    ws.append(headers)
    rows = [
        ["خط-A", "تاندیش اسلب", "1001", "علی رضایی", "اسید سولفوریک", 20, "لیتر", "قفسه ۱", "2026-09-06", "کم", "X", "W1"],
        ["خط-A", "تاندیش بلوم", "1001", "علی رضایی", "سود سوزآور", 80, "کیلو", "قفسه ۲", "2026-09-06", "", "Y", "W2"],
        ["خط-B", "نامعتبر", "1002", "مریم احمدی", "آب اکسیژنه", 100, "لیتر", "سالن B", "2026-09-06", "", "Z", "W3"],
        ["خط-B", "تاندیش اسلب", "1003", "حسین کریمی", "کلر", None, "کیلو", "سالن B", "2026-09-06", "خالی", "Q", "W4"],
        ["انبار", "", "1002", "مریم احمدی", "روغن صنعتی", 50, "لیتر", "انبار مرکزی", "2026-09-06", "", "R", "W5"],
        ["خط-B", "تاندیش اسلب", "1003", "حسین کریمی", "کلر", 8, "کیلو", "سالن B", "2026-09-06", "بحرانی", "S", "W6"],
    ]
    for r in rows:
        ws.append(r)
    wb.save(wide_path)

    result = extract_and_save_clean(wide_path, "product_inventory")
    assert result.clean_path.exists()
    assert result.raw_row_count == 6
    assert result.kept_row_count == 3  # 2 good + 1 کلر with qty; drop invalid/blank/empty-qty
    assert result.dropped_row_count == 3
    assert "extra_col_a" in result.extra_columns_dropped
    import pandas as pd
    clean_df = pd.read_excel(result.clean_path, engine="openpyxl")
    assert list(clean_df.columns) == REQUIRED_COLUMNS["product_inventory"]
    assert len(clean_df) == 3

    sess = db.get_or_create_session("999")
    eid = db.save_extracted(
        "999",
        sess["id"],
        "product_inventory",
        str(result.raw_path),
        str(result.clean_path),
        result.kept_row_count,
        result.columns,
    )
    assert eid > 0
    latest = db.get_latest_extracted("999", "product_inventory")
    assert latest and latest["clean_path"] == str(result.clean_path)
    assert latest["row_count"] == 3
    listed = db.list_extracted_for_session(sess["id"])
    assert any(r["id"] == eid for r in listed)

    # cleaned inventory still works with analytics/RBAC
    clean_paths = {
        "tank_consumption": paths["tank_consumption"],
        "product_inventory": str(result.clean_path),
        "monthly_consumption": paths["monthly_consumption"],
    }
    cf, cm = process_session_files(clean_paths, mgr)
    assert cm["product_inventory"]["total_rows"] == 3

    print("SMOKE OK CRITICAL_DAYS=", CRITICAL_DAYS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
