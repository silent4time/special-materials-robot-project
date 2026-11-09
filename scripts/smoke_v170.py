#!/usr/bin/env python3
"""Smoke v1.7.0 (user rules 1405/08/18) — temp DB only, never touches data/bot.db.

1) سقف نوسازی تاندیش (analytics.renovation_caps), shared by bot + web:
   annual billet 5 / bloom 2 / slab 5; ≤1 per section per month; only مهر..اسفند;
   N-tundish example: 100 slab → patching × 100, renovation × 5 (never × 100);
   critical-items horizon windows (monthly, annual, first-half, year wrap).
2) «گزارش ورودی اقلام» (reports menu, bot + web): inbound events of the 30 days up to
   the last warehouse-stock upload, from the stored history, with dates; several
   uploads increasing the same item keep every event; the post-upload notice stays
   the diff vs the previous upload only.
3) «✏️ ویرایش رکورد»: after the ID, each field is asked separately; an empty answer
   (⏭ بدون تغییر / «-») keeps the previous value; web shows the same fields with the
   current value and an empty input keeps it.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import io
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import _smoke_isolation  # noqa: E402,F401
import config  # noqa: E402
import pandas as pd  # noqa: E402

ZERO = {c: 0 for c in ("casting_floor", "billet_renovation", "billet_patching", "bloom_renovation",
                       "bloom_patching", "slab_renovation", "slab_patching")}


def _rates_frame() -> pd.DataFrame:
    base = dict(priority=1, contractor_or_company="شرکت", unit="Kg", origin="داخلی", usage_location="اسلب")
    return pd.DataFrame([
        dict(base, category_code="1201", id="378120100011A", product_name="بتن نوسازی اسلب",
             keyword="بتن نوسازی", quantity=0, **{**ZERO, "slab_renovation": 1000}),
        dict(base, category_code="1202", id="378120200011B", product_name="مواد پچینگ اسلب",
             keyword="پچینگ", quantity=0, **{**ZERO, "slab_patching": 50}),
        dict(base, category_code="1203", id="378120300011C", product_name="بتن مشترک",
             keyword="بتن مشترک", quantity=0, **{**ZERO, "slab_renovation": 200, "slab_patching": 20,
                                                   "billet_renovation": 10, "billet_patching": 4,
                                                   "bloom_renovation": 30, "bloom_patching": 3}),
        dict(base, category_code="1204", id="378120400011D", product_name="سطح ریخته‌گری",
             keyword="کف", quantity=0, **{**ZERO, "casting_floor": 2}),
    ])


def test_caps() -> None:
    from analytics import renovation_caps as rc

    assert rc.ANNUAL_RENOVATION_CAP == {"billet": 5, "bloom": 2, "slab": 5}
    assert rc.MONTHLY_RENOVATION_CAP == 1 and rc.RENOVATION_FIRST_MONTH == 7
    # raw N: min(N, annual cap)
    assert rc.capped_renovations_for_n("slab", 100) == 5
    assert rc.capped_renovations_for_n("bloom", 100) == 2 and rc.capped_renovations_for_n("bloom", 1) == 1
    assert rc.capped_renovations_for_n("billet", 3) == 3
    # monthly cap: 3-month horizon in the 2nd half → ≤3 even with 70 tundish/month
    assert rc.window_renovations("slab", 70, 1405, 8, 3) == 3          # آبان, آذر, دی
    assert rc.window_renovations("bloom", 70, 1405, 8, 3) == 2         # annual cap 2
    # annual cap: 6 months from آبان → 5 2nd-half months in 1405 + فروردین 1406 → 5
    assert rc.window_renovations("slab", 70, 1405, 8, 6) == 5
    # first half: no renovation, NOT spread uniformly over فروردین..شهریور
    assert rc.window_renovations("slab", 70, 1405, 1, 3) == 0
    assert rc.window_renovations("slab", 70, 1405, 1, 6) == 0
    assert rc.window_renovations("slab", 70, 1405, 5, 3) == 1          # مرداد, شهریور, مهر
    assert rc.window_renovations("slab", 70, 1405, 4, 6) == 3          # مهر, آبان, آذر
    # year wrap: اسفند 1405 + 5 months of 1406 (first half) → 1
    assert rc.window_renovations("slab", 70, 1405, 12, 6) == 1
    # fewer tundishes than slots → tundish count wins
    assert rc.window_renovations("slab", 0.5, 1405, 8, 3) == 1.5
    assert rc.window_renovations("slab", 0, 1405, 8, 3) == 0
    assert "اسلب 5" in rc.caps_text_fa() and "بلوم 2" in rc.caps_text_fa()
    print("renovation caps (annual / monthly / first half / year wrap) OK")


def test_n_tundish() -> None:
    from analytics.critical_items import TundishMonthCounts, monthly_need_for_rates
    from services import n_tundish_report as nt

    inv = _rates_frame()
    res = nt.build_rows(inv, "slab", 100, "with", report_date="1405/08/18")
    assert res.error is None, res.error
    by = {r[nt.COL_CODE]: r for r in res.rows}
    # user example: 100 slab → patching for 100, renovation for at most 5
    assert by["1201"][nt.COL_NEED] == 1000 * 5, by["1201"]          # not 1000 × 100
    assert by["1202"][nt.COL_NEED] == 50 * 100, by["1202"]
    assert by["1203"][nt.COL_NEED] == 20 * 100 + 200 * 5, by["1203"]
    assert by["1204"][nt.COL_NEED] == 2 * 100, by["1204"]           # casting floor × N unchanged
    assert "نوسازی × 5 تاندیش" in res.basis_line and "پچینگ × 100" in res.basis_line, res.basis_line
    # «بدون نوسازی» unchanged: patching only
    wo = nt.build_rows(inv, "slab", 100, "without", report_date="1405/08/18")
    wby = {r[nt.COL_CODE]: r for r in wo.rows}
    assert "1201" not in wby and wby["1202"][nt.COL_NEED] == 5000 and wby["1203"][nt.COL_NEED] == 2000
    # small N below the cap: renovation for every tundish
    s3 = {r[nt.COL_CODE]: r for r in nt.build_rows(inv, "slab", 3, "with").rows}
    assert s3["1201"][nt.COL_NEED] == 3000 and s3["1203"][nt.COL_NEED] == 3 * 220
    # bloom cap 2
    b10 = {r[nt.COL_CODE]: r for r in nt.build_rows(inv, "bloom", 10, "with").rows}
    assert b10["1203"][nt.COL_NEED] == 3 * 10 + 30 * 2, b10["1203"]
    # mix: each section capped separately; per-tundish columns are the capped average
    mix = nt.build_mix_rows(inv, {"billet": 70, "bloom": 20, "slab": 100}, "with")
    mrow = next(r for r in mix.rows if r[nt.COL_CODE] == "1203")
    exp = (4 * 70 + 10 * 5) + (3 * 20 + 30 * 2) + (20 * 100 + 200 * 5)
    assert mrow[mix.need_col] == exp, (mrow, exp)
    assert abs(mrow[nt.COL_PER_SLAB] - (20 + 200 * 5 / 100)) < 1e-6, mrow
    assert abs(mrow[nt.COL_PER_BLOOM] - (3 + 30 * 2 / 20)) < 1e-6, mrow
    total = mrow[nt.COL_PER_BILLET] * 70 + mrow[nt.COL_PER_BLOOM] * 20 + mrow[nt.COL_PER_SLAB] * 100
    assert abs(total - exp) < 0.1, (total, exp)  # per columns rounded to 3 decimals
    # pure formula
    cnt = TundishMonthCounts(0, 1, 0, 0, 100)
    assert monthly_need_for_rates(**{**ZERO, "slab_renovation": 1000, "slab_patching": 50}, counts=cnt) == 1000 * 5 + 50 * 100
    print("N tundish (100 slab → patching ×100, renovation ×5; mix; bloom cap) OK")


def test_critical_items() -> None:
    from analytics.critical_items import (
        COL_FORECAST, COL_MONTHLY, TundishMonthCounts, build_critical_items_rows, horizon_reno_counts,
        renovation_note_fa,
    )

    inv = _rates_frame()
    # second half (آبان): 3-month domestic horizon → 3 renovations (≤1/month), not 70×3
    cnt = TundishMonthCounts(1405, 8, 0, 0, 70)
    assert horizon_reno_counts(cnt, 3)["slab"] == 3 and horizon_reno_counts(cnt, 6)["slab"] == 5
    df = build_critical_items_rows(inv, cnt, segment="company", include_covered=True)
    rows = {str(r["کد چهاررقمی"]): r for _, r in df.iterrows()}
    assert float(rows["1201"][COL_FORECAST]) == 1000 * 3, rows["1201"]          # was 1000×70×3
    assert float(rows["1202"][COL_FORECAST]) == 50 * 70 * 3                     # patching all tundishes
    assert abs(float(rows["1203"][COL_FORECAST]) - (20 * 70 * 3 + 200 * 3)) < 0.2
    assert abs(float(rows["1201"][COL_MONTHLY]) - 1000) < 0.2                   # 3000 ÷ 3 months
    # first half (فروردین): no renovation in the horizon → renovation-only item not needed
    first = build_critical_items_rows(inv, TundishMonthCounts(1405, 1, 0, 0, 70), segment="company",
                                      include_covered=True)
    assert "1201" not in set(first["کد چهاررقمی"].astype(str)), first
    # imported 6-month horizon from آبان: annual cap 5
    inv6 = inv.copy()
    inv6.loc[inv6["category_code"] == "1201", "origin"] = "وارداتی"
    six = build_critical_items_rows(inv6, cnt, segment="company", include_covered=True)
    r6 = six.loc[six["کد چهاررقمی"].astype(str) == "1201"].iloc[0]
    assert float(r6[COL_FORECAST]) == 1000 * 5, r6
    note = renovation_note_fa(cnt)
    assert "افق ۳ ماه: اسلب 3" in note and "افق ۶ ماه: اسلب 5" in note and "آبان 1405" in note, note
    # «بدون نوسازی» unchanged
    wo = build_critical_items_rows(inv, cnt, segment="company", reno_mode="without", include_covered=True)
    assert "1201" not in set(wo["کد چهاررقمی"].astype(str))
    print("critical items horizon caps (monthly/annual/first half; note) OK")


def _stock(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame([dict(category_code=c, id=i, quantity=q, product_name=n) for c, i, q, n in rows])


A, B, N1 = "378112341302R", "378112345555B", "378112346666C"


def test_inbound_window_and_edit(tmp: Path) -> None:
    import bot.handlers as handlers_mod
    import bot.keyboards as kb
    from db.models import Database
    from services import inbound_report as svc
    from services import main_source as ms
    from services import permissions as perm

    db = Database(Path(os.environ["DATABASE_PATH"]).with_name("v170.db"))
    perm.bind(db)
    db.upsert_user("901", role="owner", display_name="مالک تست")
    source = pd.DataFrame([
        dict(category_code="1203", id=A, product_name="بتن 85 شرکت", keyword="بتن 85bt", quantity=100,
             priority=1, contractor_or_company="شرکت", unit="Kg", usage_location="بیلت"),
        dict(category_code="1203", id=B, product_name="بتن ب", keyword="بتن ب", quantity=50,
             priority=1, contractor_or_company="شرکت", unit="Kg"),
    ])
    ms.persist_primary_frame(db, source, bale_user_id="901")
    live = ms.load_primary_frame(db, bale_user_id="901")
    now = datetime.now(timezone.utc)

    def up(days_ago: int, rows: list[tuple]) -> dict:
        return svc.record_stock_upload(
            db, uploaded=_stock(rows), live_before=live, live_after=live, rejected=[],
            bale_user_id="901", actor_display_name="مالک تست",
            created_at=(now - timedelta(days=days_ago)).isoformat(),
        )

    up(50, [("1203", A, 100, "بتن"), ("1203", B, 50, "ب")])                       # first baseline
    up(40, [("1203", A, 120, "بتن"), ("1203", B, 50, "ب")])                       # +20 A (outside 30 d)
    r25 = up(25, [("1203", A, 150, "بتن"), ("1203", B, 50, "ب")])                 # +30 A
    up(10, [("1203", A, 170, "بتن"), ("1203", B, 60, "ب")])                       # +20 A, +10 B
    last = up(0, [("1203", A, 170, "بتن"), ("1203", B, 60, "ب"), ("1203", N1, 7, "نو")])  # +7 N1
    # post-upload notice = diff vs the previous upload only (unchanged behavior)
    assert [ln[svc.COL_ID] for ln in last["lines"]] == [N1], last["lines"]
    assert "تاریخ پایهٔ مقایسه" in svc.summary_text_fa(last)

    win = svc.window_events(db)
    ev = [(e[svc.COL_ID], e[svc.COL_INBOUND_QTY]) for e in win["lines"]]
    assert ev == [(A, 30), (A, 20), (B, 10), (N1, 7)], ev                         # 40-day-old +20 excluded
    dates = [e[svc.COL_ADDED_DATE] for e in win["lines"] if e[svc.COL_ID] == A]
    assert len(set(dates)) == 2 and dates[0] == r25["upload_label"], (dates, r25["upload_label"])
    assert win["n_items"] == 3 and win["n_events"] == 4 and win["total"] == 67
    assert len(win["uploads"]) == 3, win["uploads"]                               # d-25, d-10, d-0
    pdf, xlsx = svc.build_window_files(db, win, out_dir=tmp / "win")
    assert pdf.read_bytes()[:4] == b"%PDF"
    sheet = pd.read_excel(xlsx, sheet_name=0, header=None).fillna("")
    hdr_i = next(i for i, r in sheet.iterrows() if "مقدار وارده" in list(map(str, r)))
    hdr = list(map(str, sheet.iloc[hdr_i]))
    for col in ("کد ۴ رقمی", "آیدی کد", "شرح", "کلیدواژه", "موجودی", "واحد", "مقدار وارده", "تاریخ اضافه شدن"):
        assert col in hdr, (col, hdr)
    assert hdr.index("کد ۴ رقمی") < hdr.index("آیدی کد") < hdr.index("مقدار وارده")
    body = sheet.iloc[hdr_i + 1:]
    a_rows = body[body[hdr.index("آیدی کد")].astype(str) == A]
    assert len(a_rows) == 2 and str(a_rows.iloc[0][hdr.index("شرح")]) == "بتن 85 شرکت"
    from openpyxl import load_workbook

    wb = load_workbook(xlsx)
    ws = wb.worksheets[0]
    assert ws.sheet_view.rightToLeft, "RTL sheet"
    fonts = {c.font.name for row in ws.iter_rows() for c in row if c.value is not None}
    assert fonts == {"Vazirmatn"}, fonts

    # ── bot: reports menu → window (format choice PDF/Excel), not only the last diff
    docs: list[Path] = []

    class FakeClient:
        def __init__(self) -> None:
            self.msgs: list[str] = []
            self.markups: list = []

        def send_message(self, chat_id, text, reply_markup=None, **_kw):
            self.msgs.append(text)
            self.markups.append(reply_markup)
            return {"ok": True}

        def send_document(self, chat_id, path, caption=None, **_kw):
            docs.append(Path(path))
            return {"ok": True}

        def __getattr__(self, name):
            return lambda *a, **k: {"ok": True}

    client = FakeClient()
    app = handlers_mod.BotApp(client, db)
    msg = {"chat": {"id": 901}, "from": {"id": 901, "first_name": "مالک"}, "text": ""}
    send = lambda t: app.handle_update({"message": dict(msg, text=t)})  # noqa: E731
    send(kb.BTN_INBOUND)
    text = "\n".join(client.msgs)
    assert svc.WINDOW_TITLE in text and "4 مورد برای 3 قلم" in text, text
    assert kb.BTN_OUT_PDF in client.msgs[-1], client.msgs[-1]                  # format choice first
    send(kb.BTN_OUT_XLSX)
    assert [p.suffix for p in docs] == [".xlsx"], docs
    bot_df = pd.read_excel(docs[-1], sheet_name=0, header=None).fillna("")
    assert bot_df.equals(sheet), "bot xlsx == service xlsx"

    # ── bot: ✏️ ویرایش رکورد — one question per field, empty keeps previous
    client.msgs.clear()
    send(kb.BTN_INV_EDIT_RECORD)
    send(A)
    fields = ms.edit_fields()
    assert "رکورد فعلی" in client.msgs[-2] and f"(1/{len(fields)})" in client.msgs[-1], client.msgs[-2:]
    assert f"«{ms.FIELD_LABELS_FA[fields[0]]}»" in client.msgs[-1] and "مقدار فعلی" in client.msgs[-1]
    asked = [client.msgs[-1]]
    for i, f in enumerate(fields):
        if f == "keyword":
            answer = "بتن تازه"
        elif f == "quantity":
            answer = "-"                       # «-» → keep
        elif f == "unit":
            answer = "بدون تغییر"              # typed keep word → keep
        else:
            answer = kb.BTN_EDIT_KEEP
        n_before = len(client.msgs)
        send(answer)
        if i + 1 < len(fields):
            assert len(client.msgs) == n_before + 1, (i, f, answer, client.msgs[n_before:])
            nxt = client.msgs[-1]
            assert f"({i + 2}/{len(fields)})" in nxt and f"«{ms.FIELD_LABELS_FA[fields[i + 1]]}»" in nxt, nxt
            asked.append(nxt)
    assert len(asked) == len(fields)
    assert "✅" in client.msgs[-1] and "کلید واژه" in client.msgs[-1], client.msgs[-1]
    _i, row = ms.find_row_by_id(ms.load_primary_frame(db, bale_user_id="901"), A)
    assert row["keyword"] == "بتن تازه" and float(row["quantity"]) == 100 and row["unit"] == "Kg", row
    assert row["usage_location"] == "بیلت" and row["product_name"] == "بتن 85 شرکت", row
    # all kept → no write
    client.msgs.clear()
    send(kb.BTN_INV_EDIT_RECORD)
    send(B)
    for _f in fields:
        send(kb.BTN_EDIT_KEEP)
    assert "تغییری ثبت نشد" in client.msgs[-1], client.msgs[-1]
    # cancel mid-way keeps everything
    send(kb.BTN_INV_EDIT_RECORD)
    send(B)
    send("999")
    send(kb.BTN_CANCEL)
    _i, rb = ms.find_row_by_id(ms.load_primary_frame(db, bale_user_id="901"), B)
    assert ms._audit_val(rb["category_code"]) == "1203", rb
    # pure helper (Bale never delivers an empty message; blank/whitespace still = keep)
    assert ms.is_keep_answer("   ") and ms.is_keep_answer("") and ms.is_keep_answer(None)
    assert not ms.is_keep_answer("0")
    upd = ms.collect_edit_updates({"keyword": "x", "quantity": 5}, {"keyword": "", "quantity": "7", "unit": "-"})
    assert upd == {"quantity": "7"}, upd

    # ── web: same window + same per-field edit semantics
    from fastapi.testclient import TestClient

    import web.deps as deps

    deps._db = db
    from web.app import create_app

    wapp = create_app()
    wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("901")
    wapp.dependency_overrides[deps.current_user] = lambda: db.get_user("901")
    c = TestClient(wapp)
    page = c.get("/reports")
    assert page.status_code == 200 and "۳۰ روز اخیر" in page.text and "/reports/inbound-30d.xlsx" in page.text
    assert "مقدار وارده" in page.text and "تاریخ اضافه شدن" in page.text and N1 in page.text
    wx = c.get("/reports/inbound-30d.xlsx")
    assert wx.status_code == 200, wx.status_code
    web_df = pd.read_excel(io.BytesIO(wx.content), sheet_name=0, header=None).fillna("")
    assert web_df.equals(sheet), "web xlsx == bot xlsx"
    assert c.get("/reports/inbound-30d.pdf").content[:4] == b"%PDF"
    ep = c.get("/settings/main-source/edit", params={"id": A})
    assert ep.status_code == 200 and "رکورد فعلی" in ep.text and "مقدار فعلی: بتن تازه" in ep.text, ep.status_code
    assert f"(1/{len(fields)})" in ep.text and "خالی = بدون تغییر" in ep.text
    form = {"id": A, **{f: "" for f in fields}, "usage_location": "اسلب"}
    pr = c.post("/settings/main-source/edit", data=form)
    assert pr.status_code == 200 and "به‌روز شد" in pr.text, pr.text[:500]
    _i, row = ms.find_row_by_id(ms.load_primary_frame(db, bale_user_id="901"), A)
    assert row["usage_location"] == "اسلب" and row["keyword"] == "بتن تازه" and float(row["quantity"]) == 100
    nochg = c.post("/settings/main-source/edit", data={"id": A, **{f: "" for f in fields}})
    assert nochg.status_code == 400 and "تغییری ثبت نشد" in nochg.text
    assert c.get("/settings/main-source/edit", params={"id": "NOPE"}).status_code == 400
    print("inbound 30-day window + per-field record edit (bot + web) OK")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_v170_"))
    config.REPORT_DIR = tmp / "reports"
    try:
        test_caps()
        test_n_tundish()
        test_critical_items()
        test_inbound_window_and_edit(tmp)
        print("SMOKE_V170_OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
