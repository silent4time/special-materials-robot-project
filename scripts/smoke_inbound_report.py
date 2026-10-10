#!/usr/bin/env python3
"""Smoke (t221u): «📥 گزارش اقلام ورودی به انبار».

Temp DB + temp uploads/reports; never touches data/bot.db. Covers:
(a) increase vs the PREVIOUS stock upload; (b) new ID with an existing 4-digit code
and stock>0; new ID with stock 0 NOT listed; code 1800 excluded; rejected rows (⛔:
new code / new 1800 row) listed separately, never inbound; work-order duplicates of
one ID are ONE value (not summed); priority 0 included; baseline NOT moved by
edit/add/delete record nor by a full منبع اصلی upload; first upload = baseline only;
bot (upload message + PDF/XLSX + on-demand + history + legacy label + technician
denied) and web (/reports latest + history + PDF/XLSX) show the same stored report.
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import _smoke_isolation  # noqa: E402,F401
import config  # noqa: E402
import pandas as pd  # noqa: E402

A = "378112341302R"      # existing, increases (company)
B = "378112345555B"      # existing, decreases
P0 = "378112348888E"     # existing, priority 0, increases
N1 = "378112346666C"     # new id, known code, stock 7
N0 = "378112347777D"     # new id, known code, stock 0
S = "378124311111A"      # existing 1800, increases → excluded
S2 = "378124312222D"     # new 1800 → rejected
X = "378119991234C"      # new 4-digit code → rejected
ADDED = "378112349999F"  # added by «افزودن رکورد» between uploads


def _xlsx(path: Path, rows: list[tuple]) -> Path:
    from openpyxl import Workbook

    wb = Workbook(); ws = wb.active; ws.title = "ریز اطلاعات"
    ws.append(["کد دسته بندی", "شناسه مواد", "شرح کالا", "کلید واژه", "موجودی", "اولویت",
               "تأمین‌کننده", "واحد", "محل استفاده"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_pure() -> None:
    from services.inbound_report import compute_inbound, stock_frame_by_id

    base = pd.DataFrame([
        dict(id=A, category_code="1203", quantity=100),
        dict(id=S, category_code="1800", quantity=5),
    ])
    up = pd.DataFrame([
        dict(id=A, category_code=1203, quantity=130),   # work order 1
        dict(id=A, category_code=1203, quantity=130),   # work order 2 (same stock)
        dict(id=A, category_code=1203, quantity=130),   # work order 3
        dict(id=N1, category_code=1203, quantity=7),
        dict(id=N0, category_code=1203, quantity=0),
        dict(id=S, category_code=1800, quantity=9),
    ])
    one = stock_frame_by_id(up)
    assert len(one) == 4 and float(one.loc[one["id"] == A, "quantity"].iloc[0]) == 130, one
    res = compute_inbound(base, up, live_before=base)
    got = {(r["شناسه مواد"], r["نوع"], r["مقدار ورودی"]) for r in res.lines}
    assert got == {(A, "افزایش", 30), (N1, "شناسه جدید", 7)}, got  # 30 not 290/360
    assert res.n_zero_new == 1 and res.n_excluded_1800 == 1, res
    # no baseline → first baseline, nothing inbound
    first = compute_inbound(None, up, live_before=base)
    assert not first.lines and not first.has_baseline


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_inbound_"))
    config.REPORT_DIR = tmp / "reports"
    try:
        test_pure()
        import bot.handlers as handlers_mod
        import bot.keyboards as kb
        from db.models import Database
        from services import inbound_report as svc
        from services.main_source import add_row, delete_row, load_primary_frame, persist_primary_frame, upsert_row

        db = Database(tmp / "inbound.db")
        db.upsert_user("901", role="owner", display_name="مالک تست")
        db.upsert_user("903", role="technician", display_name="تکنسین تست")
        db.add_category_code("1999", created_by="901")
        source = pd.DataFrame([
            dict(category_code="1203", id=A, product_name="بتن 85 شرکت", keyword="بتن 85bt",
                 quantity=100, priority=1, contractor_or_company="شرکت", unit="Kg", usage_location="بیلت"),
            dict(category_code="1203", id=B, product_name="بتن ب", keyword="بتن ب",
                 quantity=50, priority=1, contractor_or_company="شرکت", unit="Kg"),
            dict(category_code="1203", id=P0, product_name="بتن اولویت صفر", keyword="",
                 quantity=10, priority=0, contractor_or_company="شرکت", unit="No"),
            dict(category_code="1800", id=S, product_name="مازاد قدیمی", keyword="مازاد",
                 quantity=5, priority=0, contractor_or_company="شرکت", unit="Kg"),
        ])
        persist_primary_frame(db, source, bale_user_id="901")

        files: dict[str, Path] = {}
        sent_docs: list[Path] = []

        class FakeClient:
            def __init__(self) -> None:
                self.msgs: list[str] = []
                self.markups: list = []

            def download_file(self, file_id, dest):
                shutil.copy2(files[file_id], dest)
                return Path(dest)

            def send_message(self, chat_id, text, reply_markup=None, **_kw):
                self.msgs.append(text)
                self.markups.append(reply_markup)
                return {"ok": True}

            def send_document(self, chat_id, path, caption=None, **_kw):
                sent_docs.append(Path(path))
                return {"ok": True}

            def __getattr__(self, name):
                return lambda *a, **k: {"ok": True}

        client = FakeClient()
        app = handlers_mod.BotApp(client, db)
        msg = {"chat": {"id": 901}, "from": {"id": 901, "first_name": "مالک"}, "text": ""}

        def upload(file_id: str, path: Path, origin: str) -> None:
            files[file_id] = path
            app.on_pick_file_type(msg, "product_inventory", return_menu=origin)
            app.on_document(dict(msg, document={"file_id": file_id, "file_name": path.name}))

        # 1) first stock upload → first baseline only
        upload("u1", _xlsx(tmp / "stock1.xlsx", [
            (1203, A, "بتن 85 شرکت", "بتن 85bt", 100, 1, "شرکت", "Kg", "بیلت"),
            (1203, B, "بتن ب", "بتن ب", 50, 1, "شرکت", "Kg", ""),
            (1203, P0, "بتن اولویت صفر", "", 10, 0, "شرکت", "No", ""),
            (1800, S, "مازاد قدیمی", "مازاد", 5, 0, "شرکت", "Kg", ""),
        ]), "upload")
        assert db.count_stock_uploads() == 1
        r1 = svc.latest_report(db)
        assert r1 and not r1["has_baseline"] and r1["n_inbound"] == 0, r1
        assert "اولین پایه" in client.msgs[-1], client.msgs[-1]
        assert len(sent_docs) == 2 and {p.suffix for p in sent_docs} == {".pdf", ".xlsx"}, sent_docs
        base_id = db.latest_stock_upload()["id"]

        # 2) edits / add / delete / full source upload must NOT move the baseline
        upsert_row(db, A, {"quantity": 999}, bale_user_id="901")
        add_row(db, dict(category_code="1203", id=ADDED, product_name="افزوده", quantity=4,
                         priority=1, contractor_or_company="شرکت", unit="Kg"), bale_user_id="901")
        add_row(db, dict(category_code="1203", id="378112340000Z", product_name="حذفی", quantity=1,
                         priority=1, contractor_or_company="شرکت", unit="Kg"), bale_user_id="901")
        delete_row(db, "378112340000Z", bale_user_id="901")
        n_docs = len(sent_docs)
        upload("full", _xlsx(tmp / "full.xlsx", [
            (1203, A, "بتن 85 شرکت", "بتن 85bt", 500, 1, "شرکت", "Kg", "بیلت"),
        ]), "main_source")
        assert "پایه «گزارش اقلام ورودی به انبار» تغییر نکرد" in client.msgs[-1], client.msgs[-1]
        assert db.count_stock_uploads() == 1 and db.latest_stock_upload()["id"] == base_id
        assert len(sent_docs) == n_docs  # no inbound files for a full upload
        assert len(svc.list_reports(db)) == 1

        # 3) second stock upload (work-order duplicates, increases, new ids, rejects)
        n_docs = len(sent_docs)
        upload("u2", _xlsx(tmp / "stock2.xlsx", [
            (1203, A, "بتن 85 شرکت", "بتن 85bt", 130, 1, "شرکت", "Kg", "بیلت"),
            (1203, A, "بتن 85 شرکت", "بتن 85bt", 130, 1, "شرکت", "Kg", "بیلت"),
            (1203, B, "بتن ب", "بتن ب", 40, 1, "شرکت", "Kg", ""),
            (1203, P0, "بتن اولویت صفر", "", 12, 0, "شرکت", "No", ""),
            (1203, N1, "بتن جدید", "بتن نو", 7, 1, "شرکت", "Kg", "اسلب"),
            (1203, N0, "بتن جدید صفر", "", 0, 1, "شرکت", "Kg", ""),
            (1203, ADDED, "افزوده", "", 4, 1, "شرکت", "Kg", ""),
            (1800, S, "مازاد قدیمی", "مازاد", 9, 0, "شرکت", "Kg", ""),
            (1800, S2, "مازاد جدید", "", 3, 0, "شرکت", "Kg", ""),
            (1999, X, "کد جدید", "", 9, 1, "شرکت", "No", ""),
        ]), "upload")
        rep = svc.latest_report(db)
        assert rep["has_baseline"] and rep["baseline_upload_id"] == base_id, rep
        lines = {r["شناسه مواد"]: r for r in rep["lines"]}
        # (a) increase vs previous STOCK upload (100), not the edited 999 / full-upload 500
        assert lines[A]["نوع"] == "افزایش" and lines[A]["مقدار ورودی"] == 30, lines[A]
        assert lines[A]["موجودی قبلی"] == 100 and lines[A]["موجودی جدید"] == 130, lines[A]
        assert lines[A]["کلید واژه"] == "بتن 85bt" and lines[A]["تأمین‌کننده"] == "شرکت"
        assert lines[A]["واحد"] == "Kg" and lines[A]["محل استفاده"] == "بیلت", lines[A]
        # priority 0 included; keyword fallback → شرح کالا
        assert lines[P0]["مقدار ورودی"] == 2 and lines[P0]["کلید واژه"] == "بتن اولویت صفر", lines[P0]
        # (b) new ids with known code and stock>0 (incl. one added by «افزودن رکورد»)
        assert lines[N1]["نوع"] == "شناسه جدید" and lines[N1]["مقدار ورودی"] == 7, lines[N1]
        assert lines[ADDED]["نوع"] == "شناسه جدید" and lines[ADDED]["مقدار ورودی"] == 4
        assert B not in lines and N0 not in lines and S not in lines and S2 not in lines and X not in lines
        assert rep["n_increase"] == 2 and rep["n_new_id"] == 2 and rep["n_zero_new"] == 1
        assert rep["n_excluded_1800"] == 1, rep
        rej = {r["شناسه مواد"] for r in rep["rejected"]}
        assert rej == {S2, X} and rep["n_rejected"] == 2, rep["rejected"]
        assert list(rep["lines"][0].keys()) and set(svc.INBOUND_COLUMNS) <= set(rep["lines"][0]), rep["lines"][0]
        summ = {r["کد دسته"]: r for r in rep["summary"]}
        assert summ["1203"]["تعداد کل اقلام"] == 4 and summ["1203"]["جمع مقدار ورودی"] == 43, summ
        up_msg = client.msgs[-1]
        assert "📥 اقلام ورودی به انبار" in up_msg and "4 قلم" in up_msg and "⛔" in up_msg, up_msg
        # upload rules unchanged: rejected rows not added, new known-code id added
        live = set(load_primary_frame(db, bale_user_id="901")["id"].astype(str))
        assert N1 in live and S2 not in live and X not in live, live
        new_docs = sent_docs[n_docs:]
        assert [p.suffix for p in new_docs] == [".pdf", ".xlsx"], new_docs
        bot_xlsx = new_docs[1]
        xl = pd.ExcelFile(bot_xlsx)
        assert xl.sheet_names == ["ورودی", "خلاصه بر اساس کد", "رد شده"], xl.sheet_names
        assert db.latest_stock_upload()["id"] != base_id  # baseline moved by the STOCK upload only

        # 4) on-demand: latest + history (by upload date) + legacy label alias
        client.msgs.clear(); client.markups.clear()
        n_docs = len(sent_docs)
        app.handle_update({"message": dict(msg, text=kb.BTN_INBOUND)})
        assert client.msgs and "4 قلم" in client.msgs[0], client.msgs
        hist_labels = [row[0]["text"] if isinstance(row[0], dict) else row[0]
                       for row in (client.markups[0] or {}).get("keyboard", [])]
        first_lbl = next(lbl for lbl in hist_labels if lbl.startswith(svc.HISTORY_PREFIX))
        assert f"(#{r1['id']})" in first_lbl, hist_labels
        assert len(sent_docs) == n_docs + 2
        client.msgs.clear()
        app.handle_update({"message": dict(msg, text=first_lbl)})
        assert client.msgs and "اولین پایه" in client.msgs[0], client.msgs
        client.msgs.clear()
        app.handle_update({"message": dict(msg, text=kb.BTN_INBOUND_LEGACY)})
        assert client.msgs and "4 قلم" in client.msgs[0], client.msgs
        assert kb.BTN_INBOUND == "📥 گزارش اقلام ورودی به انبار"
        assert kb.BTN_INBOUND in str(kb.analytics_menu())
        # technician denied
        n_docs = len(sent_docs); client.msgs.clear()
        tmsg = {"chat": {"id": 903}, "from": {"id": 903, "first_name": "ت"}, "text": kb.BTN_INBOUND}
        app.handle_update({"message": tmsg})
        assert len(sent_docs) == n_docs, "technician must not get inbound files"

        # 5) web parity: same stored report, same rows
        from fastapi.testclient import TestClient

        import web.deps as deps

        deps._db = db  # BEFORE importing web.app (its module-level app calls get_db())
        from web.app import create_app

        wapp = create_app()
        wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("901")
        wapp.dependency_overrides[deps.current_user] = lambda: db.get_user("901")
        c = TestClient(wapp)
        page = c.get("/reports")
        assert page.status_code == 200 and "گزارش اقلام ورودی به انبار" in page.text, page.status_code
        assert f"/reports/inbound/{rep['id']}.pdf" in page.text and f"/reports/inbound/{r1['id']}.xlsx" in page.text
        assert N1 in page.text and A in page.text
        rp = c.get(f"/reports/inbound/{rep['id']}.pdf")
        assert rp.status_code == 200 and rp.content[:4] == b"%PDF", rp.status_code
        rx = c.get(f"/reports/inbound/{rep['id']}.xlsx")
        assert rx.status_code == 200, rx.status_code
        web_x = pd.ExcelFile(io.BytesIO(rx.content))
        assert web_x.sheet_names == xl.sheet_names
        for sheet in xl.sheet_names:
            a = pd.read_excel(bot_xlsx, sheet_name=sheet, header=None).fillna("")
            b = pd.read_excel(io.BytesIO(rx.content), sheet_name=sheet, header=None).fillna("")
            assert a.equals(b), (sheet, a, b)
        # technician denied on web
        wapp.dependency_overrides[deps.current_user] = lambda: db.get_user("903")
        wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("903")
        assert c.get(f"/reports/inbound/{rep['id']}.pdf").status_code in (302, 303, 403)
        print("smoke_inbound_report: OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
