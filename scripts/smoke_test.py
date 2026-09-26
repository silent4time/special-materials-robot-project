#!/usr/bin/env python3
"""Offline smoke test: RBAC + warehouse inventory extract + surplus + PDF."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl import Workbook

from analytics.tundish import (
    critical_materials,
    daily_rates,
    forecast,
    period_consumption,
    remaining,
    suggest_requests,
    surplus_materials,
)
from config import CRITICAL_DAYS, REQUIRED_COLUMNS
from db.models import Database
from excel.id_parse import extract_item_id, extract_product_name
from excel.processor import extract_and_save_clean, process_session_files
from pdf.generator import generate_report
from scripts.make_samples import main as make_samples


def _write_wide_inventory(path: Path) -> None:
    """Wide inventory: allowlisted + extra categories + priority 0 + bad id."""
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["کد دسته بندی", "کد و شرح کالا", "موجودی", "اولویت", "extra_col_a"])
    rows = [
        # keep — category 1201, priority default/1
        ["1201", "ACID01 - اسید سولفوریک", 20, 1, "X"],
        ["1201", "CAUST02 - سود سوزآور", 80, 1, "Y"],
        ["1201", "CL04 - کلر", 8, 1, "S"],
        # drop — wrong category
        ["9999", "OTHER99 - ماده خارجی", 10, 1, "Z"],
        ["8888", "DROP88 - حذف دسته", 5, 1, "Q"],
        # drop — priority 0
        ["1201", "ZERO00 - حذف اولویت", 50, 0, "P"],
        # drop — bad id (no digit token)
        ["1201", "بدون‌شناسه ماده", 3, 1, "B"],
        # keep — high surplus candidate
        ["1201", "OIL05 - روغن صنعتی", 500, 1, "O"],
    ]
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


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

    # --- category allowlist ---
    assert db.active_category_code_set() == set()
    row = db.add_category_code("1201", label="مواد ویژه", created_by="999")
    assert row["code"] == "1201"
    assert "1201" in db.active_category_code_set()
    # validation
    try:
        db.add_category_code("12")
        raise AssertionError("short code should fail")
    except ValueError:
        pass
    try:
        db.add_category_code("abcd")
        raise AssertionError("non-digit should fail")
    except ValueError:
        pass

    # --- id parser ---
    assert extract_item_id("ACID01 - اسید سولفوریک") == "ACID01"
    assert extract_product_name("ACID01 - اسید سولفوریک") == "اسید سولفوریک"
    assert extract_item_id("55 ماده") == "55"
    assert extract_item_id("بدون عدد") is None

    # Build cleaned sample inventory via extract (samples are raw 3-col)
    sample_inv_raw = ROOT / "samples" / "02_product_inventory.xlsx"
    sample_clean = extract_and_save_clean(
        sample_inv_raw,
        "product_inventory",
        category_allowlist=db.active_category_code_set(),
        clean_dir=ROOT / "uploads" / "_smoke_extract" / "samples_clean",
    )
    assert sample_clean.kept_row_count == 5
    assert all(c in sample_clean.columns for c in ("id", "priority", "category_code"))

    paths = {
        "tank_consumption": str(ROOT / "samples" / "01_tank_consumption.xlsx"),
        "product_inventory": str(sample_clean.clean_path),
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
        surplus = surplus_materials(rates, rem)
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
            "surplus": surplus,
        }
        out = ROOT / "reports" / f"smoke_{label}.pdf"
        generate_report(frames, metas, user, out, analytics=analytics)
        visible = {k: metas[k]["visible_rows"] for k in metas}
        print(label, "visible", visible, "crit", len(crit), "surplus", len(surplus), "->", out)
        assert out.exists() and out.stat().st_size > 1000

    # technician must see fewer tank rows than manager; inventory is plant-wide
    mgr = db.get_user("999")
    tech = db.get_user("1001")
    mf, mm = process_session_files(paths, mgr)
    tf, tm = process_session_files(paths, tech)
    assert tm["tank_consumption"]["visible_rows"] < mm["tank_consumption"]["visible_rows"]
    assert tm["tank_consumption"]["visible_rows"] == 4  # علی رضایی rows
    # warehouse inventory: no domain → full view for technician
    assert tm["product_inventory"]["visible_rows"] == mm["product_inventory"]["visible_rows"]
    off = db.get_user("1002")
    of, om = process_session_files(paths, off)
    assert om["tank_consumption"]["visible_rows"] == 4  # خط-B

    rates = daily_rates(mf["tank_consumption"], mf["monthly_consumption"])
    assert not rates.empty
    assert (rates["avg_daily"] > 0).any()
    rem = remaining(mf["product_inventory"])
    assert not rem.empty
    crit = critical_materials(rates, rem, CRITICAL_DAYS)
    crit_names = set(crit["material_name"].astype(str))
    assert "اسید سولفوریک" in crit_names
    sug = suggest_requests(rates, rem, 10)
    assert "suggest_qty" in sug.columns
    assert float(CRITICAL_DAYS) == 3.0

    # surplus callable
    surplus = surplus_materials(rates, rem)
    assert "surplus_reason" in surplus.columns
    assert not surplus.empty  # روغن صنعتی should be surplus (500 stock, low daily)
    surplus_names = set(surplus["material_name"].astype(str))
    assert "روغن صنعتی" in surplus_names

    # --- extract pipeline: allowlist filter + id + priority ---
    wide_dir = ROOT / "uploads" / "_smoke_extract" / "1"
    wide_path = wide_dir / "product_inventory.xlsx"
    _write_wide_inventory(wide_path)

    # empty allowlist must fail
    try:
        extract_and_save_clean(wide_path, "product_inventory", category_allowlist=set())
        raise AssertionError("empty allowlist should fail")
    except Exception as exc:
        assert "دسته" in str(exc) or "دسته‌بندی" in str(exc) or "دسته بندی" in str(exc)

    result = extract_and_save_clean(
        wide_path,
        "product_inventory",
        category_allowlist=db.active_category_code_set(),
    )
    assert result.clean_path.exists()
    assert result.raw_row_count == 8
    # keep: ACID01, CAUST02, CL04, OIL05 = 4
    assert result.kept_row_count == 4
    assert result.dropped_row_count == 4
    assert result.drop_reasons.get("wrong_category", 0) == 2
    assert result.drop_reasons.get("priority_0", 0) == 1
    assert result.drop_reasons.get("bad_id", 0) == 1

    clean_df = pd.read_excel(result.clean_path, engine="openpyxl")
    assert list(clean_df.columns) == REQUIRED_COLUMNS["product_inventory"]
    assert len(clean_df) == 4
    assert set(clean_df["id"].astype(str)) == {"ACID01", "CAUST02", "CL04", "OIL05"}
    assert (clean_df["priority"] == 1).all()
    assert (clean_df["category_code"].astype(str) == "1201").all()

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
    assert latest["row_count"] == 4

    clean_paths = {
        "tank_consumption": paths["tank_consumption"],
        "product_inventory": str(result.clean_path),
        "monthly_consumption": paths["monthly_consumption"],
    }
    cf, cm = process_session_files(clean_paths, mgr)
    assert cm["product_inventory"]["total_rows"] == 4
    # surplus still callable on cleaned extract
    surplus2 = surplus_materials(
        daily_rates(cf["tank_consumption"], cf["monthly_consumption"]),
        remaining(cf["product_inventory"]),
    )
    assert isinstance(surplus2, pd.DataFrame)

    print("SMOKE OK CRITICAL_DAYS=", CRITICAL_DAYS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
