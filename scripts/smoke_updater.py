"""Updater smoke (GitHub Releases mocked — no network), apply/rollback in a temp project tree.

Bot notice and web /about: product facts + version; update controls and install are owner-only
(manager text, forged callback, and POST are denied). User-visible text has no repo address.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import hashlib
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
TMP = _smoke_env.SMOKE_TMP / "updater"
TMP.mkdir(parents=True, exist_ok=True)
REPO_API = "https://api.github.com/repos/silent4time/special-materials-robot-project/releases/latest"


def _zip(version: str, prefix: str, extra: dict | None = None) -> bytes:
    files = {"VERSION": version + "\n", "main.py": f"print('v{version}')\n", "services/new_mod.py": "X = 1\n",
             ".env": "BALE_BOT_TOKEN=FROM_RELEASE\n", "data/bot.db": "junk", "scripts/keep.py": "k = 2\n"}
    files.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, c in files.items():
            z.writestr(prefix + n, c)
    return buf.getvalue()


def _project(name: str) -> Path:
    import sqlite3

    root = TMP / name
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "VERSION").write_text("1.5.0\n")
    (root / "main.py").write_text("print('old')\n")
    (root / ".env").write_text("BALE_BOT_TOKEN=SECRET_LOCAL\n")
    (root / "user_local.txt").write_text("mine\n")
    (root / "scripts" / "keep.py").write_text("k = 1\n")
    c = sqlite3.connect(root / "data" / "bot.db")
    c.execute("CREATE TABLE IF NOT EXISTS t(x)")
    c.execute("INSERT INTO t VALUES (42)")
    c.commit()
    c.close()
    return root


class FakeGH:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.routes: dict[str, tuple[int, bytes]] = {}

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if url not in self.routes:
            return 404, b""
        return self.routes[url]

    def release(self, version, *, asset=True, sha=True, zipbytes=None, bad_sha=False, body="## تغییرات\n- **مورد** یک"):
        zb = zipbytes or _zip(version, "special-materials-robot-project/")
        assets = []
        if asset:
            assets.append({"name": "nasoz-bot.zip", "browser_download_url": "https://dl/nasoz-bot.zip"})
            self.routes["https://dl/nasoz-bot.zip"] = (200, zb)
        if sha:
            digest = "0" * 64 if bad_sha else hashlib.sha256(zb).hexdigest()
            assets.append({"name": "nasoz-bot.zip.sha256", "browser_download_url": "https://dl/nasoz-bot.zip.sha256"})
            self.routes["https://dl/nasoz-bot.zip.sha256"] = (200, f"{digest}  nasoz-bot.zip\n".encode())
        if not asset:
            self.routes["https://api.github.com/zipball/x"] = (200, _zip(version, "silent4time-special-materials-robot-project-abc123/"))
        rel = {"tag_name": "v" + version, "assets": assets, "zipball_url": "https://api.github.com/zipball/x",
               "html_url": "https://github.com/r/releases/tag/v" + version, "body": body, "published_at": "2026-11-02T10:00:00Z"}
        self.routes[REPO_API] = (200, json.dumps(rel).encode())


def _count_status_phrases(text: str) -> int:
    stems = (
        "⏳ در حال به‌روزرسانی",
        "✅ به‌روزرسانی انجام شد",
        "✅ آخرین نسخه نصب است",
        "آمادهٔ نصب است",
        "✅ نسخهٔ جدیدتری نیست",
    )
    return sum(1 for stem in stems if stem in text)



def test_status_texts(db, client, who) -> None:
    """One status line: in-progress, done, latest, available; never a stale «انجام شد»."""
    from services import updater as up

    cur = up.current_version()

    def body(info, *, checked=True):
        return up.about_text(info, owner=True, checked=checked)

    latest = body({
        "current": cur, "latest": cur, "available": False, "error": "",
        "status": {}, "remote": {}, "in_progress": False,
    })
    assert up.INSTALLED_FA.format(version=cur) in latest, latest
    assert "نسخهٔ جدیدتری نیست" not in latest
    assert latest.count("وضعیت به‌روزرسانی:") == 1
    assert _count_status_phrases(latest) == 1, latest

    done = body({
        "current": "1.7.17", "latest": "1.7.17", "available": False, "error": "",
        "status": {"state": "applied", "version": "1.7.17", "at": "t", "message": "از 1.7.16 به 1.7.17."},
        "remote": {}, "in_progress": False,
    })
    assert up.DONE_STATUS_FA.format(version="1.7.17") in done, done
    assert "در حال به‌روزرسانی" not in done
    assert "آخرین نسخه نصب است" not in done
    assert done.count("وضعیت به‌روزرسانی:") == 1
    assert _count_status_phrases(done) == 1, done

    avail = body({
        "current": "1.7.16", "latest": "1.7.17", "available": True, "error": "",
        "status": {"state": "applied", "version": "1.7.16", "at": "t",
                   "message": "از 1.7.14 به 1.7.16."},
        "remote": {"changelog": "تغییرات تازه"},
        "in_progress": False,
    })
    assert up.AVAILABLE_STATUS_FA.format(version="1.7.17") in avail, avail
    assert "به‌روزرسانی انجام شد" not in avail
    assert "از 1.7.14 به 1.7.16" not in avail
    assert "تغییرات تازه" in avail
    assert avail.count("وضعیت به‌روزرسانی:") == 1
    assert _count_status_phrases(avail) == 1, avail

    # Stale previous success must not win over a live lock / in-progress flag.
    busy = body({
        "current": "1.7.16", "latest": "1.7.17", "available": True, "error": "",
        "status": {"state": "applied", "version": "1.7.16", "at": "t",
                   "message": "از 1.7.14 به 1.7.16."},
        "remote": {"changelog": "تغییرات 1.7.17"},
        "in_progress": True, "progress_target": "1.7.17",
    })
    assert up.RUNNING_STATUS_FA.format(version="1.7.17") in busy, busy
    assert "به‌روزرسانی انجام شد" not in busy
    assert "آخرین نسخه نصب است" not in busy
    assert "از 1.7.14 به 1.7.16" not in busy
    assert busy.count("وضعیت به‌روزرسانی:") == 1
    assert _count_status_phrases(busy) == 1, busy

    assert up.SUCCESS_FA.format(version="1.7.17").startswith("✅ به‌روزرسانی انجام شد")

    # Web /about: one status, no flash duplicate of «آخرین نسخه نصب است»
    who["u"] = db.get_user("61")
    up.release_update_lock(force=True)
    up.clear_progress_state()
    db.set_setting(up.STATUS_KEY, "{}")
    db.set_setting(up.CACHE_KEY, json.dumps({
        "checked_at": time.time(), "ttl": 3600,
        "info": {"version": cur, "changelog": "", "html_url": ""},
    }, ensure_ascii=False))
    r = client.get("/about")
    assert r.status_code == 200, r.status_code
    page = r.text
    assert up.INSTALLED_FA.format(version=cur) in page, page
    assert up.NO_UPDATE_FA.format(current=cur) not in page
    assert page.count("<th>وضعیت به‌روزرسانی</th>") == 1
    print("  status texts: in-progress / done / latest / available, no duplicate OK")


def main() -> int:
    # The 1.7.1 updater holds data/updates/update.lock (its own pid) while it
    # runs this file. Never touch that directory: a second "start update" in
    # this smoke would see the live lock and the test would delete it.
    scratch = TMP / "updates-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["NASOZ_UPDATES_DIR"] = str(scratch)

    import config
    from db.models import Database
    from services import updater as up

    live_lock = config.BASE_DIR / "data" / "updates" / "update.lock"
    live_lock.parent.mkdir(parents=True, exist_ok=True)
    prev_lock = live_lock.read_bytes() if live_lock.exists() else None
    live_body = str(os.getpid())
    live_lock.write_text(live_body, encoding="utf-8")

    def _restore_live_lock() -> None:
        if prev_lock is None:
            if live_lock.exists() and live_lock.read_text(encoding="utf-8") == live_body:
                live_lock.unlink()
        else:
            live_lock.write_bytes(prev_lock)

    import atexit
    atexit.register(_restore_live_lock)
    assert up._lock_blocks(config.BASE_DIR), "live pid lock must look held to the real install"
    assert not up.update_in_progress(), "smoke must ignore the updater's own lock"

    # semver (Updater.php: strip v, version_compare)
    assert up.is_newer("1.6.0", "1.5.0") and up.is_newer("v1.10.0", "1.9.9") and up.is_newer("2.0.0", "1.99.99")
    assert not up.is_newer("1.5.0", "1.5.0") and not up.is_newer("1.4.9", "1.5.0") and not up.is_newer("", "1.0.0")
    assert up.is_newer("1.6.0", "1.6.0-rc.1") and not up.is_newer("1.6.0-rc.1", "1.6.0")
    print("  semver OK")

    gh = FakeGH()
    up._http_get = gh
    db = Database(TMP / "u.db")
    # cache: 6h on success, 1h after failure; token only as header, never in errors
    config.UPDATE_GITHUB_TOKEN = ""
    info = up.get_remote(db)
    assert info["version"] == "" and info["error"] and len(gh.calls) == 1
    assert json.loads(db.get_setting(up.CACHE_KEY))["ttl"] == up.TTL_FAIL
    up.get_remote(db)
    assert len(gh.calls) == 1, "failure must be cached (1h)"
    gh.release("1.6.0")
    info = up.get_remote(db, force=True)
    assert info["version"] == "1.6.0" and info["package_is_asset"] and info["sha256_url"], info
    assert json.loads(db.get_setting(up.CACHE_KEY))["ttl"] == up.TTL_OK
    n = len(gh.calls)
    up.get_remote(db)
    assert len(gh.calls) == n, "success must be cached (6h)"
    import time as _t
    up.get_remote(db, now=_t.time() + up.TTL_OK + 5)
    assert len(gh.calls) == n + 1, "cache must expire after 6h"
    assert "Authorization" not in gh.calls[-1][1] and gh.calls[-1][1]["Accept"] == "application/vnd.github+json"
    config.UPDATE_GITHUB_TOKEN = "ghp_SECRETTOKEN"
    up.get_remote(db, force=True)
    assert gh.calls[-1][1]["Authorization"] == "Bearer ghp_SECRETTOKEN"
    assert "SECRETTOKEN" not in up._safe_err(RuntimeError("bad ghp_SECRETTOKEN"))
    config.UPDATE_GITHUB_TOKEN = ""
    assert up.notify_text(up.update_info(db, network=False) | {"current": "1.5.0", "latest": "1.6.0"}).count("1.6.0") >= 1
    print("  GitHub API (mocked), 6h/1h cache, token header-only OK")

    restarts: list[str] = []
    rs = lambda log: restarts.append("r") or "test"

    # apply OK: asset + checksum; .env/data/user files preserved; DB backed up
    root = _project("ok")
    res = up.apply_update(db, root=root, db_path=root / "data" / "bot.db", smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "applied", res
    assert (root / "VERSION").read_text().strip() == "1.6.0" and (root / "services" / "new_mod.py").exists()
    assert (root / ".env").read_text() == "BALE_BOT_TOKEN=SECRET_LOCAL\n", ".env must be preserved"
    assert (root / "user_local.txt").exists() and (root / "scripts" / "keep.py").read_text() == "k = 2\n"
    import sqlite3
    assert sqlite3.connect(root / "data" / "bot.db").execute("SELECT x FROM t").fetchone() == (42,), "data/ untouched"
    bks = list((root / "data" / "backups_manual").glob("bot_pre_v1.6.0_*.db"))
    assert bks and sqlite3.connect(bks[0]).execute("SELECT x FROM t").fetchone() == (42,)
    assert restarts == ["r"] and up.get_status(db)["state"] == "applied"
    assert not (root / "data" / "updates" / "update.lock").exists()
    print("  apply (sha256 verified, DB+code backup, .env/data/user files kept, restart) OK")

    # checksum mismatch → nothing touched
    gh.release("1.6.0", bad_sha=True)
    root = _project("badsha")
    res = up.apply_update(db, root=root, db_path=root / "data" / "bot.db", smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "failed" and "چک‌سام" in res["message"], res
    assert (root / "VERSION").read_text().strip() == "1.5.0" and len(restarts) == 1
    # asset without checksum file → refused
    gh.release("1.6.0", sha=False)
    res = up.apply_update(db, root=_project("nosha"), smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "failed" and "sha256" in res["message"], res
    print("  checksum mismatch / missing → refused, unchanged OK")

    # smoke fails → automatic rollback of THIS update's snapshot, not an older one
    gh.release("1.6.0")
    root = _project("rollback")
    old_snap = root / "data" / "updates" / "rollback_0.0.1_20000101_000000"
    old_snap.mkdir(parents=True)
    (old_snap / "VERSION").write_text("0.0.1\n", encoding="utf-8")
    res = up.apply_update(db, root=root, db_path=root / "data" / "bot.db", smoke=lambda r, log: False, restart=rs, pip=False)
    assert res["state"] == "rolled_back", res
    assert (root / "VERSION").read_text().strip() == "1.5.0" and (root / "main.py").read_text() == "print('old')\n"
    assert (old_snap / "VERSION").read_text().strip() == "0.0.1", "older rollback dir must stay untouched"
    assert not (root / "services" / "new_mod.py").exists() and (root / "scripts" / "keep.py").read_text() == "k = 1\n"
    assert len(restarts) == 1 and up.get_status(db)["state"] == "rolled_back"
    assert "smoke.log" in res["message"]
    print("  failing smoke → rollback (changed restored, added removed, no restart) OK")

    # zipball fallback (owner-repo-sha/ top folder stripped); version mismatch refused
    gh.release("1.6.0", asset=False, sha=False)
    root = _project("zipball")
    res = up.apply_update(db, root=root, smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "applied" and (root / "VERSION").read_text().strip() == "1.6.0", res
    gh.release("1.7.0", zipbytes=_zip("1.6.9", "p/"))
    res = up.apply_update(db, root=_project("mismatch"), smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "failed" and "یکی نیست" in res["message"], res
    # not newer → no-op
    gh.release("1.5.0")
    res = up.apply_update(db, root=_project("same"), smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "failed" and "جدیدتری" in res["message"]
    print("  zipball fallback, version mismatch, no-newer OK")

    # ---- bot: notice once to the owner; manager cannot invoke, even with a forged callback
    import smoke_phase1 as p1
    from bot import keyboards as kb
    from bot.handlers import BotApp

    gh.release(
        "99.0.0",
        body="## تغییرات\n- قابلیت تازه\nhttps://github.com/silent4time/special-materials-robot-project/releases/tag/v99.0.0",
    )
    leaked = up.about_text(
        {"current": "1.0.0", "latest": "9.0.0", "available": True, "error": "", "status": {},
         "remote": {"changelog": "see https://github.com/silent4time/special-materials-robot-project",
                    "html_url": "https://github.com/hidden/repo"}},
        owner=True, checked=True,
    )
    assert "github.com" not in leaked and "special-materials-robot-project" not in leaked, leaked
    quiet = up.about_text(
        {"current": up.current_version(), "latest": "", "available": False, "error": "", "status": {}, "remote": {}},
        owner=True, checked=True,
    )
    assert up.INSTALLED_FA.format(version=up.current_version()) in quiet and "github.com" not in quiet
    assert "نسخهٔ جدیدتری نیست" not in quiet
    assert quiet.count("وضعیت به‌روزرسانی") == 1
    assert up.creator_name() == "واحد نسوز تاندیش فولاد خوزستان"
    assert "واحد نسوز تاندیش فولاد خوزستان" in quiet and "ثبت نشده است" not in quiet
    assert "تاندیش اسلب" in quiet and up.current_version() in quiet
    assert "github.com" not in up.status_fa(
        {"state": "failed", "version": "1", "at": "t", "message": "see https://github.com/x/y token ghp_SECRET"}
    )
    db2 = Database(TMP / "bot.db")
    for uid, role in (("61", "owner"), ("62", "manager"), ("63", "technician")):
        db2.upsert_user(uid, role=role, display_name=role)
    client = p1.FakeClient()
    assert up.notify_tick(client, db2, kb.update_menu()) == "notified"
    to = sorted(c for c, _t, _m in client.sent)
    assert to == ["61"], to
    assert "99.0.0" in client.sent[0][1] and "قابلیت تازه" in client.sent[0][1]
    assert "github.com" not in client.sent[0][1] and "special-materials-robot-project" not in client.sent[0][1]
    assert kb.BTN_UPDATE in kb.keyboard_texts(client.sent[0][2])
    assert up.notify_tick(client, db2, kb.update_menu()) is None and len(client.sent) == 1, "notify once per version"

    launched: list[str] = []
    up.launch_detached = lambda by="", lock_token="": launched.append(by) or "test"
    app = BotApp(client, db2)  # type: ignore[arg-type]

    def say(uid, t):
        app.handle_update({"message": {"from": {"id": int(uid), "first_name": "a"}, "chat": {"id": int(uid)}, "text": t}})

    def forged(uid, data):
        app.handle_callback_query({
            "id": "cq-" + data, "data": data,
            "from": {"id": int(uid), "first_name": "a"},
            "message": {"chat": {"id": int(uid)}, "message_id": 1},
        })

    assert kb.BTN_ABOUT in kb.keyboard_texts(kb.bot_settings_menu(db2.get_user("62")))
    assert kb.BTN_UPDATE not in kb.keyboard_texts(kb.bot_settings_menu(db2.get_user("62")))
    say("62", kb.BTN_ABOUT)
    mgr = client.sent[-1][1]
    assert up.current_version() in mgr and "واحد نسوز تاندیش فولاد خوزستان" in mgr and "تاندیش اسلب" in mgr, mgr
    assert "ثبت نشده است" not in mgr
    assert "99.0.0" not in mgr and kb.BTN_UPDATE not in kb.keyboard_texts(client.sent[-1][2])
    assert "github.com" not in mgr
    say("62", kb.BTN_UPDATE)
    say("62", kb.BTN_UPDATE_CHECK)
    say("62", kb.BTN_UPDATE_CONFIRM)  # old label, still not an install
    forged("62", kb.CB_UPDATE_APPLY)
    forged("62", kb.CB_UPDATE_CHECK)
    forged("62", "upd|apply")
    forged("63", kb.CB_UPDATE_APPLY)
    assert launched == [], launched
    assert any("مالک" in m[1] for m in client.sent[-6:]), client.sent[-3:]
    say("61", kb.BTN_UPDATE_CHECK)
    assert "99.0.0" in client.sent[-1][1] and up.current_version() in client.sent[-1][1], client.sent[-1][1]
    assert "github.com" not in client.sent[-1][1]
    assert kb.BTN_UPDATE in kb.keyboard_texts(client.sent[-1][2])
    say("61", kb.BTN_UPDATE)
    prog = client.sent[-1][1]
    assert launched == ["61"] and "در حال دانلود" in prog and "▰" in prog and "99.0.0" in prog, client.sent[-1]
    assert kb.BTN_UPDATE_WAIT in kb.keyboard_texts(client.sent[-1][2]), client.sent[-1][2]
    saved = json.loads((scratch / "update_progress.json").read_text(encoding="utf-8"))
    assert saved["target_version"] == "99.0.0" and str(saved["chat_id"]) == "61" and saved["message_id"]
    say("61", kb.BTN_ANALYTICS)
    assert launched == ["61"] and up.BUSY_FA in client.sent[-1][1], client.sent[-1]
    assert kb.BTN_HOME not in kb.keyboard_texts(client.sent[-1][2])
    say("61", kb.BTN_UPDATE)
    assert launched == ["61"] and up.BUSY_FA in client.sent[-1][1], client.sent[-1]
    cleared: list = []
    answers: list = []
    client.edit_message_reply_markup = lambda chat, mid, reply_markup=None: cleared.append(reply_markup) or {}
    client.answer_callback_query = lambda cid, text=None, show_alert=False: answers.append((text, bool(show_alert))) or True
    forged("61", kb.CB_UPDATE_APPLY)
    assert cleared[-1] == {"inline_keyboard": []} and answers[-1] == (up.BUSY_FA, True)
    assert launched == ["61"]
    up.release_update_lock(force=True)
    up.clear_progress_state()
    forged("61", kb.CB_UPDATE_CONFIRM)
    assert launched == ["61", "61"], launched
    assert cleared[-1] == {"inline_keyboard": []} and answers[-1][0] in (None, "")
    up.release_update_lock(force=True)
    up.clear_progress_state()
    forged("61", "upd|nope")
    assert launched == ["61", "61"]
    # finished update reported once, and only to the owner
    up._set_status(db2, state="applied", version="99.0.0", previous="1.6.0", message="ok https://github.com/x", reported=False)
    n = len(client.sent)
    up.notify_tick(client, db2)
    assert len(client.sent) == n + 1 and "انجام شد" in client.sent[-1][1]
    assert "github.com" not in client.sent[-1][1]
    up.notify_tick(client, db2)
    assert len(client.sent) == n + 1
    print("  bot: owner notice, about/check/install, manager+forged callback denied, no repo URL OK")

    # ---- web: /about + banner. Install/check are owner-only (same service as the bot).
    from fastapi.testclient import TestClient

    import web.deps as deps
    import web.routers.about as about
    from web.app import create_app

    about.up.launch_detached = up.launch_detached
    db2.set_setting(up.STATUS_KEY, "{}")
    wapp = create_app()
    wapp.dependency_overrides[deps.get_db] = lambda: db2
    about.get_db = lambda: db2
    who = {"u": db2.get_user("61")}
    wapp.dependency_overrides[deps.current_user_optional] = lambda: who["u"]
    c = TestClient(wapp)
    h = c.get("/home").text
    assert "update-banner" in h and "99.0.0" in h and "/about/update" in h and 'href="/about"' in h
    assert "به‌روزرسانی روی سرور" in h and "github.com" not in h
    h = c.get("/about").text
    assert "99.0.0" in h and up.current_version() in h and "قابلیت تازه" in h and "<li>" in h
    assert "بررسی به‌روزرسانی" in h and "به‌روزرسانی روی سرور" in h
    assert "واحد نسوز تاندیش فولاد خوزستان" in h and "ثبت نشده است" not in h and "تاندیش اسلب" in h
    assert "github.com" not in h and "special-materials-robot-project" not in h
    assert 'class="brand-logo"' in h and "/assets/logo.png" in h
    logo = c.get("/assets/logo.png")
    assert logo.status_code == 200 and logo.content.startswith(b"\x89PNG"), logo.status_code
    r = c.post("/about/update")
    assert r.status_code == 200 and launched == ["61", "61", "61"] and "شروع شد" in r.text
    assert "github.com" not in r.text
    assert up.RUNNING_STATUS_FA.format(version="99.0.0") in r.text
    # The status row must be the running line (changelog may mention «انجام شد»).
    assert "وضعیت به‌روزرسانی" in r.text and r.text.count("⏳ در حال به‌روزرسانی به نسخهٔ 99.0.0") == 1
    assert r.text.count("<th>وضعیت به‌روزرسانی</th>") == 1
    busy = c.post("/about/update")
    assert busy.status_code == 200 and up.BUSY_FA in busy.text and launched == ["61", "61", "61"]
    up.release_update_lock(force=True)
    up.clear_progress_state()
    who["u"] = db2.get_user("62")
    h = c.get("/home").text
    assert "update-banner" not in h and 'href="/about"' in h
    h = c.get("/about").text
    assert up.current_version() in h and "واحد نسوز تاندیش فولاد خوزستان" in h and "ثبت نشده است" not in h
    assert 'action="/about/check"' not in h and 'action="/about/update"' not in h
    assert "github.com" not in h
    assert c.post("/about/check", follow_redirects=False).status_code == 403
    assert c.post("/about/update", follow_redirects=False).status_code == 403
    assert launched == ["61", "61", "61"]
    who["u"] = db2.get_user("63")
    h = c.get("/home").text
    assert "update-banner" not in h and 'href="/about"' not in h
    assert c.get("/about", follow_redirects=False).status_code in (303, 403)
    assert c.post("/about/update", follow_redirects=False).status_code in (303, 403)
    assert c.post("/about/check", follow_redirects=False).status_code in (303, 403)
    assert launched == ["61", "61", "61"]
    print("  web: banner + /about owner-only install, manager POST denied, no repo URL OK")

    # progress bar, lock, stuck "running" status, final message after restart
    assert up.progress_bar(50) == "▰▰▰▱▱▱ 50%", up.progress_bar(50)
    assert up.progress_bar(0).startswith("▱") and up.progress_bar(100).count("▰") == 6
    half, detail = up.download_progress(50, 100)
    assert "50٪" in detail and 5 <= half <= 40, (half, detail)
    unk_pct, unk = up.download_progress(2048, 0)
    assert "٪" not in unk and "کیلوبایت" in unk and unk_pct == 8, (unk_pct, unk)
    stages: list[str] = []
    gh.release("1.6.0")
    root = _project("progress")
    res = up.apply_update(
        db, root=root, db_path=root / "data" / "bot.db",
        smoke=lambda r, log: True, restart=rs, pip=False,
        progress=lambda stage, pct, ver, det="": stages.append(stage),
    )
    assert res["state"] == "applied", res
    assert stages[:6] == ["download", "download", "verify", "backup", "install", "restart"], stages
    assert not (root / "data" / "updates" / "update.lock").exists()
    held = up.try_acquire_update_lock(root)
    assert held and up.try_acquire_update_lock(root) is None
    busy = up.apply_update(db, root=root, smoke=lambda r, log: True, restart=rs, pip=False)
    assert busy["state"] == "busy" and busy["message"] == up.BUSY_FA, busy
    up.release_update_lock(root, token=held)
    # stale lock (and a dead pid) must not block the next run
    root = _project("stale")
    stale = root / "data" / "updates" / "update.lock"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({"pid": 2**22, "started": 1, "token": "old", "role": "apply"}), encoding="utf-8")
    res = up.apply_update(db, root=root, db_path=root / "data" / "bot.db", smoke=lambda r, log: True, restart=rs, pip=False)
    assert res["state"] == "applied" and not stale.exists(), res
    gh.release("1.6.0", bad_sha=True)
    bad_stages: list[str] = []
    root = _project("badprog")
    up.write_progress_state(7, 3, "1.6.0", root=root)
    res = up.apply_update(
        db, root=root, smoke=lambda r, log: True, restart=rs, pip=False,
        progress=lambda stage, pct, ver, det="": bad_stages.append(stage),
    )
    assert res["state"] == "failed" and "error" in bad_stages, res
    assert not (root / "data" / "updates" / "update.lock").exists()
    assert up.read_progress_state(root) is None
    print("  progress bar, download %, lock + stale lock, failure releases lock OK")

    class _Chat:
        def __init__(self):
            self.edits: list[tuple] = []
            self.sent: list[tuple] = []

        def edit_message_text(self, chat, mid, text, reply_markup=None):
            self.edits.append((str(chat), int(mid), text))
            return {}

        def send_message(self, chat, text, reply_markup=None):
            self.sent.append((str(chat), text))
            return {"message_id": len(self.sent)}

    # production bug: status stayed "running" after VERSION already equals the target
    rec = TMP / "reconcile"
    (rec).mkdir(parents=True, exist_ok=True)
    (rec / "VERSION").write_text("1.7.3\n", encoding="utf-8")
    dbx = Database(TMP / "reconcile.db")
    dbx.upsert_user("61", role="owner", display_name="owner")
    old_at = "2020-01-01 00:00"
    up._set_status(dbx, state="running", version="1.7.3", previous="1.7.2", message="", started_epoch=time.time() - 8 * 60)
    # _set_status stamps `at` as now; rewrite the stored row to the stuck production timestamp
    raw = json.loads(dbx.get_setting(up.STATUS_KEY))
    raw["at"] = old_at
    dbx.set_setting(up.STATUS_KEY, json.dumps(raw, ensure_ascii=False))
    up.write_progress_state("61", 44, "1.7.3", root=rec, started=time.time() - 8 * 60)
    chat = _Chat()
    outcome = up.finalize_after_restart(chat, dbx, root=rec)
    assert outcome == "completed", outcome
    st = up.get_status(dbx)
    assert st["state"] == "applied" and st.get("at") != old_at, st
    assert "در حال به‌روزرسانی" not in up.status_fa(st), up.status_fa(st)
    assert chat.edits and chat.edits[-1][2] == up.SUCCESS_FA.format(version="1.7.3"), chat.edits
    assert chat.sent == [], "completion must not also be sent as a second message"
    assert up.get_status(dbx).get("reported") is True
    assert up.read_progress_state(rec) is None
    # a later finalize / notify_tick (bot restart, or updater + bot) must stay silent
    up.write_progress_state("61", 44, "1.7.3", root=rec, started=time.time())
    again = _Chat()
    up.finalize_after_restart(again, dbx, root=rec)
    assert again.edits == [] and again.sent == [], (again.edits, again.sent)
    assert up.notify_tick(again, dbx) is None and again.sent == []
    assert up.read_progress_state(rec) is None
    # edit failure falls back to a new message, then the state file is removed
    up.clear_result_notices(root=rec)
    up._set_status(dbx, state="running", version="1.7.3", previous="1.7.2", message="",
                   started_epoch=time.time(), reported=False)
    (rec / "VERSION").write_text("1.7.3\n", encoding="utf-8")
    up.write_progress_state("61", 44, "1.7.3", root=rec)

    class _NoEdit(_Chat):
        def edit_message_text(self, *a, **k):
            raise RuntimeError("message gone")

    chat = _NoEdit()
    up.finalize_after_restart(chat, dbx, root=rec)
    assert chat.sent and up.SUCCESS_FA.format(version="1.7.3") in chat.sent[-1][1]
    assert len(chat.sent) == 1, "fallback send must still be a single notice"
    assert up.read_progress_state(rec) is None
    # running version != target → failed, Persian message, lock released
    (rec / "VERSION").write_text("1.7.2\n", encoding="utf-8")
    up._set_status(dbx, state="running", version="1.7.3", previous="1.7.2", message="", started_epoch=time.time())
    up.write_progress_state("61", 9, "1.7.3", root=rec)
    (rec / "data" / "updates").mkdir(parents=True, exist_ok=True)
    (rec / "data" / "updates" / "update.lock").write_text(
        json.dumps({"pid": 2**22, "started": time.time(), "token": "dead", "role": "apply"}),
        encoding="utf-8",
    )
    chat = _Chat()
    assert up.finalize_after_restart(chat, dbx, root=rec) == "failed"
    assert up.get_status(dbx)["state"] == "failed"
    assert "کامل نشد" in chat.edits[-1][2] and "1.7.2" in chat.edits[-1][2]
    assert chat.sent == [], chat.sent
    assert not (rec / "data" / "updates" / "update.lock").exists()
    assert up.read_progress_state(rec) is None
    # older than 30 minutes and version still behind → stale/failed even if the pid looks alive
    (rec / "VERSION").write_text("1.7.2\n", encoding="utf-8")
    up._set_status(dbx, state="running", version="9.9.9", previous="1.7.2", message="", started_epoch=time.time() - 31 * 60)
    (rec / "data" / "updates" / "update.lock").write_text(
        json.dumps({"pid": os.getpid(), "started": time.time() - 31 * 60, "token": "live", "role": "apply"}),
        encoding="utf-8",
    )
    assert up.reconcile_running_status(dbx, root=rec) == "stale"
    assert up.get_status(dbx)["state"] == "failed" and "۳۰ دقیقه" in up.get_status(dbx)["message"]
    assert not (rec / "data" / "updates" / "update.lock").exists()
    # a young live lock with a different version is still in progress
    up._set_status(dbx, state="running", version="9.9.9", previous="1.7.2", message="", started_epoch=time.time())
    (rec / "data" / "updates" / "update.lock").write_text(
        json.dumps({"pid": os.getpid(), "started": time.time(), "token": "live", "role": "apply"}),
        encoding="utf-8",
    )
    assert up.reconcile_running_status(dbx, root=rec) is None
    assert up.get_status(dbx)["state"] == "running"
    (rec / "data" / "updates" / "update.lock").unlink()
    # finished install still labelled running, and no lock → About says the latest is installed
    (rec / "VERSION").write_text("1.7.11\n", encoding="utf-8")
    up._set_status(dbx, state="running", version="1.7.11", previous="1.7.10", message="")
    settled = up.settle_status(dbx, root=rec)
    assert settled["state"] == "applied" and settled["message"] == "آخرین نسخه نصب است", settled
    done = up.about_text(
        {"current": "1.7.11", "latest": "", "available": False, "error": "", "status": settled, "remote": {}},
        owner=True, checked=True,
    )
    assert "آخرین نسخه نصب است" in done and "در حال به‌روزرسانی" not in done, done
    up._set_status(dbx, state="running", version="1.7.11", previous="1.7.10", message="")
    (rec / "data" / "updates" / "update.lock").write_text(
        json.dumps({"pid": os.getpid(), "started": time.time(), "token": "live", "role": "apply"}),
        encoding="utf-8",
    )
    assert up.settle_status(dbx, root=rec)["state"] == "running"
    busy = up.about_text(
        {"current": "1.7.11", "latest": "", "available": False, "error": "",
         "status": up.get_status(dbx), "remote": {}},
        owner=True,
    )
    assert "در حال به‌روزرسانی" in busy, busy
    (rec / "data" / "updates" / "update.lock").unlink()
    print("  startup: version match completes, mismatch/stale fails, live update left running OK")

    from services import bot_logo

    calls: list = []

    class _Photo:
        def try_set_my_photo(self, path):
            calls.append(Path(path).name)
            return True, "setMyProfilePhoto"

    assert bot_logo.PROFILE_PHOTO_API_SUPPORTED is False
    skipped = bot_logo.sync_official_logo(_Photo())
    assert skipped["supported"] is False and skipped["applied"] is False and calls == []
    assert "setuserpic" in bot_logo.MANUAL_STEPS_FA and "@botfather" in bot_logo.MANUAL_STEPS_FA
    logo_root = TMP / "logo"
    (logo_root / "assets").mkdir(parents=True)
    mark = logo_root / "assets" / "logo.png"
    mark.write_bytes(b"\x89PNG\r\n\x1a\nlogo-a")
    bot_logo.PROFILE_PHOTO_API_SUPPORTED = True
    try:
        first = bot_logo.sync_official_logo(_Photo(), root=logo_root)
        assert first["applied"] is True and calls == ["logo.png"], first
        again = bot_logo.sync_official_logo(_Photo(), root=logo_root)
        assert again["reason"] == "unchanged" and calls == ["logo.png"], again
        mark.write_bytes(b"\x89PNG\r\n\x1a\nlogo-b")
        third = bot_logo.sync_official_logo(_Photo(), root=logo_root)
        assert third["applied"] is True and len(calls) == 2, third

        class _Boom:
            def try_set_my_photo(self, path):
                raise RuntimeError("api down")

        mark.write_bytes(b"\x89PNG\r\n\x1a\nlogo-c")
        failed = bot_logo.sync_official_logo(_Boom(), root=logo_root)
        assert failed["applied"] is False and failed["reason"] == "error"
    finally:
        bot_logo.PROFILE_PHOTO_API_SUPPORTED = False
    print("  logo: API unsupported (no call); hash gate is idempotent when enabled OK")

    # default_smoke names the failing script and writes its output; children
    # get NASOZ_UPDATES_DIR so they cannot delete the caller's lock.
    sm = TMP / "smokelog"
    (sm / "scripts").mkdir(parents=True)
    (sm / "scripts" / "smoke_ok.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (sm / "scripts" / "smoke_bad.py").write_text(
        "import sys\nprint('boom-detail')\nsys.exit(3)\n", encoding="utf-8",
    )
    sm_log: list[str] = []
    assert up.default_smoke(sm, sm_log.append) is False
    assert "FAIL smoke_bad.py" in sm_log and any(x.startswith("PASS ") for x in sm_log)
    assert up.default_smoke.last_failures == ["smoke_bad.py"]
    body = Path(up.default_smoke.last_log).read_text(encoding="utf-8")
    assert "boom-detail" in body and "smoke_bad.py" in body
    reason = up.smoke_failure_text(up.default_smoke, sm)
    assert "smoke_bad.py" in reason and "smoke.log" in reason
    # the live updater lock is still the one we planted (this script did not release it)
    assert live_lock.read_text(encoding="utf-8") == live_body
    print("  default_smoke: failed name + smoke.log; live updater lock left intact OK")

    # Parallel runner: finish order is not log order; progress stays in smoke_start…smoke_end.
    par = TMP / "smokepar"
    (par / "scripts").mkdir(parents=True)
    (par / "scripts" / "smoke_a.py").write_text("import time\ntime.sleep(0.6)\n", encoding="utf-8")
    (par / "scripts" / "smoke_b.py").write_text("import time\ntime.sleep(0.05)\n", encoding="utf-8")
    (par / "scripts" / "smoke_c.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    (par / "scripts" / "smoke_d.py").write_text("import time\ntime.sleep(0.2)\n", encoding="utf-8")
    par_log: list[str] = []
    ticks: list[tuple[str, int, str]] = []

    def _tick(stage, percent, ver, detail=""):
        ticks.append((stage, int(percent), detail))

    assert up.default_smoke(par, par_log.append, progress=_tick, version="1.7.7", workers=4) is False
    assert par_log == [
        "PASS smoke_a.py", "PASS smoke_b.py", "FAIL smoke_c.py", "PASS smoke_d.py",
    ], par_log
    assert up.default_smoke.last_failures == ["smoke_c.py"]
    start, end = up.STAGE_PERCENT["smoke_start"], up.STAGE_PERCENT["smoke_end"]
    assert ticks and ticks[-1][0] == "smoke" and ticks[-1][1] == end and ticks[-1][2] == "آزمون 4 از 4"
    assert all(start <= pct <= end for _s, pct, _d in ticks)
    assert [pct for _s, pct, _d in ticks] == sorted(pct for _s, pct, _d in ticks)
    assert all(d.startswith("آزمون ") and " از 4" in d for _s, _p, d in ticks)
    assert up.smoke_progress_percent(0, 42) == start and up.smoke_progress_percent(42, 42) == end
    # «آزمون ۱ از ۴۵» must not sit at 88٪ (that looked like the job was almost done)
    assert up.smoke_progress_percent(1, 45) == start + (end - start) // 45
    assert up.smoke_progress_percent(1, 45) < 50
    assert up.smoke_worker_count() == max(1, min(4, os.cpu_count() or 1))
    print("  default_smoke: parallel pool, sorted log, progress smoke_start→smoke_end OK")
    test_status_texts(db2, c, who)
    up._http_get = None
    print("smoke_updater: ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
