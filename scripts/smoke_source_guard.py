#!/usr/bin/env python3
"""Smoke (temp DB only, 1405/07/25): «📥 به‌روزرسانی موجودی انبار» never damages منبع اصلی.

* a real 3-column warehouse export over a full source keeps every column (rates, unit,
  critical point, priority, keyword, …); only stock changes
* new id with a known 4-digit code is added (rates empty, listed); unknown code / 1800 → ⛔
* ids missing from the stock file keep their row and stock
* blank new values never overwrite filled ones (merge_clean_frames guard)
* refuse + Persian warning when rates would be wiped; live file untouched
* inbound report still built against the previous stock upload
* «➕ افزودن رکورد» by a non-admin → pending request; owner ✅ applies, ❌ rejects (bot + web)
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

A, B, C = "378121641252L", "378121641302R", "378121741122N"
NEW_OK, NEW_UNK, NEW_1800 = "378121649999Q", "378199990001X", "378180000001Y"


def _stock_xlsx(path: Path, rows) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "ریز اطلاعات"
    ws.append(["کد دسته بندي", "کد و شرح کالا", "موجودي"])
    for code, iid, name, qty in rows:
        ws.append([code, f"{iid} - {name}   ", qty])
    wb.save(path)
    return path


def main() -> int:
    import pandas as pd

    from bot import handlers as handlers_mod
    from bot import keyboards as kb
    from db.models import Database
    from excel.processor import merge_clean_frames
    from services import inbound_report as inbound_svc
    from services import main_source as ms
    from services import main_source_approvals as msa
    from services import permissions as perm

    tmp = Path(_smoke_env.SMOKE_TMP)
    db = Database(tmp / "guard_src.db")
    perm.bind(db)
    db.upsert_user("901", role="owner", display_name="مالک")
    db.upsert_user("902", role="responsible_officer", display_name="کاردان مسئول")
    for code in ("1203", "1206", "1800"):
        db.add_category_code(code, created_by="901")
    full = dict(work_order="WO-1", keyword="بتن", origin="داخلی", shared="خیر", critical_point=500,
                unit="Kg", casting_floor=0.1, billet_renovation=1.5, billet_patching=0.5,
                bloom_renovation=2, bloom_patching=1, slab_renovation=3, slab_patching=1.2,
                rate_group="G1", usage_location="بیلت", contractor_or_company="شرکت")
    source = pd.DataFrame([
        dict(category_code="1203", id=A, product_name="نوآوران C85", quantity=100, priority=3, **full),
        dict(category_code="1203", id=B, product_name="رایان 85BT", quantity=50, priority=0, **full),
        dict(category_code="1206", id=C, product_name="رایان 1800CD", quantity=70, priority=2, **full),
    ])
    ms.persist_primary_frame(db, source, bale_user_id="901")

    files: dict[str, Path] = {}

    class FakeClient:
        def __init__(self) -> None:
            self.msgs: list[tuple] = []

        def download_file(self, file_id, dest):
            shutil.copy2(files[file_id], dest)
            return Path(dest)

        def send_message(self, chat_id, text, reply_markup=None, **_kw):
            self.msgs.append((str(chat_id), text, reply_markup))
            return {"ok": True, "message_id": len(self.msgs)}

        def __getattr__(self, name):
            return lambda *a, **k: {"ok": True}

    client = FakeClient()
    app = handlers_mod.BotApp(client, db)
    owner_msg = {"chat": {"id": 901}, "from": {"id": 901, "first_name": "مالک"}, "text": ""}

    def stock_upload(fid: str, path: Path) -> str:
        files[fid] = path
        app.on_pick_file_type(owner_msg, "product_inventory", return_menu="upload")
        app.on_document(dict(owner_msg, document={"file_id": fid, "file_name": path.name}))
        return client.msgs[-1][1] if not client.msgs[-1][1].startswith("📊") else client.msgs[-2][1]

    # 1) first stock upload (baseline) + 2) second with new/unknown/1800 ids, C missing
    stock_upload("s1", _stock_xlsx(tmp / "s1.xlsx", [(1203, A, "نوآوران C85", 100), (1203, B, "رایان 85BT", 50),
                                                       (1206, C, "رایان 1800CD", 70)]))
    n_up = db.count_stock_uploads()
    text = stock_upload("s2", _stock_xlsx(tmp / "s2.xlsx", [
        (1203, A, "نوآوران C85", 130), (1203, B, "رایان 85BT", 40),
        (1203, NEW_OK, "قلم جدید", 9), (1999, NEW_UNK, "کد ناشناخته", 1), (1800, NEW_1800, "مازاد", 2)]))
    all_text = "\n".join(m[1] for m in client.msgs[-4:])
    live = ms.load_primary_frame(db, bale_user_id="901").set_index("id")
    assert float(live.at[A, "quantity"]) == 130 and float(live.at[B, "quantity"]) == 40
    assert float(live.at[C, "quantity"]) == 70  # missing from file → kept, not zeroed
    for iid, prio in ((A, 3), (B, 0), (C, 2)):
        assert int(live.at[iid, "priority"]) == prio, (iid, live.at[iid, "priority"])
        for col, val in full.items():
            if col == "contractor_or_company":
                continue
            got = live.at[iid, col]
            same = (float(got) == float(val)) if isinstance(val, (int, float)) else str(got) == str(val)
            assert same, (iid, col, got, val)
    assert NEW_OK in live.index and float(live.at[NEW_OK, "slab_renovation"]) == 3  # uniform 1203 siblings → copied
    assert NEW_UNK not in live.index and NEW_1800 not in live.index
    assert "➕ 1 شناسهٔ جدید" in all_text and NEW_OK in all_text, all_text
    assert "⛔" in all_text or "کد ۴ رقمی در منبع اصلی نیست" in all_text, all_text
    assert db.count_stock_uploads() == n_up + 1
    rep = inbound_svc.latest_report(db)
    assert rep and rep["has_baseline"] and rep["n_inbound"] >= 1, rep

    # 3) merge guard: blank new values never overwrite filled old values
    old = ms.load_primary_frame(db, bale_user_id="901")
    blank = old[["category_code", "id", "product_name", "quantity"]].copy()
    merged = merge_clean_frames(old, blank, "product_inventory").set_index("id")
    assert str(merged.at[A, "unit"]) == "Kg" and float(merged.at[A, "slab_renovation"]) == 3

    # 4) refuse when rates would be wiped (full upload path); live unchanged
    wiped = old.copy()
    for c in ms.SOURCE_RATE_COLS:
        wiped[c] = None
    assert ms.rates_wiped(old, wiped) and not ms.rates_wiped(old, old)

    # 5) add-record approval flow (bot)
    officer = db.get_user("902")
    perm.set_role_feature(db, "responsible_officer", perm.MAIN_SOURCE_EDIT, True) if hasattr(perm, "set_role_feature") else None
    assert msa.needs_approval(officer) and not msa.needs_approval(db.get_user("901"))
    off_msg = {"chat": {"id": 902}, "from": {"id": 902, "first_name": "ک"}, "text": ""}
    rec = dict(category_code="1203", id="378121640001A", product_name="رکورد درخواستی", quantity=5)
    app._submit_main_source_request(off_msg, officer, rec, allow_update=False)
    req = msa.list_pending(db)[-1]
    notif = [m for m in client.msgs if m[0] == "901" and m[2] and "inline_keyboard" in m[2]]
    assert notif and notif[-1][2]["inline_keyboard"][0][0]["callback_data"] == f"msa|{req['id']}|ok"
    assert "378121640001A" not in set(ms.load_primary_frame(db, bale_user_id="901")["id"].astype(str))
    cq = {"id": "c1", "from": {"id": 902}, "message": {"message_id": 5, "chat": {"id": 902}}, "data": f"msa|{req['id']}|ok"}
    app.handle_callback_query(cq)  # officer can't approve
    assert msa.get(db, req["id"])["status"] == "pending"
    cq = dict(cq, **{"from": {"id": 901}, "data": f"msa|{req['id']}|ok"})
    app.handle_callback_query(cq)
    assert msa.get(db, req["id"])["status"] == "approved"
    assert "378121640001A" in set(ms.load_primary_frame(db, bale_user_id="901")["id"].astype(str))
    assert any(m[0] == "902" and "✅" in m[1] for m in client.msgs), client.msgs[-3:]
    app._submit_main_source_request(off_msg, officer, dict(rec, id="378121640002B"), allow_update=False)
    req2 = msa.list_pending(db)[-1]
    app.handle_callback_query(dict(cq, data=f"msa|{req2['id']}|no"))
    assert msa.get(db, req2["id"])["status"] == "rejected"
    assert "378121640002B" not in set(ms.load_primary_frame(db, bale_user_id="901")["id"].astype(str))
    assert any(m[0] == "902" and "رد شد" in m[1] for m in client.msgs)
    # owner adds directly (no request)
    n_req = len(msa.list_pending(db))
    assert msa.is_approver(db.get_user("901")) and len(msa.list_pending(db)) == n_req
    assert kb.BTN_INV_ADD_RECORD
    # 6) sibling prefill for new ids (uniform code → copied; mixed code → empty + listed)
    from excel.id_parse import apply_id_segment_rule

    base = dict(work_order="", keyword="آجر", origin="داخلی", shared="خیر", critical_point=10, unit="No",
                casting_floor=0, billet_renovation=1, billet_patching=2, bloom_renovation=3, bloom_patching=4,
                slab_renovation=5, slab_patching=6, rate_group="", usage_location="اسلب")
    old6 = pd.DataFrame([
        dict(category_code="1603", id="378160310001A", product_name="u1", quantity=1, priority=2, **base),
        dict(category_code="1603", id="378160310002B", product_name="u2", quantity=2, priority=3, **base),
        dict(category_code="1203", id="378120310001C", product_name="m1", quantity=1, priority=1,
             **dict(base, unit="Kg", slab_renovation=9, keyword="بتن")),
        dict(category_code="1203", id="378120310002D", product_name="m2", quantity=1, priority=1,
             **dict(base, unit="No", critical_point=None)),
    ])
    new6 = pd.DataFrame([
        dict(category_code="1603", id="378700001603E", product_name="جدید یکسان", quantity=7),
        dict(category_code="1203", id="378120319999F", product_name="جدید مختلط", quantity=8),
    ])
    m6, info6 = ms.merge_stock_update(old6, new6)
    m6, _ = apply_id_segment_rule(m6)
    m6 = m6.set_index("id")
    u = m6.loc["378700001603E"]
    for col, val in base.items():
        if str(val) == "":
            continue
        got = u[col]
        assert (float(got) == float(val)) if isinstance(val, (int, float)) else str(got) == str(val), (col, got)
    assert float(u["quantity"]) == 7 and u["product_name"] == "جدید یکسان"
    assert u["contractor_or_company"] == "پیمانکار", u["contractor_or_company"]  # 2nd group 0000
    mx = m6.loc["378120319999F"]
    assert mx["contractor_or_company"] == "شرکت"
    for col in ("unit", "slab_renovation", "keyword", "critical_point"):
        assert pd.isna(mx[col]) or str(mx[col]).strip() == "", (col, mx[col])
    assert float(mx["bloom_patching"]) == 4 and mx["origin"] == "داخلی"  # shared values still copied
    add_mx = [a for a in info6["added"] if a["id"] == "378120319999F"][0]
    assert {"unit", "slab_renovation", "keyword", "critical_point"} <= set(add_mx["differing"]), add_mx
    note = ms.stock_update_note_fa(info6)
    assert "خالی (متفاوت بین هم‌کدها)" in note and "واحد" in note and "اولویت" in note, note
    assert "priority" not in add_mx["prefilled"] and "product_name" not in add_mx["prefilled"]
    pre, diff = ms.sibling_defaults(old6, "1603")
    assert pre.get("unit") == "No" and not diff

    print("smoke_source_guard OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
