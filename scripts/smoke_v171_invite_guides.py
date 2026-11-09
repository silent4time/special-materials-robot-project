#!/usr/bin/env python3
"""Smoke v1.7.1 — invite welcome + role guide / intro PDFs (temp DB only, never data/bot.db).

1) services.guide_docs: packaged default per role + intro; uploaded override wins;
   restore default; non-PDF / fake PDF / oversize rejected.
2) bot: invite redeem → welcome, then the guide of the invited role + the intro PDF
   (send_document mocked); a later plain /start sends no PDF; a missing file is skipped
   without breaking the welcome; first-owner claim also gets owner guide + intro.
3) bot: «👥 کاربران» → «📄 راهنمای نقش» (one button per role) / «📘 معرفی ربات»:
   status, upload PDF (pending state), preview, restore; non-admin roles denied.
4) web /settings/users: same two sections; upload / download / restore share the bot
   storage; non-admin roles get 403.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads/bot_assets before config import

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import _smoke_isolation  # noqa: E402,F401
import config  # noqa: E402

PDF_BYTES = b"%PDF-1.4\n% smoke\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
ROLES = list(config.ROLES)


def _db(name: str):
    from db.models import Database
    from services import permissions as perm

    db = Database(Path(os.environ["DATABASE_PATH"]).with_name(name))
    perm.bind(db)
    return db


def _assert_live_untouched() -> None:
    assert Path(config.DATABASE_PATH).resolve() != (ROOT / "data" / "bot.db").resolve()
    assert Path(config.BOT_ASSETS_DIR).resolve() != (ROOT / "data" / "bot_assets").resolve()


def test_service(tmp: Path) -> None:
    from services import guide_docs as gd

    db = _db("v171_service.db")
    assert gd.TARGETS == ("intro", *ROLES)
    for t in gd.TARGETS:
        d = gd.default_path(t)
        assert d == ROOT / "docs" / "default_guides" / f"{t}.pdf" and d.is_file(), d
        assert d.read_bytes()[:5] == b"%PDF-", d
        assert gd.resolve(db, t) == d and not gd.status(db, t)["is_custom"]
        assert gd.status(db, t)["source_fa"] == "فایل پیش‌فرض"
    assert gd.caption_fa("technician") == "راهنمای کار با ربات — نقش تکنسین"
    assert gd.caption_fa("intro") == "معرفی ربات مواد نسوز تاندیش"
    assert gd.download_name("shift_supervisor") == "راهنمای-مسئول-شیفت.pdf"
    docs = gd.invite_documents(db, "responsible_officer")
    assert [t for t, _ in docs] == ["responsible_officer", "intro"]
    assert docs[0][1].name == "responsible_officer.pdf" and docs[1][1].name == "intro.pdf"

    # upload override wins
    src = tmp / "up.pdf"
    src.write_bytes(PDF_BYTES)
    dest = gd.save_upload(db, "technician", src, "راهنمای تازه.pdf", updated_by="901")
    assert dest.parent == Path(config.BOT_ASSETS_DIR) and dest.is_file() and not src.exists()
    assert gd.resolve(db, "technician") == dest and gd.status(db, "technician")["is_custom"]
    assert gd.status(db, "technician")["name"] == "راهنمای تازه.pdf"
    assert db.get_setting("guide_pdf_technician") == str(dest)
    assert gd.resolve(db, "manager") == gd.default_path("manager")  # others unaffected
    # file deleted behind our back → default
    dest.unlink()
    assert gd.resolve(db, "technician") == gd.default_path("technician")
    src.write_bytes(PDF_BYTES)
    dest = gd.save_upload(db, "technician", src, "x.pdf", updated_by="901")
    # restore default
    assert gd.restore_default(db, "technician", updated_by="901") is True
    assert not dest.exists() and db.get_setting("guide_pdf_technician") is None
    assert gd.resolve(db, "technician") == gd.default_path("technician")
    assert gd.restore_default(db, "technician", updated_by="901") is False

    # rejects
    for name, data, frag in (
        ("a.docx", PDF_BYTES, "فقط فایل PDF"),
        ("fake.pdf", b"PK\x03\x04 not a pdf", "معتبر نیست"),
        ("empty.pdf", b"", "خالی"),
    ):
        bad = tmp / "bad.bin"
        bad.write_bytes(data)
        try:
            gd.save_upload(db, "intro", bad, name, updated_by="901")
        except ValueError as exc:
            assert frag in str(exc), (name, exc)
        else:
            raise AssertionError(f"accepted {name}")
        assert not bad.exists()
    big = tmp / "big.pdf"
    with big.open("wb") as fh:
        fh.write(PDF_BYTES)
        fh.truncate(gd.MAX_PDF_BYTES + 1)
    assert "حجم" in (gd.validate_pdf(big) or "")
    assert db.get_setting("intro_pdf_path") is None
    try:
        gd.setting_key("nobody")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown target accepted")
    print("guide_docs service OK")


class FakeClient:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.events: list[tuple] = []  # ("msg", text) | ("photo", caption) | ("doc", path, caption, filename)
        self.download_bytes = PDF_BYTES

    def send_message(self, chat_id, text, reply_markup=None, **_kw):
        self.events.append(("msg", text, reply_markup))
        return {"ok": True}

    def send_photo(self, chat_id, path, caption=None, reply_markup=None, **_kw):
        self.events.append(("photo", caption, reply_markup))
        return {"ok": True}

    def send_document(self, chat_id, path, caption=None, filename=None, **_kw):
        self.events.append(("doc", Path(path), caption, filename))
        return {"ok": True}

    def download_file(self, file_id, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.download_bytes)
        return Path(dest)

    def get_me(self):
        return {"username": "smoke_bot"}

    def __getattr__(self, name):
        return lambda *a, **k: {"ok": True}

    def docs(self):
        return [e for e in self.events if e[0] == "doc"]

    def texts(self):
        return [e[1] for e in self.events if e[0] in ("msg", "photo")]


def test_bot_invite(tmp: Path) -> None:
    import bot.handlers as handlers_mod
    from services import guide_docs as gd

    db = _db("v171_bot.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")
    client = FakeClient(tmp)
    app = handlers_mod.BotApp(client, db)

    def send(uid: int, text: str, **extra):
        msg = {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": f"u{uid}"}, "text": text}
        msg.update(extra)
        app.handle_update({"message": msg})

    for i, role in enumerate(ROLES):
        uid = 7000 + i
        inv = db.create_invite(role=role, created_by="901", expires_days=7)
        client.events.clear()
        send(uid, f"/start {inv['token']}")
        assert db.get_user(str(uid))["role"] == role
        kinds = [e[0] for e in client.events]
        assert kinds[0] in ("msg", "photo") and "دعوت پذیرفته شد" in client.events[0][1], client.events[:1]
        docs = client.docs()
        assert len(docs) == 2, (role, client.events)
        assert kinds.index("doc") > 0, "welcome first, then documents"
        assert docs[0][1] == gd.default_path(role) and docs[0][2] == gd.caption_fa(role), docs[0]
        assert docs[0][2] == f"راهنمای کار با ربات — نقش {config.ROLES[role]}"
        assert docs[1][1] == gd.default_path("intro") and docs[1][2] == "معرفی ربات مواد نسوز تاندیش", docs[1]
        assert docs[0][3] == gd.download_name(role) and docs[1][3] == gd.download_name("intro")
        # later plain /start → welcome again, NO pdf
        client.events.clear()
        send(uid, "/start")
        assert client.docs() == [] and client.texts(), client.events
    # a used invite token again → error text, no pdf
    inv = db.create_invite(role="technician", created_by="901", expires_days=7)
    send(7100, f"/start {inv['token']}")
    client.events.clear()
    send(7101, f"/start {inv['token']}")
    assert client.docs() == [], client.events

    # uploaded override is what invitees get; missing intro is skipped without breaking welcome
    src = tmp / "tech.pdf"
    src.write_bytes(PDF_BYTES)
    custom = gd.save_upload(db, "technician", src, "tech.pdf", updated_by="901")
    orig_default = gd.default_path
    gd.default_path = lambda t: (tmp / "missing.pdf") if t == "intro" else orig_default(t)
    try:
        inv = db.create_invite(role="technician", created_by="901", expires_days=7)
        client.events.clear()
        send(7200, f"/start {inv['token']}")
        docs = client.docs()
        assert [d[1] for d in docs] == [custom], docs
        assert "دعوت پذیرفته شد" in client.texts()[0]
    finally:
        gd.default_path = orig_default
    gd.restore_default(db, "technician", updated_by="901")

    # send_document failure → logged, redeem still completes
    boom_client = FakeClient(tmp)
    boom_client.send_document = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    app2 = handlers_mod.BotApp(boom_client, db)
    inv = db.create_invite(role="manager", created_by="901", expires_days=7)
    app2.handle_update({"message": {"chat": {"id": 7300}, "from": {"id": 7300, "first_name": "x"},
                                    "text": f"/start {inv['token']}"}})
    assert db.get_user("7300")["role"] == "manager" and "دعوت پذیرفته شد" in boom_client.texts()[0]

    # first install: first user claims owner → owner guide + intro, once
    db2 = _db("v171_claim.db")
    c2 = FakeClient(tmp)
    app3 = handlers_mod.BotApp(c2, db2)
    app3.handle_update({"message": {"chat": {"id": 8000}, "from": {"id": 8000, "first_name": "o"}, "text": "/start"}})
    assert db2.get_user("8000")["role"] == "owner"
    assert [d[1].name for d in c2.docs()] == ["owner.pdf", "intro.pdf"], c2.events
    c2.events.clear()
    app3.handle_update({"message": {"chat": {"id": 8000}, "from": {"id": 8000, "first_name": "o"}, "text": "/start"}})
    assert c2.docs() == []
    from services import permissions as perm

    perm.bind(db)
    print("bot invite redeem → welcome + role guide + intro OK")


def test_bot_menus(tmp: Path) -> None:
    import bot.handlers as handlers_mod
    import bot.keyboards as kb
    from services import guide_docs as gd

    db = _db("v171_menu.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")
    db.upsert_user("902", role="manager", display_name="مدیر تست")
    db.upsert_user("903", role="technician", display_name="تکنسین")
    db.upsert_user("904", role="responsible_officer", display_name="کاردان")
    client = FakeClient(tmp)
    app = handlers_mod.BotApp(client, db)

    def send(uid: int, text: str = "", **extra):
        msg = {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": f"u{uid}"}, "text": text}
        msg.update(extra)
        app.handle_update({"message": msg})

    def last_markup_texts():
        for e in reversed(client.events):
            if e[0] in ("msg", "photo") and e[2]:
                return kb.keyboard_texts(e[2])
        return []

    send(902, kb.BTN_USERS)
    labels = last_markup_texts()
    assert kb.BTN_USERS_ROLE_GUIDES in labels and kb.BTN_USERS_INTRO_DOC in labels, labels
    assert kb.BTN_USERS_ROLE_GUIDES == "📄 راهنمای نقش" and kb.BTN_USERS_INTRO_DOC == "📘 معرفی ربات"

    send(902, kb.BTN_USERS_ROLE_GUIDES)
    labels = last_markup_texts()
    for r in ROLES:
        assert kb.role_guide_button(r) in labels, (r, labels)
    assert "فایل پیش‌فرض" in client.texts()[-1]

    # role → status + upload → preview → restore
    send(902, kb.role_guide_button("shift_supervisor"))
    assert "راهنمای نقش مسئول شیفت" in client.texts()[-1] and "فایل پیش‌فرض" in client.texts()[-1]
    labels = last_markup_texts()
    assert {kb.BTN_GDOC_SEND, kb.BTN_GDOC_UPLOAD, kb.BTN_GDOC_RESTORE} <= set(labels), labels
    client.events.clear()
    send(902, kb.BTN_GDOC_SEND)
    assert client.docs()[-1][1] == gd.default_path("shift_supervisor")
    send(902, kb.BTN_GDOC_UPLOAD)
    assert "pdf" in client.texts()[-1].lower()
    send(902, "سلام")  # typed text while waiting → reminder, still waiting
    assert "منتظر فایل PDF" in client.texts()[-1]
    # non-PDF document refused
    send(902, document={"file_id": "F1", "file_name": "x.xlsx", "mime_type": "application/vnd.ms-excel"})
    assert "فقط فایل PDF" in client.texts()[-1]
    # fake PDF (bad magic) refused
    client.download_bytes = b"not a pdf at all"
    send(902, document={"file_id": "F2", "file_name": "x.pdf", "mime_type": "application/pdf"})
    assert "معتبر نیست" in client.texts()[-1], client.texts()[-1]
    assert db.get_setting("guide_pdf_shift_supervisor") is None
    # real PDF accepted
    client.download_bytes = PDF_BYTES
    send(902, document={"file_id": "F3", "file_name": "شیفت.pdf", "mime_type": "application/pdf", "file_size": 60})
    assert "✅ فایل ذخیره شد" in client.texts()[-1], client.texts()[-1]
    saved = Path(db.get_setting("guide_pdf_shift_supervisor"))
    assert saved.is_file() and saved.parent == Path(config.BOT_ASSETS_DIR)
    assert gd.resolve(db, "shift_supervisor") == saved
    acts = [r for r in db.list_user_activities() if r["action_key"] == "settings_guide_doc"]
    assert acts and "راهنمای نقش مسئول شیفت" in acts[-1]["message_fa"], acts
    client.events.clear()
    send(902, kb.BTN_GDOC_SEND)
    assert client.docs()[-1][1] == saved and "فایل بارگذاری‌شده" in client.texts()[-1]
    # invitee with that role now gets the uploaded file
    inv = db.create_invite(role="shift_supervisor", created_by="902", expires_days=7)
    client.events.clear()
    send(9100, f"/start {inv['token']}")
    assert [d[1] for d in client.docs()] == [saved, gd.default_path("intro")]
    # restore
    send(902, kb.BTN_USERS_ROLE_GUIDES)
    send(902, kb.role_guide_button("shift_supervisor"))
    send(902, kb.BTN_GDOC_RESTORE)
    assert "فایل پیش‌فرض دوباره فعال شد" in client.texts()[-1], client.texts()[-1]
    assert not saved.exists() and gd.resolve(db, "shift_supervisor") == gd.default_path("shift_supervisor")

    # intro section (owner)
    send(901, kb.BTN_USERS)
    send(901, kb.BTN_USERS_INTRO_DOC)
    assert "معرفی ربات مواد نسوز تاندیش" in client.texts()[-1] and "فایل پیش‌فرض" in client.texts()[-1]
    send(901, kb.BTN_GDOC_UPLOAD)
    send(901, document={"file_id": "F4", "file_name": "intro-new.pdf", "mime_type": "application/pdf"})
    assert gd.status(db, "intro")["is_custom"]
    # back → users menu, pending cleared
    send(901, kb.BTN_BACK)
    assert "901" not in app._guide_doc_pending
    send(901, kb.BTN_USERS_INTRO_DOC)
    send(901, kb.BTN_GDOC_RESTORE)
    assert not gd.status(db, "intro")["is_custom"]
    # back from a role item → role list
    send(901, kb.BTN_USERS_ROLE_GUIDES)
    send(901, kb.role_guide_button("owner"))
    send(901, kb.BTN_BACK)
    assert kb.role_guide_button("manager") in last_markup_texts()

    # non-admin roles denied (button gate + direct document)
    for uid in (903, 904):
        client.events.clear()
        send(uid, kb.BTN_USERS_ROLE_GUIDES)
        send(uid, kb.role_guide_button("technician"))
        send(uid, kb.BTN_GDOC_UPLOAD)
        assert client.docs() == [] and str(uid) not in app._guide_doc_pending
        assert all(kb.BTN_GDOC_UPLOAD not in (kb.keyboard_texts(e[2]) if e[0] == "msg" and e[2] else [])
                   for e in client.events)
    assert db.get_setting("guide_pdf_technician") is None
    print("bot «📄 راهنمای نقش» / «📘 معرفی ربات» menus OK")


def test_web(tmp: Path) -> None:
    from fastapi.testclient import TestClient

    import web.deps as deps
    from services import guide_docs as gd

    db = _db("v171_web.db")
    db.upsert_user("901", role="owner", display_name="مالک تست")
    db.upsert_user("902", role="manager", display_name="مدیر تست")
    db.upsert_user("903", role="technician", display_name="تکنسین")
    db.upsert_user("904", role="responsible_officer", display_name="کاردان")
    db.upsert_user("905", role="shift_supervisor", display_name="شیفت")
    deps._db = db
    from web.app import create_app

    current = {"uid": "902"}
    wapp = create_app()
    wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user(current["uid"])
    wapp.dependency_overrides[deps.current_user] = lambda: db.get_user(current["uid"])
    c = TestClient(wapp)

    page = c.get("/settings/users")
    assert page.status_code == 200, page.status_code
    assert "📘 معرفی ربات" in page.text and "📄 راهنمای نقش" in page.text
    for t in gd.TARGETS:
        assert f"/settings/users/guides/{t}/upload" in page.text and f"/settings/users/guides/{t}/file" in page.text
    assert "فایل پیش‌فرض" in page.text
    dl = c.get("/settings/users/guides/technician/file")
    assert dl.status_code == 200 and dl.content == gd.default_path("technician").read_bytes()
    assert dl.headers["content-type"].startswith("application/pdf")
    assert c.get("/settings/users/guides/nobody/file").status_code == 404

    bad = c.post("/settings/users/guides/manager/upload", files={"file": ("x.png", b"\x89PNG", "image/png")})
    assert bad.status_code == 400 and "فقط فایل PDF" in bad.text
    fake = c.post("/settings/users/guides/manager/upload", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert fake.status_code == 400 and "معتبر نیست" in fake.text
    ok = c.post("/settings/users/guides/manager/upload", files={"file": ("مدیر.pdf", PDF_BYTES, "application/pdf")})
    assert ok.status_code == 200 and "ذخیره شد" in ok.text, ok.text[:300]
    assert "فایل بارگذاری‌شده" in ok.text and "مدیر.pdf" in ok.text
    path = gd.resolve(db, "manager")
    assert path.parent == Path(config.BOT_ASSETS_DIR) and path.read_bytes() == PDF_BYTES
    assert c.get("/settings/users/guides/manager/file").content == PDF_BYTES
    assert gd.invite_documents(db, "manager")[0][1] == path  # the bot sends this file

    rs = c.post("/settings/users/guides/manager/restore")
    assert rs.status_code == 200 and "فایل پیش‌فرض دوباره فعال شد" in rs.text
    assert gd.resolve(db, "manager") == gd.default_path("manager") and not path.exists()

    current["uid"] = "901"
    ok = c.post("/settings/users/guides/intro/upload", files={"file": ("intro.pdf", PDF_BYTES, "application/pdf")})
    assert ok.status_code == 200 and gd.status(db, "intro")["is_custom"]
    assert c.post("/settings/users/guides/intro/restore").status_code == 200

    for uid in ("903", "904", "905"):
        current["uid"] = uid
        assert c.get("/settings/users").status_code == 403, uid
        assert c.get("/settings/users/guides/intro/file").status_code == 403, uid
        r = c.post("/settings/users/guides/technician/upload", files={"file": ("t.pdf", PDF_BYTES, "application/pdf")})
        assert r.status_code == 403, (uid, r.status_code)
        assert c.post("/settings/users/guides/technician/restore").status_code == 403
    assert db.get_setting("guide_pdf_technician") is None
    print("web /settings/users guides (upload / download / restore / 403) OK")


def main() -> int:
    import tempfile

    _assert_live_untouched()
    tmp = Path(tempfile.mkdtemp(prefix="smoke_v171_"))
    try:
        test_service(tmp)
        test_bot_invite(tmp)
        test_bot_menus(tmp)
        test_web(tmp)
        print("SMOKE_V171_OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
