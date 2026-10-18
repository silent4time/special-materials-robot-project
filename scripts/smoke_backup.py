#!/usr/bin/env python3
"""💾 بک‌آپ/بازگردانی — temp copies only (live data/backups, bot_assets, DB never touched):
creation + zip contents (no .env/token), validation (tamper, traversal, foreign, non-zip),
restore round-trip with automatic pre-restore backup, retention, nightly job + failure
notice, bot permissions + two-step restore, web pages + owner-only restore."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TOKEN = "987654321:SECRET-token_SHOULD_NOT_LEAK"


def main() -> int:
    from services import backup as bk

    tmp = Path(_smoke_env.SMOKE_TMP)
    paths = bk.Paths(db=tmp / "guard.db", uploads=tmp / "uploads", assets=tmp / "assets",
                     backups=tmp / "backups", env=tmp / ".env")
    bk.DEFAULTS.update(db=paths.db, uploads=paths.uploads, assets=paths.assets, backups=paths.backups, env=paths.env)
    live_backups = ROOT / "data" / "backups"
    live_before = set(os.listdir(live_backups)) if live_backups.is_dir() else set()

    from db.models import Database
    from services import permissions as perm

    db = Database(paths.db)
    perm.bind(db)
    db.upsert_user("701", "owner", display_name="مالک")
    db.upsert_user("702", "manager", display_name="مدیر")
    db.upsert_user("703", "technician", display_name="تکنسین")
    (paths.uploads / "701" / "1" / "cleaned").mkdir(parents=True)
    src_file = paths.uploads / "701" / "1" / "cleaned" / "product_inventory.xlsx"
    src_file.write_bytes(b"PK-fake-excel-main-source")
    paths.assets.mkdir(parents=True)
    (paths.assets / "logo_701.jpg").write_bytes(b"\xff\xd8logo")
    paths.env.write_text(f"BALE_BOT_TOKEN={TOKEN}\nWEB_ADMIN_PASSWORD=pw123secret\nWEB_SECRET_KEY=abc\nWEB_PORT=8000\nCRITICAL_DAYS=3\n")

    # 1) creation + contents
    z = bk.create_backup(bk.MANUAL, paths=paths, actor="701")
    assert bk.NAME_RE.match(z.name) and z.name.endswith("_manual.zip"), z.name
    assert z.name.startswith("nasoz_backup_14"), z.name  # Jalali year
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "db/bot.db", "config/env_public.txt",
                "uploads/701/1/cleaned/product_inventory.xlsx", "bot_assets/logo_701.jpg"} <= names, names
        assert not any(n.endswith(".env") or "/.env" in n for n in names)
        blob = b"".join(zf.read(n) for n in names)
        assert TOKEN.encode() not in blob and b"pw123secret" not in blob and b"SECRET-token" not in blob
        env_pub = zf.read("config/env_public.txt").decode()
        assert "WEB_PORT=8000" in env_pub and "WEB_SECRET_KEY" not in env_pub
        man = json.loads(zf.read("manifest.json"))
    assert man["app"] == "nasoz" and man["type"] == "manual" and man["counts"]["db"]["users"] == 3
    assert all(len(v["sha256"]) == 64 for v in man["files"].values()) and man["commit"]
    print("create OK", z.name)

    # 2) validation
    assert bk.validate(z)[0]
    def rezip(dst: Path, mutate):
        with zipfile.ZipFile(z) as a, zipfile.ZipFile(dst, "w") as b:
            for n in a.namelist():
                data = a.read(n)
                r = mutate(n, data)
                if r is not None:
                    b.writestr(r[0], r[1])
        return dst
    bad_sha = rezip(tmp / "t1.zip", lambda n, d: (n, b"tampered") if n.startswith("uploads/") else (n, d))
    assert "sha256" in bk.validate(bad_sha)[1]
    trav = tmp / "t2.zip"
    with zipfile.ZipFile(trav, "w") as b:
        b.writestr("manifest.json", json.dumps({"app": "nasoz", "format": 1, "files": {}}))
        b.writestr("../../etc/evil", "x")
    assert "ناامن" in bk.validate(trav)[1]
    abs_zip = tmp / "t3.zip"
    with zipfile.ZipFile(abs_zip, "w") as b:
        b.writestr("manifest.json", "{}")
        b.writestr("/abs/evil", "x")
    assert bk.validate(abs_zip)[0] is None
    foreign = tmp / "t4.zip"
    with zipfile.ZipFile(foreign, "w") as b:
        b.writestr("readme.txt", "hi")
    assert "manifest" in bk.validate(foreign)[1]
    other_app = rezip(tmp / "t5.zip", lambda n, d: (n, json.dumps({**man, "app": "other"}).encode()) if n == "manifest.json" else (n, d))
    assert bk.validate(other_app)[0] is None
    notzip = tmp / "t6.zip"
    notzip.write_bytes(b"not a zip")
    assert "ZIP" in bk.validate(notzip)[1]
    extra = rezip(tmp / "t7.zip", lambda n, d: (n, d))
    with zipfile.ZipFile(extra, "a") as b:
        b.writestr("uploads/unlisted.txt", "x")
    assert "manifest" in bk.validate(extra)[1]
    ok, err, pre = bk.restore(trav, paths=paths)
    assert not ok and pre is None  # rejected before any change, no pre-restore needed
    print("validate OK")

    # 3) restore round-trip on temp copies
    db.upsert_user("799", "technician", display_name="بعد از بک‌آپ")
    src_file.unlink()
    (paths.uploads / "junk.txt").write_text("new")
    n_before = len(bk.list_backups(paths))
    ok, text, pre = bk.restore(z, paths=paths, actor="701")
    assert ok, text
    assert pre and pre.name.endswith("_pre-restore.zip") and len(bk.list_backups(paths)) == n_before + 1
    db2 = Database(paths.db)
    assert db2.get_user("799") is None and db2.get_user("701")
    assert src_file.read_bytes() == b"PK-fake-excel-main-source" and not (paths.uploads / "junk.txt").exists()
    with zipfile.ZipFile(pre) as zf:  # pre-restore holds the state just before
        assert "uploads/junk.txt" in zf.namelist()
    print("restore OK")

    # 4) retention
    rdir = tmp / "ret"
    rp = bk.Paths(db=paths.db, uploads=paths.uploads, assets=paths.assets, backups=rdir, env=paths.env)
    rdir.mkdir()
    now = time.time()
    def fake(name, days):
        p = rdir / name
        p.write_bytes(b"x")
        os.utime(p, (now - days * 86400, now - days * 86400))
    for d in range(40):  # one auto per day for 40 days
        fake(f"nasoz_backup_1405-06-{(d % 28) + 1:02d}_02{d:02d}.zip", d)
    fake("nasoz_backup_1405-07-10_1200_manual.zip", 10)
    fake("nasoz_backup_1405-06-01_1200_manual.zip", 45)
    fake("nasoz_backup_1405-07-12_1300_pre-restore.zip", 20)
    fake("nasoz_backup_1405-05-01_1300_pre-restore.zip", 31)
    fake("unrelated.zip", 100)
    removed = set(bk.prune(rp, now=now))
    left = {b["name"]: b for b in bk.list_backups(rp)}
    autos = [b for b in left.values() if b["kind"] == bk.AUTO]
    assert 7 <= len(autos) <= 11, len(autos)  # 7 daily + up to 4 weekly
    assert all(now - b["mtime"] < 7 * 86400 * 5 for b in autos)
    assert "nasoz_backup_1405-07-10_1200_manual.zip" in left and "nasoz_backup_1405-07-12_1300_pre-restore.zip" in left
    assert "nasoz_backup_1405-06-01_1200_manual.zip" in removed and "nasoz_backup_1405-05-01_1300_pre-restore.zip" in removed
    assert (rdir / "unrelated.zip").exists()
    print("retention OK", len(autos), "autos kept")

    # 5) nightly job (02:00 Tehran, once a day, catch-up, failure notice)
    from bot.jalali import TEHRAN

    class FC:
        def __init__(self):
            self.msgs, self.docs = [], []
        def send_message(self, chat_id, text, reply_markup=None, **_k):
            self.msgs.append((str(chat_id), text, reply_markup)); return {"ok": True}
        def send_document(self, chat_id, path, caption=None):
            self.docs.append((str(chat_id), Path(path).name, caption)); return {"ok": True}
        def download_file(self, file_id, dest):
            import shutil
            shutil.copy2(file_id, dest); return Path(dest)
        def __getattr__(self, n):
            return lambda *a, **k: {"ok": True}
    fc = FC()
    # OWNER-ONLY hard lock (1405/07/26): even a DB override written straight into
    # role_permissions (bypassing the UI) must not open backups for a manager/officer.
    db2.upsert_user("704", "responsible_officer", display_name="کاردان")
    perm.bind(db2)
    db2.set_role_permission("manager", perm.BACKUP, True, updated_by="smoke")
    db2.set_role_permission("responsible_officer", perm.BACKUP, True, updated_by="smoke")
    perm.invalidate()
    for role in ("manager", "responsible_officer", "technician", "observer"):
        assert perm.BACKUP not in perm.role_features(role), role
    assert perm.BACKUP in perm.role_features("owner")
    assert perm.BACKUP not in perm.TOGGLABLE_FEATURES and perm.BACKUP in perm.LOCKED_OWNER_ONLY
    ok, why = perm.set_permission(db2, db2.get_user("701"), "manager", perm.BACKUP, True)
    assert not ok and "قابل تغییر نیست" in why, why
    assert all(r["feature"] != perm.BACKUP for r in perm.matrix_rows("manager"))
    assert "بک‌آپ (پشتیبان‌گیری): مالک (فقط مالک (قفل))" in perm.matrix_text_fa(), perm.matrix_text_fa()
    assert [u["bale_user_id"] for u in bk.owner_recipients(db2)] == ["701"]
    assert bk.nightly_tick(fc, db2, paths=paths, now=datetime(2026, 10, 17, 1, 30, tzinfo=TEHRAN)) is None
    name = bk.nightly_tick(fc, db2, paths=paths, now=datetime(2026, 10, 17, 2, 0, 30, tzinfo=TEHRAN))
    assert name and bk.kind_of(paths.backups / name) == bk.AUTO and fc.docs and fc.docs[-1][0] == "701"
    assert {d[0] for d in fc.docs} == {"701"}, fc.docs  # nightly file → owner only
    assert bk.nightly_tick(fc, db2, paths=paths, now=datetime(2026, 10, 17, 9, 0, tzinfo=TEHRAN)) is None  # once a day
    assert "✅" in db2.get_setting(bk.S_LAST_AUTO_STATUS) and "آخرین پشتیبان خودکار" in bk.last_auto_status_fa(db2)
    broken = bk.Paths(db=tmp / "missing" / "x.db", uploads=paths.uploads, assets=paths.assets, backups=paths.backups, env=paths.env)
    assert bk.nightly_tick(fc, db2, paths=broken, now=datetime(2026, 10, 18, 3, 0, tzinfo=TEHRAN)) is None
    assert "❌" in db2.get_setting(bk.S_LAST_AUTO_STATUS) and any("ناموفق" in m[1] for m in fc.msgs)
    assert {m[0] for m in fc.msgs} == {"701"}, fc.msgs  # failure notice → owner only
    assert bk.nightly_tick(fc, db2, paths=paths, now=datetime(2026, 10, 18, 3, 10, tzinfo=TEHRAN)) is None  # 30-min backoff
    assert bk.nightly_tick(fc, db2, paths=paths, now=datetime(2026, 10, 18, 3, 45, tzinfo=TEHRAN))  # retried OK
    big = bk.BALE_MAX_UPLOAD
    bk.BALE_MAX_UPLOAD = 1
    assert bk.send_to_chat(fc, "701", z, caption="x") is False and "/settings/backups" in fc.msgs[-1][1]
    bk.BALE_MAX_UPLOAD = big
    print("nightly OK")

    # 6) bot: permissions + two-step owner restore
    from bot import handlers as hm
    from bot import keyboards as kb

    fc = FC()
    app = hm.BotApp(fc, db2)
    def msg(uid, text="", **kw):
        return {"chat": {"id": int(uid)}, "from": {"id": int(uid), "first_name": "x"}, "text": text, **kw}
    assert kb.BTN_BACKUP in kb.keyboard_texts(kb.bot_settings_menu(db2.get_user("701")))
    for uid in ("702", "703", "704"):  # manager (even with the DB override), technician, officer
        assert kb.BTN_BACKUP not in kb.keyboard_texts(kb.bot_settings_menu(db2.get_user(uid))), uid
    assert kb.BTN_BACKUP_RESTORE in kb.keyboard_texts(kb.backup_menu(db2.get_user("701")))
    for uid in ("702", "703", "704"):
        for btn in (kb.BTN_BACKUP, kb.BTN_BACKUP_CREATE, kb.BTN_BACKUP_LIST, kb.BTN_BACKUP_RESTORE):
            app.handle_message(msg(uid, btn))
            assert "فقط برای «مالک»" in fc.msgs[-1][1], (uid, btn, fc.msgs[-1][1])
    assert not fc.docs, fc.docs
    app.handle_message(msg(701, kb.BTN_BACKUP_CREATE))
    assert fc.docs and fc.docs[-1][1].endswith("_manual.zip") and fc.docs[-1][0] == "701", fc.docs
    app.handle_message(msg(701, kb.BTN_BACKUP_LIST))
    lst = [m for m in fc.msgs if m[2] and "inline_keyboard" in (m[2] or {})][-1]
    cb = lst[2]["inline_keyboard"][0][0]["callback_data"]
    app.handle_callback_query({"id": "1", "data": cb, "from": {"id": 701}, "message": {"chat": {"id": 701}}})
    assert len(fc.docs) == 2
    # a manager replaying the owner's ⬇️ callback gets nothing
    app.handle_callback_query({"id": "1b", "data": cb, "from": {"id": 702}, "message": {"chat": {"id": 702}}})
    assert len(fc.docs) == 2 and {d[0] for d in fc.docs} == {"701"}
    # owner restore: send zip → summary → r1 → r2
    db2.upsert_user("798", "technician", display_name="موقت")
    app.handle_message(msg(701, kb.BTN_BACKUP_RESTORE))
    app.handle_message(msg(701, document={"file_id": str(notzip), "file_name": "x.zip"}))
    assert "رد شد" in fc.msgs[-1][1]
    app.handle_message(msg(701, kb.BTN_BACKUP_RESTORE))
    app.handle_message(msg(701, document={"file_id": str(z), "file_name": z.name}))
    assert "ادامه می‌دهید" in fc.msgs[-1][1]
    app.handle_callback_query({"id": "2", "data": "bk|r2", "from": {"id": 701}, "message": {"chat": {"id": 701}}})
    assert db2.get_user("798")  # r2 before r1 → ignored
    app.handle_callback_query({"id": "3", "data": "bk|r1", "from": {"id": 701}, "message": {"chat": {"id": 701}}})
    assert "تأیید نهایی" in fc.msgs[-1][1]
    app.handle_callback_query({"id": "4", "data": "bk|r2", "from": {"id": 701}, "message": {"chat": {"id": 701}}})
    assert "✅ بازگردانی انجام شد" in fc.msgs[-1][1], fc.msgs[-1][1]
    assert Database(paths.db).get_user("798") is None
    print("bot OK")

    # 7) web: pages, download, owner-only restore (two confirmations)
    from fastapi.testclient import TestClient

    from web.app import create_app
    from web.auth_web import set_credential
    from web.deps import get_db

    db3 = Database(paths.db)
    perm.bind(db3)
    wapp = create_app()
    wapp.dependency_overrides[get_db] = lambda: db3
    for uid, role in (("701", "owner"), ("702", "manager"), ("703", "technician")):
        db3.upsert_user(uid, role, display_name=role)
        set_credential(db3, bale_user_id=uid, username=f"u{uid}", password="secret1234")
    def login(uid):
        c = TestClient(wapp)
        assert c.post("/login", data={"username": f"u{uid}", "password": "secret1234"}, follow_redirects=False).status_code in (302, 303)
        return c
    own, mgr, tech = login("701"), login("702"), login("703")
    db3.set_role_permission("manager", perm.BACKUP, True, updated_by="smoke")  # must stay ignored
    perm.invalidate()
    assert tech.get("/settings/backups", follow_redirects=False).status_code in (302, 303, 403)
    nm = bk.list_backups()[0]["name"]
    for c in (mgr, tech):
        for r in (c.get("/settings/backups", follow_redirects=False),
                  c.post("/settings/backups/create", follow_redirects=False),
                  c.get(f"/settings/backups/file/{nm}", follow_redirects=False)):
            assert r.status_code in (302, 303, 403) and r.content[:2] != b"PK", r.status_code
            if r.status_code == 403:
                assert "فقط برای «مالک»" in r.text, r.text[-400:]
    assert 'href="/settings/backups"' not in mgr.get("/home").text  # nav link hidden
    assert 'href="/settings/backups"' in own.get("/home").text
    r = own.post("/settings/backups/create")
    assert r.status_code == 200 and "_manual.zip" in r.text
    nm = bk.list_backups()[0]["name"]
    d = own.get(f"/settings/backups/file/{nm}")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    assert own.get("/settings/backups/file/..%2F..%2Fbot.db").status_code == 404
    assert "♻️ بازگردانی" in own.get("/settings/backups").text
    assert mgr.post("/settings/backups/restore/upload", files={"file": ("z.zip", z.read_bytes(), "application/zip")},
                    follow_redirects=False).status_code in (302, 303, 403)
    r = own.post("/settings/backups/restore/upload", files={"file": ("t.zip", bad_sha.read_bytes(), "application/zip")})
    assert r.status_code == 400 and "sha256" in r.text
    db3.upsert_user("797", "technician", display_name="وب موقت")
    r = own.post("/settings/backups/restore/upload", files={"file": ("z.zip", z.read_bytes(), "application/zip")})
    assert r.status_code == 200 and "بازگردانی" in r.text and "confirm2" in r.text
    r = own.post("/settings/backups/restore/confirm", data={"confirm1": "1", "confirm2": "نه"})
    assert r.status_code == 400 and Database(paths.db).get_user("797")
    r = own.post("/settings/backups/restore/confirm", data={"confirm1": "1", "confirm2": "بازگردانی"})
    assert r.status_code == 200 and "✅ بازگردانی انجام شد" in r.text, r.text[-500:]
    assert Database(paths.db).get_user("797") is None
    print("web OK")

    live_after = set(os.listdir(live_backups)) if live_backups.is_dir() else set()
    assert live_after == live_before, "live data/backups changed!"
    print("smoke_backup OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
