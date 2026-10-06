#!/usr/bin/env python3
"""Smoke: اقلام بحرانی split (شرکت / پیمانکار) × mode (با نوسازی / بدون نوسازی),
ledger applied ONCE, and A4-portrait PDFs for every generator (bot + web share
the same code path).

Self-contained: temp DB + synthetic منبع اصلی; never touches data/bot.db.
Page-1 PNGs are written to reports/smoke_portrait/ for visual checks.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

PNG_DIR = ROOT / "reports" / "smoke_portrait"

COMPANY_ID = "378112341302R"
CONTRACTOR_ID = "378700009002G"


def _inventory_frame() -> pd.DataFrame:
    zero = {
        "bloom_renovation": 0,
        "bloom_patching": 0,
        "slab_renovation": 0,
        "slab_patching": 0,
        "casting_floor": 0,
    }
    rows = [
        # same category on both sides — must NOT be merged
        dict(category_code="1203", id=COMPANY_ID, product_name="بتن 85 شرکت",
             keyword="بتن 85bt", quantity=1000, priority=1, unit="Kg",
             contractor_or_company="شرکت", billet_renovation=10, billet_patching=10, **zero),
        dict(category_code="1203", id=CONTRACTOR_ID, product_name="بتن پلی 85",
             keyword="بتن پلی 85", quantity=500, priority=1, unit="Kg",
             contractor_or_company="پیمانکار", usage_location="اسلب",
             billet_renovation=0, billet_patching=0,
             # real-data shape: the contractor row of 1203 is slab (a billet rate
             # here would be dropped — billet comes from the شرکت rows)
             **{**zero, "slab_renovation": 5, "slab_patching": 5}),
        # blank column + «0000» id → contractor (id fallback)
        dict(category_code="1655", id="378800001111A", product_name="روکش کشور",
             keyword="روکش کشور", quantity=300, priority=1, unit="Kg",
             contractor_or_company="", billet_renovation=3, billet_patching=0, **zero),
        # Arabic kaf/yeh variant + spaces → company
        dict(category_code="1450", id="378155551234B", product_name="بتن ملات",
             keyword="بتن ملات", quantity=50, priority=1, unit="Kg",
             contractor_or_company=" شركت ", billet_renovation=2, billet_patching=0, **zero),
        # casting floor only → listed in BOTH modes (cf × total tundishes)
        dict(category_code="1451", id="378166661234D", product_name="بتن 80 گان",
             keyword="بتن 80 گان", quantity=40, priority=1, unit="Kg",
             contractor_or_company="شرکت", billet_renovation=0, billet_patching=0,
             **{**zero, "casting_floor": 1}),
        # code 1700: mixed priorities. prio-0 row (stock 5000, rate 3) is unused →
        # excluded from stock AND rates; qty<100 rows still count; an unrated
        # sibling row still adds stock → stock 40+60=100, need 10×1=10
        dict(category_code="1700", id="378177771111E", product_name="نازل الف",
             keyword="نازل الف", quantity=40, priority=1, unit="No", origin="وارداتی",
             contractor_or_company="شرکت", billet_renovation=0, billet_patching=1, **zero),
        dict(category_code="1700", id="378177772222F", product_name="نازل قدیمی",
             keyword="نازل قدیمی", quantity=5000, priority=0, unit="No",
             contractor_or_company="شرکت", billet_renovation=0, billet_patching=3, **zero),
        dict(category_code="1700", id="378177773333G", product_name="نازل ب",
             keyword="نازل ب", quantity=60, priority=2, unit="No",
             contractor_or_company="شرکت", billet_renovation=0, billet_patching=0, **zero),
        # code 1800: only a priority-0 row carries the rate → not listed
        dict(category_code="1800", id="378188881111H", product_name="فقط اولویت صفر",
             keyword="فقط اولویت صفر", quantity=10, priority=0, unit="No",
             contractor_or_company="شرکت", billet_renovation=0, billet_patching=5, **zero),
        # no rates → never listed
        dict(category_code="9999", id="378199991234C", product_name="بدون نرخ",
             keyword="سایر", quantity=7, priority=1, unit="No",
             contractor_or_company="شرکت", billet_renovation=0, billet_patching=0, **zero),
    ]
    df = pd.DataFrame(rows)
    df["critical_point"] = ""
    # «سازنده»: 1203 company row domestic; 1700 imported (set above); others blank
    # → flagged «نامشخص», domestic horizon 3 months.
    df.loc[df["id"] == COMPANY_ID, "origin"] = "داخلی"
    return df


def _assert_portrait(pdf: Path) -> int:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    assert reader.pages, pdf
    for i, page in enumerate(reader.pages):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        rot = int(page.get("/Rotate") or 0) % 180
        if rot:
            w, h = h, w
        assert w < h, f"{pdf.name} page {i + 1} is landscape ({w:.0f}x{h:.0f})"
        assert abs(w - 595.3) < 2 and abs(h - 841.9) < 2, f"{pdf.name} not A4: {w}x{h}"
    return len(reader.pages)


def _png(pdf: Path, name: str) -> Path | None:
    if not shutil.which("pdftoppm"):
        return None
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    stem = PNG_DIR / name
    subprocess.run(
        ["pdftoppm", "-png", "-r", "70", "-f", "1", "-l", "1", "-singlefile", str(pdf), str(stem)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return stem.with_suffix(".png")


def test_classify() -> None:
    from analytics.critical_items import (
        SEGMENT_COMPANY as CO,
        SEGMENT_CONTRACTOR as CT,
        classify_contractor_or_company as cls,
    )

    assert cls("شرکت") == CO
    assert cls("پیمانکار") == CT
    assert cls(" پيمانكار ") == CT  # Arabic yeh/kaf
    assert cls("پیمان\u200cکار") == CT  # ZWNJ
    assert cls("شركت") == CO
    assert cls("Contractor") == CT and cls("company") == CO
    assert cls("", CONTRACTOR_ID) == CT and cls(None, COMPANY_ID) == CO
    assert cls("nan", "12") == CO  # unknown → company
    assert cls("فولاد خوزستان", CONTRACTOR_ID) == CT  # free text → id rule
    # Rule A (1405-07-14): شناسه مواد is AUTHORITATIVE over the cell
    assert cls("پیمانکار", "378121641252L") == CO and cls("شرکت", "378700009032J") == CT
    print("classify OK")


def test_id_rule_and_surplus(tmp: Path) -> None:
    """Rule A: importer/saves derive پیمانکار/شرکت from id + warn; rule B: 1800 never consumable."""
    from openpyxl import Workbook

    from analytics.critical_items import TundishMonthCounts, build_critical_items_rows
    from analytics.section_rules import section_inventory
    from excel.id_parse import apply_id_segment_rule, segment_mismatch_note_fa
    from excel.processor import extract_and_save_clean

    df = pd.DataFrame({
        "id": ["378121641252L", "378700009032J", "378700009002G", "12"],
        "category_code": ["1203", "1655", "1203", "1450"],
        "contractor_or_company": ["پیمانکار", "پیمانکار", "", "پیمانکار"],
    })
    out, mism = apply_id_segment_rule(df)
    assert list(out["contractor_or_company"]) == ["شرکت", "پیمانکار", "پیمانکار", "پیمانکار"], out
    assert [m["id"] for m in mism] == ["378121641252L"] and mism[0]["rule_label"] == "شرکت", mism
    note = segment_mismatch_note_fa(mism)
    assert "378121641252L" in note and "0000" in note, note

    # importer: file label disagrees → corrected + reported in ExtractResult
    raw = tmp / "id_rule_inv.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "ریز اطلاعات"
    # t209u: header renamed «تأمین‌کننده» (old «پیمانکار / شرکت» still accepted — merge fixture)
    ws.append(["کد دسته بندی", "شناسه مواد", "شرح کالا", "موجودی", "اولویت", "تأمین‌کننده",
               "نوسازی تاندیش بیلت", "واحد"])
    ws.append([1203, "378121641252L", "NANAREF C85", 33920, 3, "پیمانکار", 200, "Kg"])
    ws.append([1203, "378700009002G", "POLY 85", 6000, 1, "پیمانکار", 0, "Kg"])
    ws.append([1800, "378124311162N", "DIRCAST M3", 17800, 2, "شرکت", 500, "Kg"])
    wb.save(raw)
    res = extract_and_save_clean(raw, "product_inventory", category_allowlist={"1203", "1800"},
                                 clean_dir=tmp / "id_rule_clean")
    assert [m["id"] for m in res.segment_mismatches] == ["378121641252L"], res.segment_mismatches
    clean = pd.read_excel(res.clean_path)
    lab = dict(zip(clean["id"], clean["contractor_or_company"]))
    assert lab["378121641252L"] == "شرکت" and lab["378700009002G"] == "پیمانکار", lab

    # rule B: 1800 with priority≠0 and a billet rate is still NOT a critical item / consumable
    cnt = TundishMonthCounts(1405, 6, 10, 0, 0)
    crit = build_critical_items_rows(clean, cnt, segment="company", include_covered=True)
    codes = {str(c) for c in crit["کد چهاررقمی"]}
    assert "1203" in codes and "1800" not in codes, codes
    billet = section_inventory(clean, "billet")
    assert "1800" not in {str(c) for c in billet["category_code"]}, billet
    # t209u label rename: every user-facing header says «تأمین‌کننده»
    from excel.processor import _normalize_columns
    from pdf.generator import HEADER_FA
    from services.main_source import FIELD_LABELS_FA

    assert FIELD_LABELS_FA["contractor_or_company"] == "تأمین‌کننده"
    assert HEADER_FA["contractor_or_company"] == "تأمین‌کننده"
    for hdr in ("تأمین‌کننده", "تامین کننده", "پیمانکار / شرکت", "شرکت/پیمانکار"):
        assert list(_normalize_columns(pd.DataFrame(columns=[hdr])).columns) == ["contractor_or_company"], hdr
    assert "تأمین‌کننده" in note
    print("id rule (authoritative + upload warning) + 1800 surplus exclusion + «تأمین‌کننده» header OK")


def test_aggregation_rule() -> None:
    """Per 4-digit code: drop priority 0, no qty<100 filter, stock over all rows."""
    from analytics.critical_items import COL_MONTHLY, TundishMonthCounts, build_critical_items_rows
    from analytics.tundish import critical_point_category_totals

    inv = _inventory_frame()
    cnt = TundishMonthCounts(1405, 6, 10, 0, 0)
    df = build_critical_items_rows(inv, cnt, segment="company", include_covered=True)
    got = {str(r["کد چهاررقمی"]): (int(r["موجودی"]), int(r[COL_MONTHLY])) for _, r in df.iterrows()}
    assert got["1700"] == (100, 10), got  # 40 (<100) + 60 (unrated); prio-0 5000/3 excluded
    assert got["1450"] == (50, 20), got  # qty 50 < 100 still counted
    assert "1800" not in got and "9999" not in got, got
    # explicit critical point: alert when summed stock (100) ≤ cp
    inv2 = inv.copy()
    inv2.loc[inv2["category_code"] == "1700", "critical_point"] = "150"
    r = build_critical_items_rows(inv2, cnt, segment="company", include_covered=True)
    r = r.loc[r["کد چهاررقمی"].astype(str) == "1700"].iloc[0]
    assert bool(r["below_threshold"]) and float(r["filtered_stock"]) == 100.0
    tot = critical_point_category_totals(inv)
    tmap = dict(zip(tot["category_code"], tot["total_quantity"]))
    assert tmap["1700"] == 100.0 and tmap["1450"] == 50.0 and "1800" not in tmap, tmap
    print("aggregation rule (priority≠0, no <100 filter, all rows) OK")


def test_horizon_rule() -> None:
    """t206u/t207u: نیاز = max(0, monthly avg × H − stock); H 3 (داخلی) / 6 (وارداتی);
    monthly = 3-month AVERAGE of manual tundish counts; only نیاز > 0 listed."""
    from analytics.critical_items import (
        COL_FORECAST,
        COL_HORIZON,
        COL_MONTHLY,
        COL_NEED,
        COL_ORIGIN,
        REPORT_COLUMNS,
        average_tundish_basis,
        build_critical_items_rows,
        horizon_header_note,
        origin_audit,
        origin_notes,
    )

    assert COL_MONTHLY == "میانگین مصرف ماهانه بر اساس ۳ ماه گذشته" and COL_MONTHLY in REPORT_COLUMNS
    for c in ("مبدأ", "افق (ماه)", "مصرف پیش‌بینی‌شده در افق", "نیاز"):
        assert c in REPORT_COLUMNS, c
    stored = [
        {"jalali_year": 1405, "jalali_month": m, "count_billet": b, "count_bloom": 0, "count_slab": s}
        for m, b, s in ((3, 1000, 1000), (4, 40, 40), (5, 50, 50), (6, 60, 60))
    ]
    cnt = average_tundish_basis(1405, 6, stored)
    assert (cnt.count_billet, cnt.count_slab) == (50, 50), cnt  # month 3 (1000) is outside
    assert [m[1] for m in cnt.months()] == [4, 5, 6]
    note = horizon_header_note(cnt)
    assert "۳ ماه آینده" in note and "۶ ماه آینده" in note and "تیر تا شهریور 1405" in note, note
    one = average_tundish_basis(1405, 6, stored[-1:])
    assert one.count_billet == 60 and "فقط 1 ماه از ۳ ماه کامل" in horizon_header_note(one)
    assert average_tundish_basis(1405, 10, stored) is None  # nothing in مهر..دی window
    # year wrap: فروردین 1406 averages اسفند/بهمن 1405
    wrap = average_tundish_basis(1406, 1, [
        {"jalali_year": 1405, "jalali_month": 12, "count_billet": 10, "count_bloom": 0, "count_slab": 0},
        {"jalali_year": 1406, "jalali_month": 1, "count_billet": 20, "count_bloom": 0, "count_slab": 0}])
    assert wrap.count_billet == 15 and len(wrap.months()) == 2

    def r(code, iid, qty, origin, rate=2):
        return dict(category_code=code, id=iid, product_name=f"کالا {code}", keyword=f"قلم {code}",
                    quantity=qty, priority=1, unit="No", contractor_or_company="شرکت", origin=origin,
                    billet_renovation=0, billet_patching=rate, bloom_renovation=0, bloom_patching=0,
                    slab_renovation=0, slab_patching=0, casting_floor=0, critical_point="")

    inv = pd.DataFrame([
        r("2001", "378120010012A", 250, "داخلی"),    # monthly 100 → 300 − 250 = 50 listed
        r("2002", "378120020012B", 300, "داخلی"),    # 300 − 300 = 0 → NOT listed
        r("2003", "378120030012C", 500, "وارداتی"),  # 600 − 500 = 100 listed (domestic would hide it)
        r("2004", "378120040012D", 100, ""),         # missing origin → domestic 3, flagged
        r("2005", "378120050012E", 100, "داخلی"),    # mixed origins → H 6
        r("2005", "378120050022F", 0, "وارداتی"),
        r("2006", "378120060012G", 10, "داخلی", rate=0),  # no rate → never
    ])
    cnt = average_tundish_basis(1405, 6, stored)
    df = build_critical_items_rows(inv, cnt, segment="company")
    rows = {str(x["کد چهاررقمی"]): x for _, x in df.iterrows()}
    assert set(rows) == {"2001", "2003", "2004", "2005"}, set(rows)
    a = rows["2001"]
    assert (a[COL_ORIGIN], int(a[COL_HORIZON]), int(a[COL_MONTHLY]), int(a[COL_FORECAST]), int(a[COL_NEED])) == (
        "داخلی", 3, 100, 300, 50), a.to_dict()
    assert int(a["حد تحمل(روز)"]) == 75  # 250 / (100/30)
    c = rows["2003"]
    assert (c[COL_ORIGIN], int(c[COL_HORIZON]), int(c[COL_FORECAST]), int(c[COL_NEED])) == ("وارداتی", 6, 600, 100)
    assert int(rows["2004"][COL_HORIZON]) == 3 and rows["2004"]["origin_flag"] == "missing"
    assert int(rows["2005"][COL_HORIZON]) == 6 and int(rows["2005"][COL_NEED]) == 500
    # most urgent first (fewest days of cover)
    assert list(df["کد چهاررقمی"].astype(str)) == ["2004", "2005", "2001", "2003"], list(df["کد چهاررقمی"])
    cov = build_critical_items_rows(inv, cnt, segment="company", include_covered=True)
    assert int(cov.loc[cov["کد چهاررقمی"].astype(str) == "2002", COL_NEED].iloc[0]) == 0
    audit = origin_audit(inv, "company")
    assert audit == {"missing": ["2004"], "mixed": ["2005"]}, audit
    notes = " ".join(origin_notes(audit))
    assert "2004" in notes and "2005" in notes and "داخلی" in notes
    print("horizon rule (3-month avg, H 3/6, نیاز>0 only, origin flags) OK")


MERGE_HEADERS = [
    "کد دسته بندي", "شناسه مواد", "شرح کالا", "شماره دستور کار", "محل استفاده",
    "کلید واژه", "موجودي", "اولویت", "پیمانکار / شرکت", "سازنده", "اشتراکی",
    "نقطه بحرانی", "واحد", "سطح ریخته گری", "نوسازی تاندیش بیلت ",
    "پچینگ تاندیش بیلت", "نوسازی تاندیش بلوم ", "پچینگ تاندیش بلوم",
    "نوسازی تاندیش اسلب ", "پچینگ تاندیش اسلب",
]


def _merged_fixture(path: Path) -> None:
    """«ریز اطلاعات» with vertically merged cells (plant export style).

    Rows (excel row → code, id, qty, prio, segment):
      2-4  1901 company: billet patching P2:P4=5 and نقطه بحرانی L2:L4=80
           merged; qty 40 (p1) / 500 (p0) / 30 (p2)
      5-6  1902 company: موجودی G5:G6=300 merged (per-row → once), rate on row 5
      7-8  1903 company qty 100 / 1904 company qty 200: billet renovation +
           patching O7:P8=6 merged ACROSS two codes → shared need group
      9-10 1905 contractor qty 10 / 1905 company qty 20: slab patching T9:T10=2
           merged ACROSS segments → each segment keeps rate 2
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "ریز اطلاعات"
    ws.append(MERGE_HEADERS)

    def row(code, iid, qty, prio, seg, **rates):
        vals = {h: None for h in MERGE_HEADERS}
        vals.update({
            "کد دسته بندي": code, "شناسه مواد": iid, "شرح کالا": f"کالا {iid}",
            "کلید واژه": f"قلم {code}", "موجودي": qty, "اولویت": prio,
            "پیمانکار / شرکت": seg, "واحد": "No", "سطح ریخته گری": 0,
        })
        for h in MERGE_HEADERS[14:]:
            vals[h] = 0
        vals.update(rates)
        ws.append([vals[h] for h in MERGE_HEADERS])

    row("1901", "378190100012A", 40, 1, "شرکت", **{"پچینگ تاندیش بیلت": 5, "نقطه بحرانی": 80})
    row("1901", "378190100022B", 500, 0, "شرکت", **{"پچینگ تاندیش بیلت": None})
    row("1901", "378190100032C", 30, 2, "شرکت", **{"پچینگ تاندیش بیلت": None})
    row("1902", "378190200012D", 300, 1, "شرکت", **{"پچینگ تاندیش بیلت": 1})
    row("1902", "378190200022E", None, 2, "شرکت")
    row("1903", "378190300012F", 100, 1, "شرکت", **{"نوسازی تاندیش بیلت ": 6, "پچینگ تاندیش بیلت": 6})
    row("1904", "378190400012G", 200, 1, "شرکت", **{"نوسازی تاندیش بیلت ": None, "پچینگ تاندیش بیلت": None})
    # (محل استفاده «اسلب»: a شرکت row of a code with both sides feeds slab only then)
    row("1905", "378700009012H", 10, 1, "پیمانکار", **{"پچینگ تاندیش اسلب": 2, "محل استفاده": "اسلب"})
    row("1905", "378190500012I", 20, 1, "شرکت", **{"پچینگ تاندیش اسلب": None, "محل استفاده": "اسلب"})
    for rng in ("P2:P4", "L2:L4", "G5:G6", "O7:O8", "P7:P8", "T9:T10"):
        ws.merge_cells(rng)
    wb.save(path)


