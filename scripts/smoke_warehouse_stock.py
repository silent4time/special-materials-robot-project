"""v1.5.0 smoke: «📦 گزارش موجودی انبار» (bot 3-step flow, segments, month-end snapshots,
web route) + the PDF/Excel output-format choice. Temp dirs/DBs only — never live data."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
TMP = _smoke_env.SMOKE_TMP


def _frame() -> pd.DataFrame:
    from services.main_source import INVENTORY_COLUMNS

    rows = []
    for code, iid, name, q in [("1601", "378400001601A", "آجر پیمانکار", 7.0),
                               ("1710", "378412341710B", "نازل شرکت", 3.0),
                               ("1800", "378455551800C", "مازاد شرکت", 2.5)]:
        r = {c: "" for c in INVENTORY_COLUMNS}
        r.update(category_code=code, id=iid, product_name=name, quantity=q, priority=1,
                 unit="Kg", keyword="واژه‌" + code)
        rows.append(r)
    return pd.DataFrame(rows)


def main() -> int:
    import services.main_source as ms
    import smoke_phase1 as p1
    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from openpyxl import load_workbook
    from services import warehouse_stock_report as wsr

    ms.UPLOAD_DIR = TMP / "uploads_ws"
    db = Database(TMP / "ws.db")
    db.upsert_user("51", role="owner", display_name="مالک")
    ms.persist_primary_frame(db, _frame(), bale_user_id="51")
    user = db.get_user("51")

    # service: segments by ID rule (chars 5–8 == 0000 → contractor)
    r = wsr.build(db, user, wsr.MODE_TODAY, wsr.SEGMENT_CONTRACTOR)
    assert not r.error and r.n_rows == 1 and r.sections[0]["rows"][0]["item_id"] == "378400001601A", r.sections
    r = wsr.build(db, user, wsr.MODE_TODAY, wsr.SEGMENT_COMPANY)
    assert r.n_rows == 2 and {x["item_id"] for x in r.sections[0]["rows"]} == {"378412341710B", "378455551800C"}
    r = wsr.build(db, user, wsr.MODE_TODAY, wsr.SEG_BOTH)
    assert r.n_rows == 3 and len(r.sections) == 2
    row = r.sections[0]["rows"][0]
    assert set(row) == {"code4", "item_id", "product_name", "stock", "keyword"} and row["stock"].startswith("7"), row
    for fmt in ("pdf", "xlsx"):
        p = wsr.write_file(r, fmt, output_dir=TMP / "ws_out")
        assert p.is_file() and p.suffix == "." + fmt, p
        if fmt == "xlsx":
            wb = load_workbook(p)
            fonts = {c.font.name for ws in wb for rr in ws.iter_rows() for c in rr if c.value is not None}
            assert fonts == {"Vazirmatn"}, fonts
            assert all(ws.sheet_view.rightToLeft for ws in wb)

    # month-end without snapshots → clear message
    assert wsr.month_end_unavailable(db) and "پایان ماه گذشته" in wsr.month_end_unavailable(db)
    assert wsr.build(db, user, wsr.MODE_MONTH_END, wsr.SEG_BOTH).error
    # snapshot before cutoff (qty 99) + one after cutoff (ignored)
    _jy, _jm, cutoff, _label = wsr.prev_month_cutoff()
    snap = TMP / "ws_snap.xlsx"
    f = _frame(); f["quantity"] = [99.0, 98.0, 97.0]; f.to_excel(snap, index=False)
    snap2 = TMP / "ws_snap2.xlsx"
    f["quantity"] = [1.0, 1.0, 1.0]; f.to_excel(snap2, index=False)
    with sqlite3.connect(db.path) as c:
        for path, when in ((snap, cutoff - timedelta(days=3)), (snap2, cutoff + timedelta(hours=2))):
            c.execute("INSERT INTO inventory_stock_uploads(kind, snapshot_path, row_count, created_at) VALUES (?,?,?,?)",
                      ("stock_update", str(path), 3, when.isoformat()))
    assert wsr.month_end_unavailable(db) is None
    r = wsr.build(db, user, wsr.MODE_MONTH_END, wsr.SEGMENT_CONTRACTOR)
    assert not r.error and r.sections[0]["rows"][0]["stock"].startswith("99"), r.sections
    print("  service (segments, columns, Vazirmatn xlsx, month-end snapshots/no-snapshot) OK")

    # bot flow
    client = p1.FakeClient()
    app = BotApp(client, db)  # type: ignore[arg-type]
    app.auto_output_format = None
    base = {"from": {"id": 51, "first_name": "a"}, "chat": {"id": 51}}
    say = lambda t: app.handle_update({"message": dict(base, text=t)})
    assert any(kb.BTN_STOCK_REPORT in str(m) for m in [kb.analytics_menu(user)])
    say(kb.BTN_STOCK_REPORT); assert "مرحله" in client.sent[-1][1], client.sent[-1]
    say(kb.BTN_STOCK_TODAY); assert "مرحلهٔ ۲" in client.sent[-1][1]
    say(kb.BTN_SEG_COMPANY); assert "مرحلهٔ ۳" in client.sent[-1][1]
    n = len(client.docs)
    say(kb.BTN_OUT_XLSX)
    assert len(client.docs) == n + 1 and client.docs[-1][1].endswith(".xlsx"), client.docs
    say(kb.BTN_STOCK_REPORT); say(kb.BTN_STOCK_MONTH_END); say(kb.BTN_SEG_BOTH); say(kb.BTN_OUT_PDF)
    assert len(client.docs) == n + 2 and client.docs[-1][1].endswith(".pdf"), client.docs
    print("  bot flow (3 steps, single chosen file) OK")

    # generic output-format choice (_deliver_files)
    pdf, xlsx = TMP / "a.pdf", TMP / "a.xlsx"
    pdf.write_bytes(b"%PDF"); xlsx.write_bytes(b"x")
    msg = dict(base, text="")
    n = len(client.docs)
    app._deliver_files(msg, user, [("pdf", pdf, "p"), ("xlsx", xlsx, "x")], done_text="✅ تمام")
    assert len(client.docs) == n and "قالب خروجی" in client.sent[-1][1]
    say(kb.BTN_OUT_PDF)
    assert len(client.docs) == n + 1 and client.docs[-1][1] == str(pdf) and client.sent[-1][1] == "✅ تمام"
    app._deliver_files(msg, user, [("pdf", pdf, "p"), ("xlsx", xlsx, "x")])
    say(kb.BTN_OUT_XLSX)
    assert len(client.docs) == n + 2 and client.docs[-1][1] == str(xlsx)
    print("  output-format choice (prompt, only chosen kind sent) OK")

    # web route (same builder, chosen format only)
    from fastapi.testclient import TestClient
    from web import deps

    deps._db = db
    from web.app import create_app

    wapp = create_app()
    wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("51")
    wapp.dependency_overrides[deps.current_user] = lambda: db.get_user("51")
    c = TestClient(wapp)
    page = c.get("/reports")
    assert page.status_code == 200 and 'id="warehouse-stock"' in page.text and "خروجی اکسل" in page.text
    for fmt, ctype in (("pdf", "application/pdf"), ("xlsx", "spreadsheetml")):
        res = c.get(f"/reports/warehouse-stock?mode=today&segment=contractor&fmt={fmt}")
        assert res.status_code == 200 and ctype in res.headers["content-type"], (res.status_code, res.headers)
    res = c.get("/reports/warehouse-stock?mode=month_end&segment=both&fmt=pdf")
    assert res.status_code == 200, res.status_code
    print("  web route (page card, PDF/xlsx, month-end) OK")

    print("SMOKE_WAREHOUSE_STOCK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
