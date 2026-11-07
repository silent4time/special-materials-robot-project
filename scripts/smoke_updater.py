"""Updater smoke (GitHub Releases mocked — no network), apply/rollback in a temp project tree.

Bot notice and web /about: product facts + version; update controls and install are owner-only
(manager text, forged callback, and POST are denied). User-visible text has no repo address.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import hashlib
import io
import json
import sys
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


def main() -> int:
    import config
    from db.models import Database
    from services import updater as up

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

    # smoke fails → automatic rollback
    gh.release("1.6.0")
    root = _project("rollback")
    res = up.apply_update(db, root=root, db_path=root / "data" / "bot.db", smoke=lambda r, log: False, restart=rs, pip=False)
    assert res["state"] == "rolled_back", res
    assert (root / "VERSION").read_text().strip() == "1.5.0" and (root / "main.py").read_text() == "print('old')\n"
    assert not (root / "services" / "new_mod.py").exists() and (root / "scripts" / "keep.py").read_text() == "k = 1\n"
    assert len(restarts) == 1 and up.get_status(db)["state"] == "rolled_back"
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
    assert "نسخهٔ جدیدتری نیست" in quiet and "github.com" not in quiet
    assert up.creator_name() is None and "ثبت نشده است" in quiet
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
    up.launch_detached = lambda by="": launched.append(by) or "test"
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
    assert up.current_version() in mgr and "ثبت نشده است" in mgr and "تاندیش اسلب" in mgr, mgr
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
    assert launched == ["61"] and "شروع شد" in client.sent[-1][1], client.sent[-1]
    forged("61", "upd|nope")
    assert launched == ["61"]
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
    assert "ثبت نشده است" in h and "تاندیش اسلب" in h
    assert "github.com" not in h and "special-materials-robot-project" not in h
    r = c.post("/about/update")
    assert r.status_code == 200 and launched == ["61", "61"] and "شروع شد" in r.text
    assert "github.com" not in r.text
    who["u"] = db2.get_user("62")
    h = c.get("/home").text
    assert "update-banner" not in h and 'href="/about"' in h
    h = c.get("/about").text
    assert up.current_version() in h and "ثبت نشده است" in h
    assert 'action="/about/check"' not in h and 'action="/about/update"' not in h
    assert "github.com" not in h
    assert c.post("/about/check", follow_redirects=False).status_code == 403
    assert c.post("/about/update", follow_redirects=False).status_code == 403
    assert launched == ["61", "61"]
    who["u"] = db2.get_user("63")
    h = c.get("/home").text
    assert "update-banner" not in h and 'href="/about"' not in h
    assert c.get("/about", follow_redirects=False).status_code in (303, 403)
    assert c.post("/about/update", follow_redirects=False).status_code in (303, 403)
    assert c.post("/about/check", follow_redirects=False).status_code in (303, 403)
    assert launched == ["61", "61"]
    print("  web: banner + /about owner-only install, manager POST denied, no repo URL OK")
    up._http_get = None
    print("smoke_updater: ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