def test_merged_cells(tmp: Path) -> None:
    """Importer fills merged ranges; per-code values once; shared-need groups."""
    import hashlib

    from analytics.critical_items import (
        COL_MONTHLY,
        ROW_GROUP,
        ROW_MEMBER,
        SHARED_NEED_LABEL,
        TundishMonthCounts,
        build_critical_items_rows,
        critical_item_count,
        group_row_indices,
    )
    from excel.processor import _normalize_columns, extract_and_save_clean
    from services.main_source import ensure_inventory_columns

    src = tmp / "merged_source.xlsx"
    _merged_fixture(src)
    digest = hashlib.sha1(src.read_bytes()).hexdigest()
    res = extract_and_save_clean(
        src, "product_inventory",
        category_allowlist=["1901", "1902", "1903", "1904", "1905"],
        clean_dir=tmp / "merged_clean",
    )
    assert hashlib.sha1(src.read_bytes()).hexdigest() == digest, "source file modified"
    assert res.kept_row_count == 9, res.drop_reasons
    inv = ensure_inventory_columns(_normalize_columns(pd.read_excel(res.clean_path)))
    by_id = {str(r["id"]): r for _, r in inv.iterrows()}
    # fill: rate + critical point copied into every covered row
    assert all(float(by_id[i]["billet_patching"]) == 5 for i in
               ("378190100012A", "378190100022B", "378190100032C")), inv
    assert float(by_id["378190100032C"]["critical_point"]) == 80
    # per-row stock NOT copied (second row 0)
    assert float(by_id["378190200022E"]["quantity"]) == 0
    assert float(by_id["378190500012I"]["slab_patching"]) == 2
    assert str(by_id["378190400012G"]["rate_group"]).startswith("G7-8:billet_renovation,billet_patching")
    assert str(by_id["378190100012A"]["rate_group"]) in {"", "nan", "None"}

    cnt = TundishMonthCounts(1405, 6, 10, 0, 1)
    co = build_critical_items_rows(inv, cnt, segment="company", shared_mode="pooled", include_covered=True)
    rows = {str(r["کد چهاررقمی"]): r for _, r in co.iterrows()}
    # 1901: patching 5 once (not 5×3), stock 40+30 (p0 500 excluded), cp 80 → below
    assert int(rows["1901"][COL_MONTHLY]) == 50 and int(rows["1901"]["موجودی"]) == 70, rows["1901"]
    assert bool(rows["1901"]["below_threshold"])
    # 1902: merged stock counted once
    assert int(rows["1902"]["موجودی"]) == 300 and int(rows["1902"][COL_MONTHLY]) == 10
    # 1903/1904 pooled: one group row, need (6+6)×10 once, combined stock
    g = rows["1903/1904"]
    assert g["row_kind"] == ROW_GROUP and int(g[COL_MONTHLY]) == 120 and int(g["موجودی"]) == 300, g
    assert rows["1903"]["row_kind"] == ROW_MEMBER and rows["1903"][COL_MONTHLY] == SHARED_NEED_LABEL
    assert int(rows["1903"]["موجودی"]) == 100 and int(rows["1904"]["موجودی"]) == 200
    pos = list(co["کد چهاررقمی"].astype(str))
    assert pos.index("1903/1904") + 1 == pos.index("1903") and pos.index("1904") == pos.index("1903") + 1
    assert group_row_indices(co) == [pos.index("1903/1904")]
    assert critical_item_count(co) == 4, co  # 1901, 1902, group, 1905 (members not counted)
    # 1905 company keeps the merged slab rate (cross-segment fill, no pooling)
    assert int(rows["1905"][COL_MONTHLY]) == 2 and int(rows["1905"]["موجودی"]) == 20
    ct = build_critical_items_rows(inv, cnt, segment="contractor", shared_mode="pooled", include_covered=True)
    crow = ct.loc[ct["کد چهاررقمی"].astype(str) == "1905"].iloc[0]
    assert int(crow[COL_MONTHLY]) == 2 and int(crow["موجودی"]) == 10, ct
    # alternative mode: every code gets the full rate separately
    pc = build_critical_items_rows(inv, cnt, segment="company", shared_mode="per_code", include_covered=True)
    prow = {str(r["کد چهاررقمی"]): r for _, r in pc.iterrows()}
    assert "1903/1904" not in prow and int(prow["1903"][COL_MONTHLY]) == 120 and int(prow["1904"][COL_MONTHLY]) == 120
    assert critical_item_count(pc) == 5
    print("merged cells (fill, stock once, per-code max, pooled vs per_code, segments) OK")


