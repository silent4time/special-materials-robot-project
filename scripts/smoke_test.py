#!/usr/bin/env python3
"""Offline smoke test: RBAC + warehouse inventory extract + site stock + surplus + PDF."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl import Workbook

from bot import keyboards as kb
from bot.jalali import (
    format_date,
    format_datetime,
    format_month_year_range,
    parse_month_year_range,
    parse_user_date,
    parse_user_date_range,
    resolve_month_year_preset,
    month_year_to_gregorian_bounds,
)
from bot.settings_text import DEFAULT_INVITE_TEXT, format_invite_text, format_welcome_text
from analytics.tundish import (
    critical_materials,
    daily_rates,
    forecast,
    period_consumption,
    apply_inventory_ledger,
    remaining,
    suggest_requests,
    surplus_materials,
)
from auth.rbac import can_configure_catalog, can_request_materials, filter_dataframe_for_user
from config import CRITICAL_DAYS, DEFAULT_CATEGORY_CODES, REQUIRED_COLUMNS, SITE_STOCK_GROUPS, FILE_TYPES
from db.models import Database
from excel.id_parse import extract_item_id, extract_product_name
from excel.inbound import (
    STATUS_INCREASE,
    STATUS_NEW,
    compute_inbound_delta,
    format_inbound_list_fa,
    write_inbound_excel,
)
from services import main_source as main_source_svc
from excel.processor import (
    extract_and_save_clean,
    format_inventory_table_fa,
    merge_clean_frames,
    process_session_files,
)
from excel.monthly_summary import build_monthly_summary, load_monthly_detail, aggregate_monthly_detail, filter_summary_by_month_range
from excel.work_order import (
    WORK_ORDER_TO_GROUP,
    group_for_work_order,
    normalize_work_order,
    tundish_kg_totals_from_items,
)
from pdf.generator import generate_monthly_summary_pdf, generate_report, generate_simple_report_pdf, apply_letterhead
from excel.monthly_summary import summary_sections_for_pdf
from scripts.make_samples import main as make_samples


def _write_wide_inventory(path: Path) -> None:
    """Wide inventory: allowlisted + extra categories + priority 0 + bad id."""
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["کد دسته بندی", "کد و شرح کالا", "موجودی", "اولویت", "کلید واژه", "extra_col_a"])
    rows = [
        # keep — category 1201, priority default/1
        ["1201", "ACID01 - اسید سولفوریک", 20, 1, "اسید", "X"],
        ["1201", "CAUST02 - سود سوزآور", 80, 1, "سود", "Y"],
        ["1201", "CL04 - کلر", 8, 1, "", "S"],
        # drop — wrong category
        ["9999", "OTHER99 - ماده خارجی", 10, 1, "", "Z"],
        ["8888", "DROP88 - حذف دسته", 5, 1, "", "Q"],
        # drop — priority 0
        ["1201", "ZERO00 - حذف اولویت", 50, 0, "", "P"],
        # drop — bad id (no digit token)
        ["1201", "بدون‌شناسه ماده", 3, 1, "", "B"],
        # keep — high surplus candidate
        ["1201", "OIL05 - روغن صنعتی", 500, 1, "روغن", "O"],
    ]
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _test_first_owner_claim() -> None:
    """Empty DB: first bare claim wins owner; second user cannot claim."""
    claim_db_path = ROOT / "data" / "smoke_first_owner.db"
    if claim_db_path.exists():
        claim_db_path.unlink()
    cdb = Database(claim_db_path)
    assert cdb.count_active_owners() == 0
    first = cdb.try_claim_first_owner("7001", display_name="اولین")
    assert first is not None
    assert first["role"] == "owner"
    assert first["bale_user_id"] == "7001"
    assert cdb.count_active_owners() == 1
    second = cdb.try_claim_first_owner("7002", display_name="دومین")
    assert second is None
    assert cdb.count_active_owners() == 1
    assert cdb.get_user("7002") is None
    # already-registered owner path stays owner
    again = cdb.try_claim_first_owner("7001", display_name="اولین")
    assert again is None  # owners already exist → no re-claim path returns None
    assert cdb.count_active_owners() == 1
    claim_db_path.unlink(missing_ok=True)


def _test_install_help_soft_seed() -> None:
    help_txt = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "مالک با اولین /start" in help_txt
    assert "env_looks_configured" in help_txt
    assert "run_env_wizard" in help_txt



def _test_merge_and_monthly_summary() -> None:
    """Merge upsert + monthly summary grand total ≈ 1221814 from real sample."""
    import pandas as pd

    old = pd.DataFrame(
        [
            {
                "id": "A1",
                "product_name": "old-only",
                "keyword": "",
                "usage_location": "اسلب",
                "quantity": 10,
                "category_code": "1201",
                "priority": 1,
            },
            {
                "id": "B2",
                "product_name": "shared",
                "keyword": "shared-kw",
                "usage_location": "بلوم",
                "quantity": 5,
                "category_code": "1201",
                "priority": 1,
            },
        ]
    )
    new = pd.DataFrame(
        [
            {
                "id": "B2",
                "product_name": "shared-new",
                "keyword": "shared-kw-new",
                "usage_location": "",  # blank → preserve بلوم
                "quantity": 99,
                "category_code": "1201",
                "priority": 1,
            },
            {
                "id": "C3",
                "product_name": "new-only",
                "keyword": "",
                "usage_location": "بیلت",
                "quantity": 7,
                "category_code": "1201",
                "priority": 1,
            },
        ]
    )
    merged = merge_clean_frames(old, new, "product_inventory")
    assert set(merged["id"].astype(str)) == {"A1", "B2", "C3"}
    assert float(merged.loc[merged["id"].astype(str) == "B2", "quantity"].iloc[0]) == 99
    assert float(merged.loc[merged["id"].astype(str) == "A1", "quantity"].iloc[0]) == 10
    assert str(merged.loc[merged["id"].astype(str) == "B2", "usage_location"].iloc[0]) == "بلوم"
    assert str(merged.loc[merged["id"].astype(str) == "A1", "usage_location"].iloc[0]) == "اسلب"
    assert str(merged.loc[merged["id"].astype(str) == "C3", "usage_location"].iloc[0]) == "بیلت"

    # work_order normalize + map
    assert normalize_work_order("1102010000") == "1102010000"
    assert normalize_work_order(1102020000) == "1102020000"
    assert normalize_work_order(1102030000.0) == "1102030000"
    assert normalize_work_order("۱۱۰۲۰۱۰۰۰۰") == "1102010000"
    assert normalize_work_order(" 1102010000 ") == "1102010000"
    assert group_for_work_order("1102010000") == "slab"
    assert group_for_work_order("1102020000") == "bloom"
    assert group_for_work_order("1102030000") == "billet"
    assert WORK_ORDER_TO_GROUP["1102010000"] == "slab"

    # usage_location from monthly consumption (work_order → اسلب/بلوم/بیلت)
    monthly_tiny = pd.DataFrame(
        [
            {"id": "X1", "work_order": "1102010000", "tundish_type": "تاندیش اسلب", "quantity": 1},
            {"id": "X1", "work_order": "1102030000", "tundish_type": "تاندیش بیلت", "quantity": 2},
            {"id": "X2", "work_order": "1102020000", "tundish_type": "تاندیش بلوم", "quantity": 3},
        ]
    )
    loc_map = main_source_svc.build_usage_location_map(monthly_tiny)
    assert loc_map["x1"] == "اسلب، بیلت" or loc_map.get("X1") == "اسلب، بیلت" or "اسلب" in loc_map.get("x1", loc_map.get("X1", ""))
    # normalize_key_part lowercases → keys are casefolded
    assert "اسلب" in loc_map["x1"] and "بیلت" in loc_map["x1"]
    assert loc_map["x2"] == "بلوم"

    sample = ROOT / "samples" / "real" / "monthly_consumption_sample.xlsx"
    if sample.exists():
        detail = load_monthly_detail(sample)
        data = aggregate_monthly_detail(detail)
        assert abs(float(data.grand_kg) - 1225329) < 0.5, data.grand_kg
        assert (data.items["مقدار"] > 0).all()
        assert len(data.items) == 153
        month_sum = sum(float(s["total_kg"]) for s in data.month_sections)
        assert abs(month_sum - float(data.grand_kg)) < 0.5
        totals = data.tundish_totals or tundish_kg_totals_from_items(data.items)
        slab_kg = float(totals["slab"]["kg"])
        bloom_kg = float(totals["bloom"]["kg"])
        billet_kg = float(totals["billet"]["kg"])
        unk_kg = float(totals.get("unknown", {}).get("kg") or 0)
        assert abs(slab_kg + bloom_kg + billet_kg + unk_kg - float(data.grand_kg)) < 0.5, (
            slab_kg, bloom_kg, billet_kg, unk_kg, data.grand_kg
        )
        # Spot-check sample WO section magnitudes (dominant-WO aggregation)
        # count = unique شرح کالا; kg rolled up per description then by dominant WO
        assert abs(slab_kg - 840153.6) < 1.0, slab_kg
        assert abs(bloom_kg - 1092.0) < 1.0, bloom_kg
        assert abs(billet_kg - 384083.4) < 1.0, billet_kg
        assert int(totals["slab"]["count"]) == 23
        assert int(totals["bloom"]["count"]) == 1
        assert int(totals["billet"]["count"]) == 22
        assert int(totals["slab"]["count"]) + int(totals["bloom"]["count"]) + int(totals["billet"]["count"]) == int(data.items["شرح"].nunique())
        excel_out = ROOT / "reports" / "smoke_monthly_summary.xlsx"
        pdf_out = ROOT / "reports" / "smoke_monthly_summary.pdf"
        data2, written = build_monthly_summary(sample, excel_out=excel_out)
        assert written.exists()
        # Excel must contain the WO section title near the end
        from openpyxl import load_workbook
        wb = load_workbook(written, data_only=True)
        ws = wb.active
        found_title = False
        found_slab = False
        for row in ws.iter_rows(values_only=True):
            joined = " ".join(str(c) for c in row if c is not None)
            if "مصرف مواد بر حسب اسلب، بلوم و بیلت" in joined:
                found_title = True
            if "مصرف مواد اسلب" in joined:
                found_slab = True
        assert found_title and found_slab
        # Per-month WO under Farvardin: section kg sum == month total
        from excel.work_order import tundish_kg_totals_from_items as _mtot
        farv = next(s for s in data.month_sections if "فروردین" in s["title"])
        mt = _mtot(farv["rows"])
        assert abs(float(mt["slab"]["kg"]) + float(mt["bloom"]["kg"]) + float(mt["billet"]["kg"]) + float((mt.get("unknown") or {}).get("kg") or 0) - float(farv["total_kg"])) < 0.5
        generate_monthly_summary_pdf(
            summary_sections_for_pdf(data2),
            grand_kg=data2.grand_kg,
            output_path=pdf_out,
        )
        assert pdf_out.exists() and pdf_out.stat().st_size > 1000
        assert kb.BTN_MONTHLY_SUMMARY in str(kb.analytics_menu())
        assert kb.BTN_INBOUND in str(kb.analytics_menu())
        assert kb.BTN_MY_CURRENT in str(kb.month_year_range_menu())
        # Month/year filter: Farvardin–Ordibehesht only
        filt = filter_summary_by_month_range(data, start=(1405, 1), end=(1405, 2))
        assert len(filt.month_sections) == 2
        assert all(s["month"] in (1, 2) for s in filt.month_sections)
        assert abs(sum(float(s["total_kg"]) for s in filt.month_sections) - float(filt.grand_kg)) < 0.5
        empty = filter_summary_by_month_range(data, start=(1390, 1), end=(1390, 2))
        assert empty.items.empty and empty.grand_kg == 0.0
        data3, written3 = build_monthly_summary(sample, excel_out=excel_out, start=(1405, 1), end=(1405, 2))
        assert abs(float(data3.grand_kg) - float(filt.grand_kg)) < 0.5


def _test_inbound_delta() -> None:
    """Unit/smoke: inbound = new or increase; decreases/unchanged ignored; allowlist filter."""
    previous = pd.DataFrame(
        [
            {
                "id": "A1",
                "keyword": "",
                "product_name": "اسید",
                "category_code": "1201",
                "quantity": 10,
                "priority": 1,
            },
            {
                "id": "B2",
                "keyword": "",
                "product_name": "روغن",
                "category_code": "1201",
                "quantity": 50,
                "priority": 1,
            },
            {
                "id": "D4",
                "keyword": "",
                "product_name": "کاهش",
                "category_code": "1201",
                "quantity": 30,
                "priority": 1,
            },
            {
                "id": "E5",
                "keyword": "",
                "product_name": "ثابت",
                "category_code": "1201",
                "quantity": 7,
                "priority": 1,
            },
        ]
    )
    current = pd.DataFrame(
        [
            # increase
            {
                "id": "A1",
                "keyword": "",
                "product_name": "اسید",
                "category_code": "1201",
                "quantity": 25,
                "priority": 1,
            },
            # decrease — omit
            {
                "id": "D4",
                "keyword": "",
                "product_name": "کاهش",
                "category_code": "1201",
                "quantity": 10,
                "priority": 1,
            },
            # unchanged — omit
            {
                "id": "E5",
                "keyword": "",
                "product_name": "ثابت",
                "category_code": "1201",
                "quantity": 7,
                "priority": 1,
            },
            # brand-new allowlisted
            {
                "id": "C3",
                "keyword": "",
                "product_name": "جدید",
                "category_code": "1201",
                "quantity": 12,
                "priority": 1,
            },
            # brand-new NOT in allowlist — omit
            {
                "id": "X9",
                "keyword": "",
                "product_name": "خارجی",
                "category_code": "9999",
                "quantity": 100,
                "priority": 1,
            },
            # increase but unknown category — omit
            {
                "id": "B2",
                "keyword": "",
                "product_name": "روغن",
                "category_code": "8888",
                "quantity": 80,
                "priority": 1,
            },
            # new with missing category — omit
            {
                "id": "Z0",
                "keyword": "",
                "product_name": "بدون دسته",
                "category_code": None,
                "quantity": 5,
                "priority": 1,
            },
        ]
    )
    allow = {"1201"}
    inbound = compute_inbound_delta(previous, current, category_allowlist=allow)
    ids = set(inbound["کد کالا"].astype(str))
    assert ids == {"A1", "C3"}, ids
    assert "D4" not in ids and "E5" not in ids and "X9" not in ids
    assert "B2" not in ids and "Z0" not in ids
    a1 = inbound.loc[inbound["کد کالا"].astype(str) == "A1"].iloc[0]
    assert float(a1["مقدار قبلی"]) == 10
    assert float(a1["مقدار جدید"]) == 25
    assert float(a1["مقدار ورودی"]) == 15
    assert a1["وضعیت"] == STATUS_INCREASE
    c3 = inbound.loc[inbound["کد کالا"].astype(str) == "C3"].iloc[0]
    assert float(c3["مقدار قبلی"]) == 0
    assert float(c3["مقدار ورودی"]) == 12
    assert c3["وضعیت"] == STATUS_NEW
    # empty allowlist → no rows
    empty = compute_inbound_delta(previous, current, category_allowlist=set())
    assert empty.empty
    # first upload (no previous) → all allowlisted current items as new
    first = compute_inbound_delta(None, current, category_allowlist=allow)
    assert set(first["کد کالا"].astype(str)) == {"A1", "C3", "D4", "E5"}
    assert (first["وضعیت"] == STATUS_NEW).all()
    chunks = format_inbound_list_fa(inbound)
    assert chunks and "A1" in chunks[0]
    out = ROOT / "reports" / "smoke_inbound.xlsx"
    write_inbound_excel(inbound, out)
    assert out.exists() and out.stat().st_size > 100
    pdf_in = ROOT / "reports" / "smoke_inbound.pdf"
    generate_simple_report_pdf(
        "گزارش ورودی به انبار",
        subtitle="smoke",
        columns=list(inbound.columns),
        rows=inbound.to_dict(orient="records"),
        output_path=pdf_in,
        filename_stem="smoke_inbound",
    )
    assert pdf_in.exists() and pdf_in.stat().st_size > 500
    # Empty inbound: handlers now reply with text only (no empty PDF).
    # letterhead stamp
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.pagesizes import A4, landscape as _landscape
    lh = ROOT / "reports" / "smoke_letterhead.pdf"
    c = _canvas.Canvas(str(lh), pagesize=_landscape(A4))
    c.drawString(50, 50, "LH")
    c.showPage()
    c.save()
    stamped = apply_letterhead(pdf_in, lh, output_path=ROOT / "reports" / "smoke_inbound_lh.pdf")
    assert stamped.exists() and stamped.stat().st_size > pdf_in.stat().st_size - 1000
    print("inbound_delta OK", list(inbound["کد کالا"]), "pdf+letterhead OK")




def _test_user_activity_log() -> None:
    """Unit/smoke: log_activity + format lines + PDF with rows; empty = no rows."""
    from bot.activity import format_activity_line, log_activity, ACTION_PHRASES
    from bot.jalali import format_datetime
    from pdf.generator import generate_simple_report_pdf

    db_path = ROOT / "data" / "smoke_activity.db"
    if db_path.exists():
        db_path.unlink()
    db = Database(db_path)
    db.upsert_user("1644670601", role="owner", display_name="نصراله ولی‌زاده")
    user = db.get_user("1644670601")
    log_activity(db, user, "report_monthly_summary")
    log_activity(db, user, "site_stock_saved", tundish_group="slab")
    log_activity(db, user, "upload_product_inventory")
    rows = db.list_user_activities(newest_first=True)
    assert len(rows) == 3
    line = format_activity_line("نصراله ولی‌زاده", "1644670601", "report_monthly_summary")
    assert "نصراله" in line and "1644670601" in line and "خلاصه مصرف ماهیانه" in line
    assert "اسلب" in format_activity_line("x", 1, "site_stock_saved", tundish_group="slab")
    assert "BTN_USER_ACTIVITY" in dir(kb) or hasattr(kb, "BTN_USER_ACTIVITY")
    menu = {b["text"] for row in kb.analytics_menu()["keyboard"] for b in row}
    assert kb.BTN_USER_ACTIVITY in menu
    pdf_rows = [
        {"زمان": format_datetime(r["created_at"]), "فعالیت": r["message_fa"]}
        for r in rows
    ]
    out = ROOT / "reports" / "smoke_user_activity.pdf"
    generate_simple_report_pdf(
        "گزارش فعالیت کاربران",
        subtitle="smoke",
        columns=["زمان", "فعالیت"],
        rows=pdf_rows,
        output_path=out,
    )
    assert out.exists() and out.stat().st_size > 500
    # empty list → handlers must not send PDF (unit check: no rows)
    empty = db.list_user_activities(
        start_iso="2099-01-01T00:00:00+00:00",
        end_iso="2099-12-31T23:59:59+00:00",
    )
    assert empty == []
    print("user_activity OK", len(ACTION_PHRASES), "actions", out.name)



def main() -> int:
    make_samples()
    _test_inbound_delta()
    _test_user_activity_log()
    _test_first_owner_claim()
    _test_install_help_soft_seed()
    db_path = ROOT / "data" / "smoke.db"
    if db_path.exists():
        db_path.unlink()
    db = Database(db_path)
    db.upsert_user("998", role="owner", display_name="مالک تست")
    db.upsert_user("999", role="manager", display_name="مدیر تست")
    db.upsert_user("1001", role="technician", display_name="علی رضایی", scope="خط-A")
    # officer: no scope required — identity is bale_user_id
    db.upsert_user("1002", role="responsible_officer", display_name="مریم احمدی")

    # --- role-specific main menus ---
    def menu_texts(menu: dict) -> set[str]:
        return {button["text"] for row in menu["keyboard"] for button in row}

    tech_menu = menu_texts(kb.main_menu(db.get_user("1001")))
    owner_menu = menu_texts(kb.main_menu(db.get_user("998")))
    manager_menu = menu_texts(kb.main_menu(db.get_user("999")))
    officer_menu = menu_texts(kb.main_menu(db.get_user("1002")))
    assert kb.BTN_SITE_STOCK in tech_menu
    assert kb.BTN_INV_MENU not in tech_menu
    assert kb.BTN_UPLOAD_MENU not in tech_menu
    assert kb.BTN_ANALYTICS not in tech_menu
    assert kb.BTN_MONTHLY not in tech_menu
    assert kb.BTN_CATALOG_SETTINGS not in tech_menu
    for full_menu in (owner_menu, manager_menu, officer_menu):
        assert {kb.BTN_UPLOAD_MENU, kb.BTN_ANALYTICS, kb.BTN_SITE_STOCK}.issubset(full_menu)
        assert kb.BTN_INV_MENU not in full_menu  # moved under آپلود فایل
        assert kb.BTN_MONTHLY not in full_menu  # moved under آپلود فایل
        assert kb.BTN_CATALOG_SETTINGS in full_menu
        assert kb.BTN_MATERIAL_REQUEST in full_menu
        assert kb.BTN_WAREHOUSE_RETURN in full_menu
    assert kb.BTN_MATERIAL_REQUEST not in tech_menu
    assert kb.BTN_WAREHOUSE_RETURN not in tech_menu
    assert can_request_materials(db.get_user("998"))
    assert can_request_materials(db.get_user("1002"))
    assert not can_request_materials(db.get_user("1001"))
    assert kb.BTN_USERS in owner_menu and kb.BTN_USERS in manager_menu
    assert kb.BTN_USERS not in tech_menu
    assert kb.BTN_USERS not in officer_menu
    assert kb.BTN_BOT_SETTINGS in owner_menu and kb.BTN_BOT_SETTINGS in manager_menu
    assert kb.BTN_BOT_SETTINGS not in tech_menu
    assert kb.BTN_BOT_SETTINGS not in officer_menu
    bot_set_menu = menu_texts(kb.bot_settings_menu())
    assert kb.BTN_SET_INVITE in bot_set_menu
    assert kb.BTN_SET_WELCOME in bot_set_menu
    assert kb.BTN_SET_LOGO in bot_set_menu
    item_menu = menu_texts(kb.bot_settings_item_menu(include_text=True))
    assert kb.BTN_SETTINGS_VIEW in item_menu
    assert kb.BTN_SETTINGS_EDIT_TEXT in item_menu
    assert kb.BTN_SETTINGS_SET_IMAGE in item_menu
    logo_menu = menu_texts(kb.bot_settings_item_menu(include_text=False))
    assert kb.BTN_SETTINGS_EDIT_TEXT not in logo_menu
    users_submenu = menu_texts(kb.users_menu())
    assert kb.BTN_USERS_ADD in users_submenu
    assert kb.BTN_USERS_EDIT in users_submenu
    assert kb.BTN_USERS_DELETE in users_submenu
    assert kb.BTN_USERS_LIST in users_submenu
    # inline URL invite button (forwardable deep link)
    inv_kb = kb.invite_url_button("https://ble.ir/nasoz_bot?start=tok123")
    assert "inline_keyboard" in inv_kb
    btn = inv_kb["inline_keyboard"][0][0]
    assert btn["text"] == kb.BTN_INVITE_ENTER == "ورود به ربات"
    assert btn["url"].startswith("https://ble.ir/")
    assert "keyboard" not in inv_kb  # not a reply keyboard

    # --- invites: create + consume as new technician ---
    inv = db.create_invite(role="technician", created_by="998", expires_days=7)
    assert inv and inv["token"] and inv["active"] == 1
    assert db.get_invite(inv["token"])["role"] == "technician"
    new_user = db.consume_invite(inv["token"], "555001", display_name="دعوت‌شده")
    assert new_user["role"] == "technician"
    assert new_user["bale_user_id"] == "555001"
    assert new_user["active"] == 1
    used = db.get_invite(inv["token"])
    assert used["used_by"] == "555001"
    assert used["active"] == 0
    try:
        db.consume_invite(inv["token"], "555002", display_name="دوباره")
        raise AssertionError("reuse should fail")
    except ValueError:
        pass
    # edit role + soft delete
    db.set_role("555001", "responsible_officer")
    assert db.get_user("555001")["role"] == "responsible_officer"
    db.deactivate_user("555001")
    assert db.get_user("555001")["active"] == 0
    # re-invite applies role even if user exists (admin intent)
    inv2 = db.create_invite(role="technician", created_by="998")
    revived = db.consume_invite(inv2["token"], "555001", display_name="دوباره فعال")
    assert revived["role"] == "technician" and revived["active"] == 1

    # officer invite does NOT need scope
    inv_off = db.create_invite(role="responsible_officer", created_by="998", scope=None)
    assert inv_off.get("scope") in (None, "")
    off_new = db.consume_invite(inv_off["token"], "555010", display_name="کاردان جدید")
    assert off_new["role"] == "responsible_officer"
    assert off_new.get("scope") in (None, "")
    assert off_new["bale_user_id"] == "555010"

    # site stock submenu labels
    site_menu = menu_texts(kb.site_stock_menu())
    assert kb.BTN_SITE_SLAB in site_menu
    assert kb.BTN_SITE_BLOOM in site_menu
    assert kb.BTN_SITE_BILLET in site_menu
    assert kb.BTN_BACK_PREV in site_menu  # clear back to previous/main
    assert SITE_STOCK_GROUPS["billet"] == "موجودی مواد بیلت"
    assert kb.SITE_GROUP_BUTTONS[kb.BTN_SITE_BILLET] == "billet"

    # آپلود فایل submenu + فایل منبع اصلی + file entry picker + routing maps
    upload_menu = menu_texts(kb.upload_files_menu())
    assert {
        kb.BTN_WAREHOUSE_STOCK,
        kb.BTN_MONTHLY,
        kb.BTN_MAIN_SOURCE_FILE,
        kb.BTN_BACK_MAIN,
    }.issubset(upload_menu)
    assert kb.BTN_SITE_STOCK not in upload_menu

    main_src_menu = menu_texts(kb.main_source_file_menu())
    assert {
        kb.BTN_INV_UPLOAD,
        kb.BTN_INV_ADD_RECORD,
        kb.BTN_INV_EDIT_RECORD,
        kb.BTN_INV_ADD_CATEGORY,
        kb.BTN_INV_LIST_CATEGORIES,
        kb.BTN_BACK_UPLOAD,
    }.issubset(main_src_menu)
    assert kb.BTN_INV_EDIT not in main_src_menu  # flattened — no nested edit opener
    assert menu_texts(kb.inventory_menu()) == main_src_menu
    assert menu_texts(kb.inventory_edit_menu()) == main_src_menu

    file_entry = menu_texts(kb.file_entry_menu())
    assert file_entry == upload_menu  # status/doc picker = upload picker (no site stock)
    assert kb.BTN_SITE_STOCK not in file_entry
    assert kb.BTN_INV_MENU not in file_entry
    assert kb.button_to_file_type(kb.BTN_WAREHOUSE_STOCK) == "product_inventory"
    assert kb.button_to_file_type(kb.BTN_INV) == "product_inventory"
    assert kb.button_to_file_type(kb.BTN_INV_UPLOAD) == "product_inventory"
    assert kb.button_to_file_type(kb.BTN_MONTHLY) == "monthly_consumption"
    assert kb.button_to_file_type(kb.BTN_SITE_STOCK) is None  # interactive, not Excel
    assert kb.button_to_file_type(kb.BTN_INV_MENU) is None  # legacy submenu opener
    assert kb.button_to_file_type(kb.BTN_MAIN_SOURCE_FILE) is None  # submenu opener
    assert kb.button_to_file_type(kb.BTN_UPLOAD_MENU) is None
    assert kb.button_to_file_type("📦 موجودی انبار") == "product_inventory"  # warehouse upload
    assert kb.BTN_WAREHOUSE_STOCK == "📥 موجودی انبار"

    # site stock inline 2-col keyboard (شرح | تعداد) + confirm
    items_fake = [
        {"id": "A1", "name_desc": "اسید کوتاه"},
        {"id": "A2", "name_desc": "نام خیلی خیلی خیلی خیلی خیلی خیلی خیلی خیلی خیلی خیلی خیلی خیلی خیلی طولانی برای دکمه"},
    ]
    ik = kb.site_stock_inline_keyboard(items_fake, {"A1": 12.5}, group_key="slab")
    assert "inline_keyboard" in ik
    rows = ik["inline_keyboard"]
    assert len(rows[0]) == 2
    assert rows[0][0]["callback_data"] == "ss|slab|0|n"
    assert rows[0][1]["callback_data"] == "ss|slab|0|q"
    assert rows[0][1]["text"] == "12.5"
    assert rows[1][1]["text"] == "…"
    assert len(rows[1][0]["text"]) <= 60
    assert rows[-2][0]["callback_data"] == "ss|ok"
    assert kb.BTN_SITE_CONFIRM in rows[-2][0]["text"]
    assert rows[-1][0]["callback_data"] == "ss|x"

    # site stock entry reply keyboard includes skip during guided entry
    entry_menu = menu_texts(kb.site_stock_entry_menu())
    assert kb.BTN_SITE_SKIP in entry_menu
    assert kb.BTN_SITE_CANCEL in entry_menu
    assert kb.BTN_BACK_SITE in entry_menu

    # reserved nav texts must not be treated as quantities while awaiting
    assert kb.BTN_BACK_SITE in kb.SITE_STOCK_RESERVED_TEXTS
    assert kb.BTN_SITE_SKIP in kb.SITE_STOCK_RESERVED_TEXTS
    assert kb.BTN_SITE_CANCEL in kb.SITE_STOCK_RESERVED_TEXTS
    assert kb.BTN_BACK_PREV in kb.SITE_STOCK_RESERVED_TEXTS
    assert kb.BTN_SITE_SLAB in kb.SITE_STOCK_RESERVED_TEXTS

    # display name strips leading catalog id
    assert kb.item_display_name({"id": "37812L", "name_desc": "37812L - پودر قالب"}) == "پودر قالب"
    assert kb.item_display_name(name_desc="اسید کوتاه") == "اسید کوتاه"

    # RBAC: technician cannot configure catalog; others can
    assert not can_configure_catalog(db.get_user("1001"))
    assert can_configure_catalog(db.get_user("998"))
    assert can_configure_catalog(db.get_user("999"))
    assert can_configure_catalog(db.get_user("1002"))

    # --- category allowlist (defaults seeded on DB init) ---
    assert db.active_category_code_set() == set(DEFAULT_CATEGORY_CODES)
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
    assert all(c in sample_clean.columns for c in ("id", "priority", "category_code", "keyword", "product_name"))
    assert "item_code_desc" not in sample_clean.columns

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
    # officer is FULL_DATA: sees all tank rows (even with domain column / leftover scope)
    assert om["tank_consumption"]["visible_rows"] == mm["tank_consumption"]["visible_rows"]
    # explicit filter_dataframe: officer with domain column + empty/any scope → all rows
    tank_df = mf["tank_consumption"]
    assert "domain" in [c.lower() for c in tank_df.columns] or any(
        c.lower() == "domain" for c in tank_df.columns
    )
    filtered_off = filter_dataframe_for_user(tank_df, off)
    assert len(filtered_off) == len(tank_df)
    # even if scope were set historically, officer still sees all
    off_scoped = dict(off)
    off_scoped["scope"] = "خط-B"
    assert len(filter_dataframe_for_user(tank_df, off_scoped)) == len(tank_df)

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

    _test_merge_and_monthly_summary()

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
    import openpyxl as _ox
    _wb = _ox.load_workbook(result.clean_path, read_only=True)
    assert _wb.sheetnames == [FILE_TYPES["product_inventory"]["label_fa"]] == ["منبع اصلی"]
    _wb.close()
    assert list(clean_df.columns) == REQUIRED_COLUMNS["product_inventory"]
    assert REQUIRED_COLUMNS["product_inventory"] == [
        "category_code",
        "id",
        "product_name",
        "keyword",
        "usage_location",
        "quantity",
        "priority",
    ]
    assert "item_code_desc" not in clean_df.columns
    assert "keyword" in clean_df.columns
    assert "usage_location" in clean_df.columns
    assert len(clean_df) == 4
    assert set(clean_df["id"].astype(str)) == {"ACID01", "CAUST02", "CL04", "OIL05"}
    assert (clean_df["priority"] == 1).all()
    assert (clean_df["category_code"].astype(str) == "1201").all()
    kw_by_id = dict(zip(clean_df["id"].astype(str), clean_df["keyword"].fillna("").astype(str)))
    assert kw_by_id["ACID01"] == "اسید"
    assert kw_by_id["CAUST02"] == "سود"
    assert kw_by_id["CL04"] == ""
    assert kw_by_id["OIL05"] == "روغن"
    table_text = "\n".join(format_inventory_table_fa(clean_df))
    assert "کد دسته" in table_text and "شرح کالا" in table_text and "موجودی" in table_text
    assert "1201" in table_text and "ACID01 - اسید سولفوریک" in table_text and "20" in table_text

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
    assert str(latest["bale_user_id"]) == "999"

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

    # ========== catalog assignment + technician site stock entry ==========
    seed = db.seed_catalog_from_latest_warehouse()
    assert seed["ok"] is True
    assert seed["counts"]["inserted"] >= 4
    catalog = db.list_catalog_items(active_only=True)
    assert len(catalog) >= 4
    ids = {c["id"] for c in catalog}
    assert {"ACID01", "CAUST02", "CL04", "OIL05"}.issubset(ids)

    # manager assigns items to groups
    db.assign_item_to_group("ACID01", "slab", assigned_by="999")
    db.assign_item_to_group("CAUST02", "slab", assigned_by="999")
    db.assign_item_to_group("CL04", "bloom", assigned_by="1002")
    db.assign_item_to_group("OIL05", "billet", assigned_by="998")

    slab_items = db.list_items_for_group("slab")
    bloom_items = db.list_items_for_group("bloom")
    billet_items = db.list_items_for_group("billet")
    assert {i["id"] for i in slab_items} == {"ACID01", "CAUST02"}
    assert {i["id"] for i in bloom_items} == {"CL04"}
    assert {i["id"] for i in billet_items} == {"OIL05"}

    # one group per item — reassign moves
    db.assign_item_to_group("OIL05", "slab", assigned_by="999")
    assert "OIL05" in {i["id"] for i in db.list_items_for_group("slab")}
    assert db.list_items_for_group("billet") == []
    db.assign_item_to_group("OIL05", "billet", assigned_by="999")  # restore

    # technician entry: upsert daily quantities
    day = db.tehran_today()
    e1 = db.upsert_site_stock_entry(
        bale_user_id="1001",
        tundish_group="slab",
        item_id="ACID01",
        quantity=12.5,
        item_name_snapshot="ACID01 - اسید سولفوریک",
        actor_display_name="علی رضایی",
    )
    assert e1["entry_date"] == day
    assert float(e1["quantity"]) == 12.5
    assert str(e1["bale_user_id"]) == "1001"
    assert e1.get("actor_display_name") == "علی رضایی"
    # re-entry same day upserts and updates actor
    e1b = db.upsert_site_stock_entry(
        bale_user_id="999",
        tundish_group="slab",
        item_id="ACID01",
        quantity=15,
        actor_display_name="مدیر تست",
    )
    assert float(e1b["quantity"]) == 15.0
    assert str(e1b["bale_user_id"]) == "999"
    assert e1b.get("actor_display_name") == "مدیر تست"
    same_day = db.list_site_stock_entries(entry_date=day, tundish_group="slab", item_id="ACID01")
    assert len(same_day) == 1

    db.upsert_site_stock_entry(
        bale_user_id="1001", tundish_group="slab", item_id="CAUST02", quantity=3
    )
    db.upsert_site_stock_entry(
        bale_user_id="1001", tundish_group="bloom", item_id="CL04", quantity=7
    )
    db.upsert_site_stock_entry(
        bale_user_id="1001", tundish_group="billet", item_id="OIL05", quantity=100
    )
    assert len(db.list_site_stock_entries(entry_date=day)) == 4

    # site stock as remaining source
    rem_rows = db.site_stock_as_remaining_rows(day)
    rem_site = remaining(pd.DataFrame(rem_rows))
    assert not rem_site.empty
    names = set(rem_site["material_name"].astype(str))
    assert any("اسید" in n or "ACID01" in n for n in names)

    # tables exist
    with db.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "catalog_items" in tables
    assert "catalog_group_assignments" in tables
    assert "site_stock_entries" in tables
    assert "invites" in tables
    assert "bot_settings" in tables
    assert "material_requests" in tables
    assert "material_request_lines" in tables
    assert "inventory_ledger" in tables
    assert "warehouse_returns" in tables
    assert "warehouse_return_lines" in tables

    # --- bot settings persistence + template format ---
    assert db.get_setting("invite_text") is None
    db.set_setting("invite_text", "دعوت برای نقش {role_fa}", updated_by="998")
    assert db.get_setting("invite_text") == "دعوت برای نقش {role_fa}"
    formatted = format_invite_text(db.get_setting("invite_text"), "manager")
    assert "مدیر" in formatted
    assert "{role_fa}" not in formatted
    default_inv = format_invite_text(None, "technician")
    assert "تکنسین" in default_inv
    assert "{role_fa}" in DEFAULT_INVITE_TEXT
    welcome = format_welcome_text(None, db.get_user("1001"))
    assert "تکنسین" in welcome
    welcome2 = format_welcome_text("سلام {name} — نقش {role_fa}", db.get_user("999"))
    assert "مدیر تست" in welcome2 and "مدیر" in welcome2
    db.set_setting("logo_path", "/tmp/fake_logo.jpg", updated_by="998")
    assert db.get_setting("logo_path") == "/tmp/fake_logo.jpg"
    db.clear_setting("logo_path", updated_by="998")
    assert db.get_setting("logo_path") is None
    # reports.created_by + catalog assigned_by present
    with db.connect() as conn:
        report_cols = {r[1] for r in conn.execute("PRAGMA table_info(reports)").fetchall()}
        assign_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(catalog_group_assignments)").fetchall()
        }
        stock_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(site_stock_entries)").fetchall()
        }
    assert "created_by" in report_cols
    assert "bale_user_id" in report_cols
    assert "assigned_by" in assign_cols
    assert "actor_display_name" in stock_cols
    rid = db.save_report("998", sess["id"], "/tmp/smoke_report.pdf", {"n": 1})
    assert rid > 0
    with db.connect() as conn:
        rrow = dict(conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone())
    assert str(rrow["bale_user_id"]) == "998"
    assert str(rrow["created_by"]) == "998"


    # --- Jalali display helpers ---
    from datetime import date as _date
    assert format_date(_date(2024, 9, 25)) == "1403/07/04"
    assert parse_user_date("1403/07/04") == _date(2024, 9, 25)
    assert parse_user_date("2024-09-25") == _date(2024, 9, 25)
    jr = parse_user_date_range("از 1403/07/01 تا 1403/07/04")
    assert jr is not None and jr[0] == _date(2024, 9, 22)
    myr = parse_month_year_range("از 1405/01 تا 1405/06")
    assert myr == ((1405, 1), (1405, 6))
    myr2 = parse_month_year_range("از فروردین 1405 تا شهریور 1405")
    assert myr2 == ((1405, 1), (1405, 6))
    assert "فروردین" in format_month_year_range((1405, 1), (1405, 6))
    cur = resolve_month_year_preset("current")
    assert cur[0] == cur[1] and 1 <= cur[0][1] <= 12
    g0, g1 = month_year_to_gregorian_bounds((1405, 1), (1405, 1))
    assert g0 <= g1

    # --- material request: suggest from samples + confirm deducts remaining via ledger ---
    assert "material_requests" in tables
    assert "inventory_ledger" in tables
    rates_mr = daily_rates(cf["tank_consumption"], cf["monthly_consumption"])
    rem_before = remaining(cf["product_inventory"])
    sug_mr = suggest_requests(rates_mr, rem_before, 7)
    assert "suggest_qty" in sug_mr.columns
    assert (sug_mr["suggest_qty"] > 0).any(), "expected some suggest qty from samples"
    # Deduct a known warehouse item (ACID01) so remaining() clearly drops
    inv_df = cf["product_inventory"]
    acid_rows = inv_df[inv_df["id"].astype(str) == "ACID01"]
    assert not acid_rows.empty
    mat_name = str(acid_rows.iloc[0]["product_name"])
    rem_qty_before = float(
        rem_before.loc[rem_before["material_name"].astype(str) == mat_name, "remaining_qty"].sum()
    )
    assert rem_qty_before > 0
    qty = min(5.0, rem_qty_before)
    req = db.create_material_request(
        "1002",
        actor_display_name="مریم احمدی",
        coverage_days=7,
        lines=[{
            "item_id": "ACID01",
            "item_name": mat_name,
            "unit": None,
            "avg_daily": 1.0,
            "remaining_qty": rem_qty_before,
            "quantity": qty,
        }],
    )
    assert req["id"] > 0
    assert req["status"] == "confirmed"
    assert len(req["lines"]) == 1
    assert str(req["bale_user_id"]) == "1002"
    assert req.get("actor_display_name") == "مریم احمدی"
    sums = db.inventory_ledger_sums()
    assert "ACID01" in sums["by_id"]
    assert abs(sums["by_id"]["ACID01"] + qty) < 1e-6  # negative delta
    ledgered = apply_inventory_ledger(inv_df, sums["by_id"], sums["by_name"])
    rem_after = remaining(ledgered)
    rem_qty_after = float(
        rem_after.loc[rem_after["material_name"].astype(str) == mat_name, "remaining_qty"].sum()
    )
    assert abs((rem_qty_before - qty) - rem_qty_after) < 1e-6, (
        rem_qty_before, qty, rem_qty_after
    )
    print("material_request OK", mat_name, "deducted", qty)

    # --- warehouse return: surplus confirm adds positive ledger ---
    assert "warehouse_returns" in tables
    rem_site2 = remaining(pd.DataFrame(db.site_stock_as_remaining_rows(day)))
    surplus_wr = surplus_materials(rates_mr, rem_site2)
    assert not surplus_wr.empty
    # Prefer OIL05 (seeded billet site stock 100 — classic surplus).
    # Cover-only surplus can have surplus_qty==0 when rem < 30d forecast; still returnable.
    oil_match = surplus_wr[surplus_wr["material_name"].astype(str).str.contains("روغن|OIL05", regex=True)]
    srow = oil_match.iloc[0] if not oil_match.empty else surplus_wr.iloc[0]
    sname = str(srow["material_name"])
    sqty = float(srow["surplus_qty"])
    rem_s = float(srow.get("remaining_qty") or 0)
    assert rem_s > 0 or sqty > 0
    sid = "OIL05"
    for rr in db.site_stock_as_remaining_rows(day):
        if str(rr.get("id")) == "OIL05" or "روغن" in str(rr.get("product_name") or ""):
            sid = str(rr.get("id"))
            sname = str(rr.get("product_name") or sname)
            break
    ret_qty = min(sqty if sqty > 0 else rem_s, 5.0)
    assert ret_qty > 0
    sums_before_ret = db.inventory_ledger_sums()
    oil_before = float(sums_before_ret["by_id"].get(sid, 0.0))
    ret = db.create_warehouse_return(
        "999",
        actor_display_name="مدیر تست",
        lines=[{
            "item_id": sid,
            "item_name": sname,
            "unit": srow.get("unit"),
            "site_qty": float(srow.get("remaining_qty") or 0),
            "surplus_qty": sqty,
            "quantity": ret_qty,
            "surplus_reason": srow.get("surplus_reason"),
        }],
    )
    assert ret["id"] > 0
    assert ret["status"] == "confirmed"
    assert float(ret["lines"][0]["quantity"]) == ret_qty
    assert str(ret["bale_user_id"]) == "999"
    sums2 = db.inventory_ledger_sums()
    oil_after = float(sums2["by_id"].get(sid, 0.0))
    assert abs((oil_before + ret_qty) - oil_after) < 1e-6, (oil_before, ret_qty, oil_after)
    # remaining for OIL05 should rise after return
    rem_wh = remaining(apply_inventory_ledger(
        cf["product_inventory"], sums2["by_id"], sums2["by_name"]
    ))
    oil_names = rem_wh[rem_wh["material_name"].astype(str).str.contains("روغن", regex=False)]
    assert not oil_names.empty
    print("warehouse_return OK", sname, "qty", ret_qty, "ledger", oil_after)


    # --- منبع اصلی canonical: latest extract preferred; warehouse vs site split ---
    from analytics.frames import (
        PRIMARY_INVENTORY_LABEL,
        resolve_primary_inventory_path,
        resolve_remaining as fr_resolve_remaining,
        resolve_warehouse_remaining,
    )
    from excel.processor import looks_like_product_inventory, load_excel

    inv_peek = load_excel(sample_inv_raw, strict_tundish=False)
    assert looks_like_product_inventory(inv_peek)
    mon_peek = load_excel(ROOT / "samples" / "03_monthly_consumption.xlsx", strict_tundish=False)
    assert not looks_like_product_inventory(mon_peek)

    primary_path = resolve_primary_inventory_path(db, bale_user_id="999")
    assert primary_path and Path(primary_path).is_file()
    frames_wh = {"product_inventory": cf["product_inventory"], "monthly_consumption": None, "tank_consumption": None}
    rem_wh, src_wh = resolve_warehouse_remaining(db, frames_wh)
    assert src_wh == PRIMARY_INVENTORY_LABEL == "منبع اصلی"
    assert rem_wh is not None and not rem_wh.empty
    rem_site, src_site = fr_resolve_remaining(db, frames_wh)
    assert "سایت" in src_site  # site stock was seeded earlier in this smoke
    print("canonical منبع اصلی OK path=", primary_path)



    # unit-style: while pending awaiting, nav buttons are not treated as qty
    from bot.handlers import BotApp

    class _FakeClient:
        def edit_message_reply_markup(self, *a, **k):
            return {}
        def send_message(self, *a, **k):
            return {"message_id": 1}

    app = BotApp(_FakeClient(), db)  # type: ignore[arg-type]
    uid = "998"
    app._site_stock_pending[uid] = {
        "group": "slab",
        "items": [{"id": "X1", "name_desc": "قلم تست"}],
        "values": {},
        "awaiting_idx": 0,
        "walk_idx": 0,
        "guided": True,
        "chat_id": 1,
        "message_id": 99,
    }
    msg = {"from": {"id": int(uid)}, "chat": {"id": 1}, "text": kb.BTN_BACK_SITE}
    assert app.on_site_stock_quantity_text(msg, kb.BTN_BACK_SITE) is False
    assert app.on_site_stock_quantity_text(msg, kb.BTN_SITE_CANCEL) is False
    assert app.on_site_stock_quantity_text(msg, kb.BTN_SITE_SKIP) is False
    assert app.on_site_stock_quantity_text(msg, kb.BTN_BACK_PREV) is False
    assert app.on_site_stock_quantity_text(msg, kb.BTN_SITE_SLAB) is False
    # pending still locked until a real nav handler clears it
    assert uid in app._site_stock_pending and app._site_stock_pending[uid]["awaiting_idx"] == 0
    print("site_stock qty reserved-button unlock OK")

    print("SMOKE OK CRITICAL_DAYS=", CRITICAL_DAYS, "site_stock_date=", day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
