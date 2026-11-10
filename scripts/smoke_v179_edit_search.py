#!/usr/bin/env python3
"""v1.7.9 — edit-record search (4-digit / id / keyword) and empty main-source download.

Temp DB only. Covers Persian/Arabic digits and yeh/kaf, a row with no id (found,
then an id is saved), duplicate id rejected, and «دانلود اکسل منبع اصلی» on an
empty table (header-only xlsx, no E-xxxx).
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def test_search_and_id() -> None:
    import os

    import config
    from db.models import Database
    from services import main_source as ms
    from services import permissions as perm

    live = (ROOT / "data" / "bot.db").resolve()
    assert Path(config.DATABASE_PATH).resolve() != live, config.DATABASE_PATH
    assert Path(os.environ["DATABASE_PATH"]).resolve() != live

    db = Database(Path(os.environ["DATABASE_PATH"]))
    perm.bind(db)
    db.upsert_user("901", role="owner", display_name="مالک")
    rows = [
        dict(category_code="1203", id="ID-AAA", product_name="بتن نسوز شرکت", keyword="نسوز",
             quantity=10, priority=1, unit="Kg"),
        dict(category_code="1203", id="ID-BBB", product_name="بتن دوم", keyword="بتن",
             quantity=4, priority=1, unit="Kg"),
        dict(category_code="۱۲۰۴", id="", product_name="آجر بدون شناسه", keyword="آجر\u200cي",
             quantity=1, priority=1, unit="عدد"),
    ]
    ms.persist_primary_frame(db, pd.DataFrame(rows), bale_user_id="901")
    frame = ms.load_primary_frame(db, bale_user_id="901")
    assert frame is not None and len(frame) == 3

    by_code = ms.search_inventory(frame, "۱۲۰۴")
    assert by_code["exact"] and by_code["exact"]["row"]["category_code"]
    assert not ms._cell_str(by_code["exact"]["row"].get("id")), by_code["exact"]["row"]

    two = ms.search_inventory(frame, "1203")
    assert two["exact"] is None and two["total"] == 2 and not two["more"], two

    by_id = ms.search_inventory(frame, "id-aaa")
    assert by_id["exact"] and ms._cell_str(by_id["exact"]["row"]["id"]) == "ID-AAA"

    yeh = "آجر " + "ي"  # space + Arabic yeh, same as ZWNJ folded to a space
    by_kw = ms.search_inventory(frame, yeh)
    assert by_kw["exact"] is None and by_kw["total"] == 1, by_kw
    assert "بدون آیدی" in ms.match_label(by_kw["matches"][0])
    assert ms.normalize_search("ك") == ms.normalize_search("ک")
    assert ms.normalize_search("ریخته\u200cگری") == ms.normalize_search("ریخته گری")

    blank = by_code["exact"]
    fields = ms.edit_fields(blank["row"])
    assert "id" in fields and fields.index("id") == fields.index("category_code") + 1
    assert "id" not in ms.edit_fields()  # rows that already have an id stay as before
    updates = ms.collect_edit_updates(blank["row"], {"id": "۱۲۳-نو", "keyword": "آجر"})
    assert updates.get("id") == "۱۲۳-نو", updates
    saved = ms.upsert_row(db, "", updates, bale_user_id="901", row_pos=blank["pos"])
    assert saved["id"] == "123-نو", saved
    again = ms.load_primary_frame(db, bale_user_id="901")
    found = ms.search_inventory(again, "123-نو")
    assert found["exact"] and ms._cell_str(found["exact"]["row"]["product_name"]) == "آجر بدون شناسه"

    try:
        ms.upsert_row(db, "ID-BBB", {"id": "۱۲۳-نو"}, bale_user_id="901")
    except ValueError as exc:
        assert "از قبل" in str(exc), exc
    else:
        _fail("duplicate id was accepted")
    still = ms.search_inventory(ms.load_primary_frame(db, bale_user_id="901"), "ID-BBB")
    assert still["exact"], "original id must stay when the duplicate is rejected"

    # more than the button cap
    extra = [dict(category_code=f"{1300+i:04d}", id=f"X{i}", product_name=f"قلم مشترک {i}",
                  keyword="مشترک", quantity=1, priority=1) for i in range(12)]
    ms.persist_primary_frame(db, pd.concat([again, pd.DataFrame(extra)], ignore_index=True), bale_user_id="901")
    many = ms.search_inventory(ms.load_primary_frame(db, bale_user_id="901"), "مشترک")
    assert many["more"] and len(many["matches"]) == ms.SEARCH_LIMIT and many["total"] == 12


def test_bot_and_web() -> None:
    import os

    import bot.handlers as handlers_mod
    import bot.keyboards as kb
    from db.models import Database
    from services import main_source as ms
    from services import permissions as perm

    db = Database(Path(os.environ["DATABASE_PATH"]).with_name("v179b.db"))
    perm.bind(db)
    db.upsert_user("901", role="owner", display_name="مالک")
    ms.persist_primary_frame(db, pd.DataFrame([
        dict(category_code="1501", id="KEEP", product_name="نگه دار", keyword="نگه", quantity=3, priority=1, unit="Kg"),
        dict(category_code="1502", id=None, product_name="بی آیدی", keyword="خالی", quantity=2, priority=1, unit="عدد"),
    ]), bale_user_id="901")

    class FakeClient:
        def __init__(self) -> None:
            self.msgs: list[str] = []
            self.markups: list = []
            self.docs: list[Path] = []

        def send_message(self, chat_id, text, reply_markup=None, **_k):
            self.msgs.append(text)
            self.markups.append(reply_markup)
            return {"ok": True, "message_id": len(self.msgs)}

        def send_document(self, chat_id, path, caption=None, **_k):
            self.docs.append(Path(path))
            self.msgs.append(caption or "")
            return {"ok": True}

        def answer_callback_query(self, *a, **k):
            return True

        def __getattr__(self, name):
            return lambda *a, **k: {"ok": True}

    client = FakeClient()
    app = handlers_mod.BotApp(client, db)
    msg = {"chat": {"id": 901}, "from": {"id": 901, "first_name": "مالک"}, "text": ""}

    def send(text: str) -> None:
        app.handle_update({"message": dict(msg, text=text)})

    send(kb.BTN_UPLOAD_MENU)
    send(kb.BTN_MAIN_SOURCE_FILE)
    send(kb.BTN_INV_EDIT_RECORD)
    assert "کد چهار رقمی" in client.msgs[-1] and "کلیدواژه" in client.msgs[-1], client.msgs[-1]
    send("1501")
    assert "رکورد فعلی" in client.msgs[-2]
    fields_keep = ms.edit_fields()
    assert "id" not in fields_keep
    for _f in fields_keep:
        send(kb.BTN_EDIT_KEEP)
    assert "تغییری ثبت نشد" in client.msgs[-1], client.msgs[-1]

    send(kb.BTN_INV_EDIT_RECORD)
    send("خالي")  # Arabic yeh in خالي vs stored خالی
    markup = client.markups[-1] or {}
    buttons = [b for row in markup.get("inline_keyboard") or [] for b in row]
    pick = next(b for b in buttons if str(b.get("callback_data", "")).startswith(ms.CB_EDIT_PICK))
    assert "بدون آیدی" in pick["text"], pick
    app.handle_update({"callback_query": {
        "id": "cq1", "data": pick["callback_data"],
        "from": msg["from"], "message": {"message_id": 1, "chat": msg["chat"]},
    }})
    assert "رکورد فعلی" in client.msgs[-2], client.msgs[-2:]
    row = ms.search_inventory(ms.load_primary_frame(db, bale_user_id="901"), "1502")["exact"]["row"]
    fields = ms.edit_fields(row)
    assert "id" in fields
    for f in fields:
        send("NEW-1" if f == "id" else kb.BTN_EDIT_KEEP)
    assert "✅" in client.msgs[-1], client.msgs[-1]
    got = ms.search_inventory(ms.load_primary_frame(db, bale_user_id="901"), "NEW-1")
    assert got["exact"], "id was not saved from the bot flow"
    try:
        ms.upsert_row(db, "KEEP", {"id": "NEW-1"}, bale_user_id="901")
    except ValueError as exc:
        assert "از قبل" in str(exc)
    else:
        _fail("bot-side duplicate id accepted")

    # empty table → header template, never a tracking code
    empty_db = Database(Path(os.environ["DATABASE_PATH"]).with_name("v179empty.db"))
    perm.bind(empty_db)
    empty_db.upsert_user("901", role="owner", display_name="مالک")
    client.msgs.clear()
    client.docs.clear()
    app.db = empty_db
    send(kb.BTN_INV_DOWNLOAD)
    blob = "\n".join(client.msgs)
    assert "رکورد داده‌ای ندارد" in blob and "کد پیگیری" not in blob, blob
    assert client.docs and client.docs[-1].suffix == ".xlsx"
    sheet = pd.read_excel(client.docs[-1])
    assert len(sheet) == 0 and "کد دسته بندی" in list(sheet.columns), list(sheet.columns)

    from fastapi.testclient import TestClient

    import web.deps as deps
    from web.app import create_app

    deps._db = db
    wapp = create_app()
    wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("901")
    wapp.dependency_overrides[deps.current_user] = lambda: db.get_user("901")
    c = TestClient(wapp)
    page = c.get("/settings/main-source/edit", params={"q": "نگه"})
    assert page.status_code == 200 and ("نگه دار" in page.text or "KEEP" in page.text), page.status_code
    missing = c.get("/settings/main-source/edit", params={"id": "NOPE"})
    assert missing.status_code == 400
    deps._db = empty_db
    wapp.dependency_overrides[deps.current_user_optional] = lambda: empty_db.get_user("901")
    wapp.dependency_overrides[deps.current_user] = lambda: empty_db.get_user("901")
    dl = c.get("/settings/main-source/download.xlsx")
    assert dl.status_code == 200 and dl.content[:2] == b"PK", dl.status_code
    web_sheet = pd.read_excel(io.BytesIO(dl.content))
    assert len(web_sheet) == 0 and "شناسه مواد" in list(web_sheet.columns)


def main() -> int:
    test_search_and_id()
    test_bot_and_web()
    print("SMOKE_V179_EDIT_SEARCH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