def test_section_rules() -> None:
    """Code with شرکت + پیمانکار rows: billet=company, bloom=contractor, slab=
    contractor + company rows located «اسلب» (critical items + main goal)."""
    import services.main_goal_report as mg
    from analytics.critical_items import COL_MONTHLY, TundishMonthCounts, build_critical_items_rows
    from analytics.section_rules import (
        apply_section_rate_attribution,
        codes_with_both_segments,
        section_inventory,
    )
    from services.main_source import ensure_inventory_columns

    def r(code, iid, seg, loc, qty, prio=1, **rates):
        base = {
            "category_code": code, "id": iid, "product_name": f"کالا {iid}",
            "keyword": f"قلم {code}", "quantity": qty, "priority": prio,
            "contractor_or_company": seg, "usage_location": loc, "unit": "No",
        }
        base.update({k: 0 for k in (
            "casting_floor", "billet_renovation", "billet_patching",
            "bloom_renovation", "bloom_patching", "slab_renovation", "slab_patching")})
        base.update(rates)
        return base

    inv = ensure_inventory_columns(pd.DataFrame([
        # 1637-like: rates merge-filled into BOTH rows
        r("1937", "378700003002A", "پیمانکار", "بلوم", 192,
          billet_patching=1, bloom_renovation=2, bloom_patching=2),
        r("1937", "378193700012B", "شرکت", "بیلت", 151,
          billet_patching=1, bloom_renovation=2, bloom_patching=2),
        # slab: company row located اسلب counts, company row located بیلت does not
        r("1938", "378700009012H", "پیمانکار", "اسلب", 50, slab_patching=3),
        r("1938", "378193800012C", "شرکت", "اسلب", 60, slab_patching=3),
        r("1938", "378193800022D", "شرکت", "بیلت", 70, slab_patching=3, billet_patching=4),
        # company rows of 1939 all priority 0 → not «both»; contractor unchanged
        r("1939", "378700009022J", "پیمانکار", "بیلت", 10, billet_patching=5),
        r("1939", "378193900012E", "شرکت", "بیلت", 20, prio=0, billet_patching=5),
        # single-segment company code: bloom rate kept
        r("1940", "378194000012F", "شرکت", "بلوم", 30, bloom_patching=1),
    ]))
    assert codes_with_both_segments(inv) == {"1937", "1938"}
    _, changes = apply_section_rate_attribution(inv)
    dropped = {(c["code"], c["segment"], c["column"]) for c in changes}
    assert dropped == {
        ("1937", "contractor", "billet_patching"),
        ("1937", "company", "bloom_renovation"),
        ("1937", "company", "bloom_patching"),
        ("1938", "company", "slab_patching"),  # the بیلت-located company row
    }, dropped
    cnt = TundishMonthCounts(1405, 6, 10, 10, 10)
    co = build_critical_items_rows(inv, cnt, segment="company", include_covered=True)
    ct = build_critical_items_rows(inv, cnt, segment="contractor", include_covered=True)
    need = lambda df, code: int(df.loc[df["کد چهاررقمی"].astype(str) == code, COL_MONTHLY].iloc[0])
    assert need(co, "1937") == 10 and need(ct, "1937") == 40  # billet 1×10 | bloom 4×10
    assert need(co, "1938") == 30 + 40 and need(ct, "1938") == 30  # slab 3×10 once + billet 4×10
    assert need(ct, "1939") == 50 and need(co, "1940") == 10
    # main goal / forecast: section-restricted matching by id
    line = mg.MaterialLine(name="x", quantity=1, unit="", item_id="378700003002A")
    assert mg.match_inventory_stock(line, inv, section="bloom")[3] == 192
    assert mg.match_inventory_stock(line, inv, section="billet")[3] is None
    sl = section_inventory(inv, "slab")
    assert set(sl["id"]) >= {"378700009012H", "378193800012C"} and "378193800022D" not in set(sl["id"])
    print("section rules (billet=شرکت, bloom=پیمانکار, slab=اسلب) OK")


def _today_tag() -> str:
    from bot.jalali import jalali_today

    t = jalali_today()
    return f"{t.year}-{t.month:02d}-{t.day:02d}"


def _basis_months() -> list[tuple[int, int]]:
    from analytics.critical_items import previous_complete_months
    from bot.jalali import jalali_today

    t = jalali_today()
    return previous_complete_months(t.year, t.month)


def _basis_label() -> str:
    from analytics.critical_items import basis_from_months

    return basis_from_months([(y, m, 0, 0, 0, "manual") for y, m in _basis_months()]).basis_label()


def test_upsert_critical_point(tmp: Path) -> None:
    """Sanctioned edit path writes «نقطه بحرانی» even when the column is all empty
    (float64 NaN) — t211s979 bug: TypeError on assigning a string."""
    import _smoke_isolation
    from db.models import Database
    from services.main_source import load_primary_frame, persist_primary_frame, upsert_row

    import services.main_source as ms

    _smoke_isolation.isolate_uploads()
    assert ms.UPLOAD_DIR == _smoke_isolation.SMOKE_UPLOAD_DIR
    db = Database(tmp / "upsert.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")
    inv = _inventory_frame()
    inv["critical_point"] = float("nan")
    persist_primary_frame(db, inv, bale_user_id="901")
    before = load_primary_frame(db, bale_user_id="901")
    assert before["critical_point"].isna().all()
    upsert_row(db, COMPANY_ID, {"critical_point": 31500}, bale_user_id="901")
    upsert_row(db, "378177771111E", {"critical_point": 0}, bale_user_id="901")
    after = load_primary_frame(db, bale_user_id="901")
    cp = dict(zip(after["id"].astype(str), after["critical_point"]))
    assert float(cp[COMPANY_ID]) == 31500 and float(cp["378177771111E"]) == 0, cp
    assert pd.isna(cp["378199991234C"]) or str(cp["378199991234C"]) in {"", "nan"}, cp
    for col in ("quantity", "priority", "billet_renovation", "product_name"):
        assert list(after[col].astype(str)) == list(before[col].astype(str)), col
    print("upsert_row «نقطه بحرانی» on empty column OK")


def _inv_upload_xlsx(path: Path, rows: list[tuple]) -> Path:
    from openpyxl import Workbook

    wb = Workbook(); ws = wb.active; ws.title = "ریز اطلاعات"
    ws.append(["کد دسته بندی", "شناسه مواد", "شرح کالا", "موجودی", "اولویت", "تأمین‌کننده",
               "نوسازی تاندیش بیلت", "واحد"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_inventory_upload_rules(tmp: Path) -> None:
    """t213u/t214u: منبع اصلی is the reference. Stock/warehouse upload never adds a
    new 4-digit code; no file upload auto-adds a NEW 1800 row; existing ids (incl.
    1800) still get stock updates; full-source upload by an authorized user may add
    new codes. Skipped rows are listed in the bot and web upload summary."""
    import _smoke_isolation
    import bot.handlers as handlers_mod
    from db.models import Database
    from services.main_source import (
        filter_inventory_upload,
        load_primary_frame,
        persist_primary_frame,
        skipped_rows_note_fa,
    )

    _smoke_isolation.isolate_uploads()
    base = pd.DataFrame([
        dict(category_code="1203", id=COMPANY_ID, product_name="بتن 85", quantity=100, priority=1,
             contractor_or_company="شرکت", billet_renovation=10, unit="Kg"),
        dict(category_code="1800", id="378124311111A", product_name="مازاد قدیمی", quantity=5,
             priority=0, contractor_or_company="شرکت", unit="Kg"),
    ])
    new = pd.DataFrame([
        dict(category_code=1203, id=COMPANY_ID, quantity=150),          # existing → update
        dict(category_code=1203, id="378112342222B", quantity=7),       # new id, known code → add
        dict(category_code=1999, id="378119991234C", quantity=9),       # new code
        dict(category_code=1800, id="378124312222D", quantity=3),       # new 1800 row
        dict(category_code=1800, id="378124311111A", quantity=8),       # existing 1800 → update
    ])
    kept, skipped = filter_inventory_upload(base, new, allow_new_codes=False)
    assert list(kept["id"]) == [COMPANY_ID, "378112342222B", "378124311111A"], kept
    assert [(s["id"], s["category_code"]) for s in skipped] == [("378119991234C", "1999"), ("378124312222D", "1800")]
    kept2, skipped2 = filter_inventory_upload(base, new, allow_new_codes=True)
    assert "378119991234C" in set(kept2["id"]) and [s["id"] for s in skipped2] == ["378124312222D"]
    assert filter_inventory_upload(None, new, allow_new_codes=False) == (new, [])  # first load
    note = skipped_rows_note_fa(skipped)
    assert "378119991234C" in note and "1800" in note and "⛔ 2 ردیف" in note, note

    # --- bot: «موجودی انبار» (stock update) vs «ورود فایل اکسل منبع اصلی» (full source)
    db = Database(tmp / "invrules.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")
    db.add_category_code("1999", created_by="901")
    user = db.get_user("901")
    persist_primary_frame(db, base, bale_user_id="901")
    src = _inv_upload_xlsx(tmp / "stock.xlsx", [
        (1203, COMPANY_ID, "بتن 85", 150, 1, "شرکت", 10, "Kg"),
        (1203, "378112342222B", "بتن 85 ب", 7, 1, "شرکت", 0, "Kg"),
        (1999, "378119991234C", "کد جدید", 9, 1, "شرکت", 0, "No"),
        (1800, "378124312222D", "مازاد جدید", 3, 0, "شرکت", 0, "Kg"),
        (1800, "378124311111A", "مازاد قدیمی", 8, 0, "شرکت", 0, "Kg"),
    ])

    class FakeClient:
        def __init__(self) -> None:
            self.msgs: list[str] = []

        def download_file(self, file_id, dest):
            shutil.copy2(src, dest)
            return Path(dest)

        def send_message(self, chat_id, text, reply_markup=None, **_kw):
            self.msgs.append(text)
            return {"ok": True}

        def __getattr__(self, name):
            return lambda *a, **k: {"ok": True}

    client = FakeClient()
    app = handlers_mod.BotApp(client, db)
    msg = {"chat": {"id": 901}, "from": {"id": 901, "first_name": "مالک"}, "text": ""}
    doc = dict(msg, document={"file_id": "f1", "file_name": "stock.xlsx"})

    def ids_qty() -> dict[str, float]:
        f = load_primary_frame(db, bale_user_id="901")
        return {str(i): float(q) for i, q in zip(f["id"], f["quantity"])}

    app.on_pick_file_type(msg, "product_inventory", return_menu="upload")
    app.on_document(doc)
    got = ids_qty()
    assert got.get(COMPANY_ID) == 150 and got.get("378124311111A") == 8, got  # updates incl. 1800
    assert "378112342222B" in got, got  # known code, new id
    assert "378119991234C" not in got and "378124312222D" not in got, got
    reply = client.msgs[-1]
    assert "⛔ 2 ردیف اضافه نشد" in reply and "378119991234C" in reply and "378124312222D" in reply, reply

    app.on_pick_file_type(msg, "product_inventory", return_menu="main_source")
    app.on_document(doc)
    got = ids_qty()
    assert "378119991234C" in got and "378124312222D" not in got, got  # full source: new code ok, 1800 never
    assert "⛔ 1 ردیف اضافه نشد" in client.msgs[-1], client.msgs[-1]

    # --- web settings upload (authorized full source): same shared rule
    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app

    db2 = Database(tmp / "invrules_web.db")
    db2.upsert_user("902", role="owner", display_name="مالک وب")
    db2.add_category_code("1999", created_by="902")
    persist_primary_frame(db2, base, bale_user_id="902")
    deps._db = db2
    wapp = create_app()
    wapp.dependency_overrides[deps.current_user_optional] = lambda: db2.get_user("902")
    wapp.dependency_overrides[deps.current_user] = lambda: db2.get_user("902")
    c = TestClient(wapp)
    r = c.post("/settings/main-source/upload",
               files={"file": ("stock.xlsx", src.read_bytes(),
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, (r.status_code, r.text[-1500:])
    f = load_primary_frame(db2, bale_user_id="902")
    wids = set(f["id"].astype(str))
    assert "378119991234C" in wids and "378124312222D" not in wids, wids
    assert "378124312222D" in r.text and "اضافه نشد" in r.text
    print("inventory upload rules (no new codes on stock update, no auto 1800, bot+web) OK")


def test_basis_sequence_log(tmp: Path) -> None:
    """t211u: report date = today; basis = 3 complete months before it; sequence log
    (one sequence = one tundish use) preferred over manual counts; a manual SAMPLE
    entry flagged exclude_from_basis is kept for audit but never used."""
    from analytics.critical_items import horizon_header_note
    from db.models import Database
    from services.critical_items_report import critical_basis

    db = Database(tmp / "basis.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")

    def seqs(y, m, section, n):
        db.replace_main_goal_sequences(
            period_key=f"m:{y}-{m:02d}", section=section,
            rows=[{"machine": section, "tundish_no": str(i), "melt_count": 7} for i in range(n)],
            meta={"period_label": f"{m}/{y}", "year": y, "month": m, "bale_user_id": "901"},
        )

    # تیر: slab 63 / billet 35 / bloom 1 — مرداد: 60 / 30 — شهریور: 89 / 37 (real log)
    for m, sl, bi, bl in ((4, 63, 35, 1), (5, 60, 30, 0), (6, 89, 37, 0)):
        seqs(1405, m, "slab", sl)
        seqs(1405, m, "billet", bi)
        if bl:
            seqs(1405, m, "bloom", bl)
    # manual SAMPLE for شهریور (70/100) → excluded; manual مرداد → superseded by the log
    db.upsert_monthly_tundish_counts(jalali_year=1405, jalali_month=6, count_billet=70,
                                     count_bloom=0, count_slab=100, updated_by="901")
    assert db.set_monthly_tundish_counts_exclusion(1405, 6, exclude=True, note="نمونه")
    db.upsert_monthly_tundish_counts(jalali_year=1405, jalali_month=5, count_billet=999,
                                     count_bloom=0, count_slab=999, updated_by="901")
    assert db.sequence_tundish_counts(1405, 4) == {"slab": 63, "billet": 35, "bloom": 1}
    b = critical_basis(db, (1405, 7, 14))
    assert b.report_date == "1405/07/14" and [m[1] for m in b.months()] == [4, 5, 6], b
    assert abs(b.count_slab - 212 / 3) < 1e-9 and b.count_billet == 34 and abs(b.count_bloom - 1 / 3) < 1e-9, b
    assert set(b.sources()) == {"sequence_log"}
    note = horizon_header_note(b)
    assert "تیر تا شهریور 1405" in note and "اسلب 70.7، بیلت 34، بلوم 0.3 در ماه" in note, note
    assert "لاگ توالی تاندیش" in note and "تاریخ گزارش: 1405/07/14" in note, note
    # a month without a log falls back to a NON-excluded manual entry
    b2 = critical_basis(db, (1405, 6, 1))  # window خرداد..مرداد: خرداد has no data
    assert b2 is not None and [m[1] for m in b2.months()] == [4, 5], b2.months()  # 3,4,5 → 4,5 logged
    db.upsert_monthly_tundish_counts(jalali_year=1405, jalali_month=3, count_billet=12,
                                     count_bloom=0, count_slab=6, updated_by="901")
    b3 = critical_basis(db, (1405, 6, 1))
    assert [(m[1], m[5]) for m in b3.months()] == [(3, "manual"), (4, "sequence_log"), (5, "sequence_log")]
    assert "ثبت دستی" in horizon_header_note(b3)
    # excluded sample is never used: report on 1405/08/01 (مرداد..مهر) → مرداد+شهریور logs only
    b4 = critical_basis(db, (1405, 8, 1))
    assert [m[1] for m in b4.months()] == [5, 6] and "فقط 2 ماه" in horizon_header_note(b4)
    # re-entering a month manually clears the exclusion (the user meant it)
    db.upsert_monthly_tundish_counts(jalali_year=1405, jalali_month=6, count_billet=1,
                                     count_bloom=0, count_slab=1, updated_by="901")
    assert not db.get_monthly_tundish_counts(1405, 6)["exclude_from_basis"]
    # default report date = today (Jalali)
    t = critical_basis(db)
    assert t is None or t.report_date.replace("/", "-") == _today_tag()
    print("basis: today + 3 complete months, sequence log preferred, sample excluded OK")


def _setup_db(tmp: Path):
    from db.models import Database

    db = Database(tmp / "crit.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")
    user = db.get_user("901")
    inv_dir = tmp / "uploads"
    inv_dir.mkdir(parents=True, exist_ok=True)
    clean = inv_dir / "product_inventory.xlsx"
    _inventory_frame().to_excel(clean, index=False)
    db.save_extracted("901", None, "product_inventory", str(clean), str(clean), 5)
    # the 3 complete months before TODAY (report date) → average billet 50, slab 50
    # (1203 پیمانکار is a slab row). No sequence log here → manual counts are the basis.
    for (y, m), n in zip(_basis_months(), (40, 50, 60)):
        db.upsert_monthly_tundish_counts(
            jalali_year=y, jalali_month=m, count_billet=n, count_bloom=0,
            count_slab=n, updated_by="901",
        )
    # one ledger deduction on the company row: -100
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO inventory_ledger
               (item_key, item_id, item_name, delta, reason, ref_type, ref_id,
                bale_user_id, actor_display_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (COMPANY_ID, COMPANY_ID, "بتن 85 شرکت", -100.0, "smoke", "smoke", 1,
             "901", "smoke", datetime.now(timezone.utc).isoformat()),
        )
    return db, user


def test_service_split_and_ledger(tmp: Path) -> dict:
    from analytics.critical_items import contractor_column_summary
    from analytics.frames import load_extract_frame, load_primary_inventory
    from services.critical_items_report import generate_critical_items_files

    db, user = _setup_db(tmp)
    raw = load_extract_frame(db, "product_inventory", user)
    inv = load_primary_inventory(db, user)
    q_raw = float(raw.loc[raw["id"] == COMPANY_ID, "quantity"].iloc[0])
    q_inv = float(inv.loc[inv["id"] == COMPANY_ID, "quantity"].iloc[0])
    assert q_raw == 1000 and q_inv == 900, (q_raw, q_inv)
    summary = {(r["value"], r["segment"]): r["rows"] for r in contractor_column_summary(raw)}
    assert summary.get(("شرکت", "company")) == 7 and summary.get(("پیمانکار", "contractor")) == 1, summary

    res = generate_critical_items_files(
        db, user, output_dir=tmp / "out", file_prefix="crit",
    )
    assert res.error is None, res.error
    assert res.reno_mode == "with" and "با نوسازی" in res.titles["company"]
    co, ct = res.frames["company"], res.frames["contractor"]
    co_codes = list(co["کد چهاررقمی"].astype(str))
    ct_codes = list(ct["کد چهاررقمی"].astype(str))
    assert sorted(co_codes) == ["1203", "1450", "1451", "1700"], co_codes
    from analytics.critical_items import COL_HORIZON, COL_MONTHLY, COL_NEED

    r1700 = co.loc[co["کد چهاررقمی"].astype(str) == "1700"].iloc[0]
    # 1700 imported: monthly 1×50, H 6 → forecast 300 − stock 100 = نیاز 200
    assert int(r1700["موجودی"]) == 100 and int(r1700[COL_MONTHLY]) == 50, r1700.to_dict()
    assert int(r1700[COL_HORIZON]) == 6 and int(r1700[COL_NEED]) == 200, r1700.to_dict()
    assert r1700["کد و شرح کالا"] == "نازل الف"
    assert sorted(ct_codes) == ["1203", "1655"], ct_codes
    r_co = co.loc[co["کد چهاررقمی"].astype(str) == "1203"].iloc[0]
    r_ct = ct.loc[ct["کد چهاررقمی"].astype(str) == "1203"].iloc[0]
    # company: ledger applied once (1000-100=900, NOT 800); monthly = 20×50; H 3
    assert int(r_co["موجودی"]) == 900, r_co["موجودی"]
    assert int(r_co[COL_MONTHLY]) == 1000 and int(r_co[COL_NEED]) == 3000 - 900
    assert int(r_co["حد تحمل(روز)"]) == 27  # 900 / (1000/30)
    # contractor: own stock/rates only (slab 10×50; origin blank → domestic)
    assert int(r_ct["موجودی"]) == 500 and int(r_ct[COL_MONTHLY]) == 500 and int(r_ct[COL_NEED]) == 1000
    assert _basis_label() in res.header_note, res.header_note
    assert f"تاریخ گزارش: {_today_tag().replace('-', '/')}" in res.header_note, res.header_note
    assert _today_tag().replace("-", "/") in res.titles["company"], res.titles
    assert _today_tag() in res.company_pdf.name, res.company_pdf.name
    assert "1655" in res.audit["missing"] and "1700" not in res.audit["missing"], res.audit
    assert "9999" not in res.audit["missing"], res.audit  # unrated codes are not audited
    assert "شرکت" in res.titles["company"] and "پیمانکار" in res.titles["contractor"]
    assert res.company_pdf and res.contractor_pdf and res.xlsx
    from openpyxl import load_workbook

    def _sheet_text(ws) -> str:
        return " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)

    wb = load_workbook(res.xlsx)
    assert wb.sheetnames == ["اقلام بحرانی — شرکت", "اقلام بحرانی — پیمانکار", "توضیحات"], wb.sheetnames
    assert "با نوسازی" in _sheet_text(wb["اقلام بحرانی — شرکت"])
    assert "«با نوسازی»" in _sheet_text(wb["توضیحات"])
    co_text = _sheet_text(wb["اقلام بحرانی — شرکت"])
    assert COL_MONTHLY in co_text and "۳ ماه آینده" in co_text and "مصرف پیش‌بینی‌شده در افق" in co_text
    assert "1655" in _sheet_text(wb["توضیحات"])  # missing-origin flag in notes
    for p in (res.company_pdf, res.contractor_pdf):
        _assert_portrait(p)
    assert "با_نوسازی" in res.company_pdf.name
    print("service split + ledger-once (با نوسازی) OK", co_codes, ct_codes)

    # «بدون نوسازی»: patching only (+ casting floor); renovation-only items drop
    wo = generate_critical_items_files(
        db, user, output_dir=tmp / "out",
        file_prefix="crit", reno_mode="without",
    )
    assert wo.error is None and wo.reno_mode == "without", wo.error
    wco, wct = wo.frames["company"], wo.frames["contractor"]
    wco_codes = sorted(wco["کد چهاررقمی"].astype(str))
    wct_codes = sorted(wct["کد چهاررقمی"].astype(str))
    assert wco_codes == ["1203", "1451", "1700"], wco_codes  # 1450 (reno only) dropped
    assert wct_codes == ["1203"], wct_codes  # 1655 (reno only) dropped
    n = lambda df, code: int(df.loc[df["کد چهاررقمی"].astype(str) == code, COL_MONTHLY].iloc[0])  # noqa: E731
    assert n(wco, "1203") == 500 and n(wct, "1203") == 250  # 50 × patching
    assert n(wco, "1451") == n(co, "1451") == 100  # casting floor (1 × 100 tundishes) same in both modes
    assert n(wco, "1700") == 50  # patching of the priority≠0 row only
    assert int(wco.loc[wco["کد چهاررقمی"].astype(str) == "1203", "موجودی"].iloc[0]) == 900
    assert "بدون نوسازی" in wo.titles["company"] and "بدون نوسازی" in wo.titles["contractor"]
    wb2 = load_workbook(wo.xlsx)
    assert "بدون نوسازی" in _sheet_text(wb2["اقلام بحرانی — پیمانکار"])
    assert "«بدون نوسازی»" in _sheet_text(wb2["توضیحات"])
    for p in (wo.company_pdf, wo.contractor_pdf):
        _assert_portrait(p)
    assert wo.company_pdf != res.company_pdf and wo.xlsx != res.xlsx
    # pure unit: need formula per mode
    from analytics.critical_items import TundishMonthCounts, monthly_need_for_rates

    cnt = TundishMonthCounts(1405, 6, 70, 0, 100)
    kw = dict(billet_renovation=200, billet_patching=50, bloom_renovation=9, bloom_patching=9,
              slab_renovation=170, slab_patching=30, casting_floor=1, counts=cnt)
    assert monthly_need_for_rates(**kw) == 70 * 250 + 100 * 200 + 170
    assert monthly_need_for_rates(**kw, reno_mode="without") == 70 * 50 + 100 * 30 + 170
    print("service بدون نوسازی OK", wco_codes, wct_codes)
    return {
        "critical_company": res.company_pdf,
        "critical_contractor": res.contractor_pdf,
        "critical_company_no_reno": wo.company_pdf,
        "critical_contractor_no_reno": wo.contractor_pdf,
    }


def test_bot_flow(tmp: Path) -> None:
    """Bot: «تولید گزارش اقلام بحرانی» → company PDF, contractor PDF, xlsx."""
    import config
    import bot.handlers as handlers_mod
    from bot import keyboards as kb

    db, user = _setup_db(tmp)
    handlers_mod.REPORT_DIR = tmp / "reports"
    config.REPORT_DIR = tmp / "reports"

    class FakeClient:
        def __init__(self) -> None:
            self.docs: list[tuple[Path, str]] = []
            self.msgs: list[str] = []

        def send_document(self, chat_id, path, caption=None, **_kw):
            self.docs.append((Path(path), caption or ""))
            return {"ok": True}

        def send_message(self, chat_id, text, reply_markup=None, **_kw):
            self.msgs.append(text)
            return {"ok": True}

        def __getattr__(self, name):  # any other API call → no-op
            return lambda *a, **k: {"ok": True}

    client = FakeClient()
    app = handlers_mod.BotApp(client, db)
    markups: list = []
    orig_send = client.send_message

    def send_message(chat_id, text, reply_markup=None, **kw):
        markups.append(reply_markup)
        return orig_send(chat_id, text, reply_markup=reply_markup, **kw)

    client.send_message = send_message
    msg = {"chat": {"id": 901}, "from": {"id": 901, "first_name": "مالک"}, "text": ""}

    def say(text: str) -> None:
        m = dict(msg, text=text)
        assert app.on_critical_flow_text(m, text), text

    # 1) «با نوسازی» via inline callback
    app.on_critical_report_start(dict(msg, text=kb.BTN_CRITICAL_REPORT))  # no year/month step
    assert "با نوسازی" in client.msgs[-1] and "بدون نوسازی" in client.msgs[-1]
    inline = markups[-1]["inline_keyboard"][0]
    datas = [b["callback_data"] for b in inline]
    assert datas == ["ci|reno|with", "ci|reno|without"], datas
    assert not client.docs  # nothing generated before the choice
    app.handle_callback_query({"id": "cq1", "data": datas[0], "from": msg["from"],
                               "message": {"message_id": 55, "chat": msg["chat"]}})
    names = [p.name for p, _ in client.docs]
    td = _today_tag()
    assert names == [f"لیست_اقلام_بحرانی_{td}_با_نوسازی_شرکت.pdf", f"لیست_اقلام_بحرانی_{td}_با_نوسازی_پیمانکار.pdf",
                     f"لیست_اقلام_بحرانی_{td}_با_نوسازی.xlsx"], names
    assert "گزارش اصلی" in client.docs[0][1] and "پیمانکار" in client.docs[1][1]
    assert "با نوسازی" in client.docs[0][1] and "با نوسازی" in client.docs[2][1]
    assert "حالت «با نوسازی»" in client.msgs[-1], client.msgs[-1]
    assert "شرکت (گزارش اصلی): 4 قلم" in client.msgs[-1], client.msgs[-1]
    assert "میانگین مصرف ماهانه بر اساس ۳ ماه گذشته: 1000" in client.msgs[-1], client.msgs[-1]
    assert "۶ ماه آینده" in client.msgs[-1] and "مبدأ نامشخص" in client.msgs[-1]
    for p, _ in client.docs[:2]:
        _assert_portrait(p)
    # stale callback after the report → friendly alert, no new docs
    n_docs = len(client.docs)
    app.handle_callback_query({"id": "cq2", "data": datas[1], "from": msg["from"],
                               "message": {"message_id": 55, "chat": msg["chat"]}})
    assert len(client.docs) == n_docs

    # 2) «بدون نوسازی» via typed label
    client.docs.clear()
    app.on_critical_report_start(dict(msg, text=kb.BTN_CRITICAL_REPORT))  # no year/month step
    say("چیز دیگر")  # invalid → re-ask
    assert not client.docs and "یکی از دو دکمه" in client.msgs[-1]
    say(kb.BTN_CRITICAL_RENO_WITHOUT)
    names = [p.name for p, _ in client.docs]
    assert names == [f"لیست_اقلام_بحرانی_{td}_بدون_نوسازی_شرکت.pdf", f"لیست_اقلام_بحرانی_{td}_بدون_نوسازی_پیمانکار.pdf",
                     f"لیست_اقلام_بحرانی_{td}_بدون_نوسازی.xlsx"], names
    assert "حالت «بدون نوسازی»" in client.msgs[-1]
    assert "شرکت (گزارش اصلی): 3 قلم" in client.msgs[-1] and "پیمانکار (گزارش جداگانه): 1 قلم" in client.msgs[-1], client.msgs[-1]
    print("bot critical flow (inline با نوسازی + typed بدون نوسازی) OK")


def test_web(tmp: Path) -> dict:
    from fastapi.testclient import TestClient

    import web.deps as deps
    import web.services.reports as web_reports
    from web.app import create_app

    db, _user = _setup_db(tmp)
    web_reports.REPORT_DIR = tmp / "web_reports"
    import services.critical_items_report as cir

    cir.REPORT_DIR = tmp / "web_reports"
    deps._db = db
    app = create_app()
    app.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("901")
    c = TestClient(app)
    out: dict[str, Path] = {}
    pages = ["/home", "/stock", "/materials/request", "/materials/return", "/reports",
             "/tundish-report", "/tundish-report/settings", "/reports/main-goal"]
    for p in pages:
        r = c.get(p)
        assert r.status_code == 200, (p, r.status_code, r.text[:300])
    r = c.get("/reports")
    assert "اقلام شرکت (PDF)" in r.text and "اقلام پیمانکار (PDF)" in r.text, r.text[-2000:]
    assert 'name="renovation" value="with"' in r.text and 'name="renovation" value="without"' in r.text
    r = c.get("/reports?renovation=without")
    assert 'value="without" checked' in r.text
    import io

    from openpyxl import load_workbook

    for reno in ("with", "without"):
        for seg in ("company", "contractor"):
            r = c.get(
                f"/reports/critical-items?segment={seg}&renovation={reno}"
            )
            assert r.status_code == 200 and r.content[:4] == b"%PDF", (seg, reno, r.status_code)
            fname = r.headers.get("content-disposition", "")
            assert ("%D8%A8%D8%AF%D9%88%D9%86" in fname) == (reno == "without"), fname  # «بدون»
            pth = tmp / f"web_critical_{seg}_{reno}.pdf"
            pth.write_bytes(r.content)
            _assert_portrait(pth)
            out[f"web_critical_{seg}_{reno}"] = pth
        r = c.get(f"/reports/critical-items.xlsx?renovation={reno}")
        assert r.status_code == 200 and r.content[:2] == b"PK"
        wb = load_workbook(io.BytesIO(r.content))
        label = "بدون نوسازی" if reno == "without" else "با نوسازی"
        notes = " ".join(str(x.value) for row in wb["توضیحات"].iter_rows() for x in row if x.value)
        assert f"«{label}»" in notes, notes[:300]
    # legacy month params are ignored: report date = today, same basis
    r = c.get("/reports/critical-items?jalali_year=1400&jalali_month=1", follow_redirects=False)
    assert r.status_code == 200 and r.content[:4] == b"%PDF", r.status_code
    r = c.get("/reports")
    assert "بدون انتخاب ماه" in r.text and _basis_label() in r.text, r.text[-3000:]
    assert f"تاریخ گزارش: {_today_tag().replace('-', '/')}" in r.text
    # other web PDF endpoints: PDF (portrait) or a redirect with a Persian error
    for ep in ("/reports/remaining-critical", "/reports/surplus", "/reports/user-activity",
               "/reports/monthly-summary"):
        r = c.get(ep, follow_redirects=False)
        assert r.status_code in (200, 303), (ep, r.status_code)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            pth = tmp / f"web_{ep.rsplit('/', 1)[-1]}.pdf"
            pth.write_bytes(r.content)
            _assert_portrait(pth)
            out[f"web_{ep.rsplit('/', 1)[-1]}"] = pth
    print("web pages + critical segments OK", sorted(out))
    return out


def test_all_generators(tmp: Path) -> dict:
    """Every PDF generator → A4 portrait; wide 20-column table still fits."""
    from analytics.tundish import daily_rates, remaining
    from pdf.generator import (
        DISPLAY_COLUMNS,
        HEADER_FA,
        generate_monthly_summary_pdf,
        generate_report,
        generate_simple_report_pdf,
        rtl_wrap_lines,
        FONT_NAME,
        _register_fonts,
    )

    _register_fonts()
    lines = rtl_wrap_lines("یک دو سه چهار پنج شش هفت هشت نه ده", 40, FONT_NAME, 8)
    assert len(lines) > 1 and lines[0].startswith("یک"), lines  # logical order kept

    out: dict[str, Path] = {}
    inv = _inventory_frame()
    inv = pd.concat([inv] * 12, ignore_index=True)
    inv["product_name"] = inv["product_name"] + " — شرح طولانی برای آزمون شکستن خط در ستون باریک"
    p = generate_simple_report_pdf(
        "منبع اصلی — آزمون ۲۰ ستون",
        subtitle="عرض صفحه A4 عمودی",
        columns=DISPLAY_COLUMNS["product_inventory"],
        rows=inv.to_dict(orient="records"),
        header_map=HEADER_FA,
        output_path=tmp / "wide20.pdf",
    )
    assert _assert_portrait(p) >= 2
    out["simple_wide20"] = p
    tank = pd.DataFrame({
        "domain": ["تاندیش"] * 3, "tundish_type": ["بیلت"] * 3, "tundish_id": ["T1"] * 3,
        "material_name": ["بتن 85"] * 3, "quantity": [10, 20, 30], "unit": ["Kg"] * 3,
        "assignee_id": ["1"] * 3, "assignee_name": ["الف"] * 3,
        "date": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]), "notes": [""] * 3,
    })
    rates = daily_rates(tank, None)
    rem = remaining(inv)
    p = generate_report(
        {"tank_consumption": tank, "product_inventory": inv},
        {"tank_consumption": {"label": "مصرف", "total_rows": 3, "visible_rows": 3},
         "product_inventory": {"label": "منبع اصلی", "total_rows": len(inv), "visible_rows": len(inv)}},
        {"bale_user_id": "901", "role": "owner", "display_name": "مالک"},
        tmp / "general.pdf",
        analytics={"daily_rates": rates, "remaining": rem, "critical": rem.head(0),
                   "forecast": rates.head(0), "suggest": rates.head(0), "period_consumption": tank,
                   "days": 3, "start": "2026-09-01", "end": "2026-09-03"},
    )
    _assert_portrait(p)
    out["general_report"] = p
    p = generate_monthly_summary_pdf(
        [{"kind": "banner", "title": "شهریور ۱۴۰۵"},
         {"title": "بیلت", "columns": ["ماده", "مقدار", "واحد", "توضیح"],
          "rows": [{"ماده": "بتن 85", "مقدار": 1200.5, "واحد": "Kg", "توضیح": "متن نسبتا طولانی برای شکستن خط"},
                   {"_kind": "subtotal", "_values": ["جمع", 1200.5, "Kg", ""]}]}],
        grand_kg=1200.5,
        output_path=tmp / "monthly.pdf",
    )
    _assert_portrait(p)
    out["monthly_summary"] = p
    print("all generators portrait OK", sorted(out))
    return out


def main() -> int:
    print("smoke critical split + portrait…")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_classify()
        test_id_rule_and_surplus(tmp)
        test_aggregation_rule()
        test_horizon_rule()
        test_basis_sequence_log(tmp)
        test_upsert_critical_point(tmp)
        test_inventory_upload_rules(tmp)
        pdfs: dict[str, Path] = {}
        for sub in ("svc", "bot", "web", "gen", "merge"):
            (tmp / sub).mkdir()
        test_merged_cells(tmp / "merge")
        test_section_rules()
        pdfs.update(test_service_split_and_ledger(tmp / "svc"))
        test_bot_flow(tmp / "bot")
        pdfs.update(test_web(tmp / "web"))
        pdfs.update(test_all_generators(tmp / "gen"))
        pngs = [_png(p, k) for k, p in pdfs.items()]
        print("page-1 PNGs:", ", ".join(str(x.name) for x in pngs if x))
    print("SMOKE_CRITICAL_PORTRAIT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
