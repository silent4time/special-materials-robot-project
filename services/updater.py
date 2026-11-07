"""v1.6.0 — به‌روزرسانی خودکار از GitHub Releases (ربات و پنل وب، یکسان).

Design follows silent4time/Carunit-login-WP ``src/Updater.php``:
  * GET /repos/<repo>/releases/latest → version = tag_name without the leading v/V
  * SemVer compare with ./VERSION; result cached 6h (1h after a failure) in bot_settings
  * package = release asset ``nasoz-bot.zip`` (built by scripts/make_release.sh with
    ``git archive --prefix=<slug>/``), fallback ``zipball_url``; the top folder of the
    archive is stripped whatever its name (GitHub zipballs use owner-repo-sha/)
  * ``nasoz-bot.zip.sha256`` asset → sha256 verified before anything is touched

apply_update(): lock → download → verify → DB backup (sqlite backup API) + code snapshot →
extract to staging → overlay files (never .env, data/, uploads/, reports/, .venv, .git,
nor files the user added locally) → pip install if requirements changed → smoke suite →
rollback on any failure → restart nasoz-bot/nasoz-web via a transient systemd unit.

The apply runs in its own process (``python -m services.updater apply``) launched through
``systemd-run`` (falls back to a detached setsid process) so restarting the services
does not kill the updater itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import config

CACHE_KEY = "update_cache"
NOTIFIED_KEY = "update_notified_version"
STATUS_KEY = "update_status"
TTL_OK = 6 * 3600
TTL_FAIL = 3600
PROTECTED_TOP = {".env", "data", "uploads", "reports", ".venv", "venv", ".git", "__pycache__", "logs"}
TEHRAN = timezone(timedelta(hours=3, minutes=30))
SERVICES = ("nasoz-bot", "nasoz-web")

# test hook: replaced by scripts/smoke_updater.py (returns (status, bytes))
_http_get: Callable[[str, dict], tuple[int, bytes]] | None = None


# --------------------------------------------------------------------------- versions
def current_version(root: Path | None = None) -> str:
    try:
        return (Path(root or config.BASE_DIR) / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def parse_semver(v: str) -> tuple:
    v = (v or "").strip().lstrip("vV")
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?", v)
    if not m:
        return (0, 0, 0, 0, ())
    pre = m.group(4)
    # a release without a pre-release tag sorts after its pre-releases
    pre_key = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ()
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), 0 if pre else 1, pre_key)


def is_newer(remote: str, local: str) -> bool:
    return bool(remote) and parse_semver(remote) > parse_semver(local)


# --------------------------------------------------------------------------- HTTP
def _headers(accept: str = "application/vnd.github+json") -> dict:
    h = {"Accept": accept, "User-Agent": "nasoz-bot-updater", "X-GitHub-Api-Version": "2022-11-28"}
    if config.UPDATE_GITHUB_TOKEN:
        h["Authorization"] = "Bearer " + config.UPDATE_GITHUB_TOKEN
    return h


def _get(url: str, accept: str = "application/vnd.github+json", timeout: float = 20) -> tuple[int, bytes]:
    if _http_get is not None:
        return _http_get(url, _headers(accept))
    import httpx

    r = httpx.get(url, headers=_headers(accept), timeout=timeout, follow_redirects=True)
    return r.status_code, r.content


def fetch_release(repo: str | None = None) -> dict:
    """Latest published (non-draft, non-prerelease) release → normalised dict. Raises on error."""
    repo = repo or config.UPDATE_REPO
    status, body = _get(f"https://api.github.com/repos/{repo}/releases/latest")
    if status != 200:
        raise RuntimeError(f"GitHub HTTP {status}")
    data = json.loads(body.decode("utf-8"))
    version = str(data.get("tag_name") or "").strip().lstrip("vV")
    if not version:
        raise RuntimeError("release without tag_name")
    package = sha_url = ""
    for a in data.get("assets") or []:
        if a.get("name") == config.UPDATE_ASSET:
            package = a.get("browser_download_url") or ""
        elif a.get("name") == config.UPDATE_ASSET_SHA:
            sha_url = a.get("browser_download_url") or ""
    return {
        "version": version,
        "tag": data.get("tag_name") or "",
        "package": package or data.get("zipball_url") or "",
        "package_is_asset": bool(package),
        "sha256_url": sha_url,
        "html_url": data.get("html_url") or "",
        "changelog": data.get("body") or "",
        "published_at": data.get("published_at") or "",
    }


def get_remote(db: Any, force: bool = False, now: float | None = None) -> dict:
    """Cached latest-release info. ``{'version': ''}`` (+error) when GitHub was unreachable."""
    now = now or time.time()
    if not force:
        try:
            cached = json.loads(db.get_setting(CACHE_KEY) or "{}")
        except (ValueError, TypeError):
            cached = {}
        if cached and now - float(cached.get("checked_at", 0)) < float(cached.get("ttl", 0)):
            return cached.get("info") or {"version": ""}
    try:
        info, ttl = fetch_release(), TTL_OK
    except Exception as e:  # noqa: BLE001 — network / JSON / rate limit
        info, ttl = {"version": "", "error": _safe_err(e)}, TTL_FAIL
    db.set_setting(CACHE_KEY, json.dumps({"checked_at": now, "ttl": ttl, "info": info}, ensure_ascii=False))
    return info


def cached_remote(db: Any) -> dict:
    """Never touches the network (used by every web page render)."""
    try:
        return (json.loads(db.get_setting(CACHE_KEY) or "{}").get("info")) or {"version": ""}
    except (ValueError, TypeError):
        return {"version": ""}


_GH_URL = re.compile(
    r"(?:https?://|git@)?(?:www\.)?"
    r"(?:github\.com|api\.github\.com|raw\.githubusercontent\.com|"
    r"objects\.githubusercontent\.com|release-assets\.githubusercontent\.com|"
    r"codeload\.github\.com)[^\s)>\]]*|"
    r"github\.com/\S+",
    re.I,
)
_REPO_SLUG = re.compile(r"\b[\w.-]+/special-materials-robot-project\b", re.I)


def redact_public(text: str) -> str:
    """Strip repo addresses and the server token from anything a person can read."""
    s = str(text or "")
    tok = config.UPDATE_GITHUB_TOKEN
    if tok:
        s = s.replace(tok, "")
    s = _GH_URL.sub("", s)
    s = _REPO_SLUG.sub("", s)
    return s


def _safe_err(e: Exception) -> str:
    return redact_public(str(e)[:300])


def update_info(db: Any, *, force: bool = False, network: bool = True) -> dict:
    remote = get_remote(db, force=force) if network else cached_remote(db)
    cur = current_version()
    return {
        "current": cur,
        "remote": remote,
        "latest": remote.get("version") or "",
        "available": is_newer(remote.get("version") or "", cur),
        "error": remote.get("error") or "",
        "status": get_status(db),
    }


def local_changelog(version: str, root: Path | None = None) -> str:
    """The «## [x.y.z]» section of CHANGELOG.md (used when the release body is empty)."""
    try:
        text = (Path(root or config.BASE_DIR) / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"^##\s[^\n]*?(?<![\d.])v?{re.escape(version)}(?![\d.])[^\n]*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def changelog_text(info: dict) -> str:
    raw = (info.get("remote") or {}).get("changelog") or local_changelog(info.get("latest") or "") or "—"
    cleaned = redact_public(raw).strip()
    return cleaned or "—"


# --------------------------------------------------------------------------- about (bot + web share this; no second copy)
PRODUCT_TITLE = "ربات مواد نسوز تاندیش"
CHECK_FAIL_FA = "⚠️ بررسی به‌روزرسانی ممکن نشد. بعداً دوباره امتحان کنید."
NO_UPDATE_FA = "✅ نسخهٔ جدیدتری نیست. نسخهٔ فعلی: {current}"
OWNER_ONLY_UPDATE_FA = "⛔ به‌روزرسانی فقط برای مالک مجاز است."


def product_spec_lines() -> list[str]:
    """Facts already fixed in config (tundish standard, input labels, report font)."""
    kinds = "، ".join(config.TUNDISH_TYPES.values())
    files = "، ".join(item["label_fa"] for item in config.FILE_TYPES.values())
    return [
        f"استاندارد تاندیش کارخانه: {kinds}",
        f"ورودی‌های داده: {files}",
        "رابط فارسی و راست‌به‌چپ؛ گزارش PDF و اکسل با قلم Vazirmatn",
    ]


def creator_name() -> str | None:
    """Person named as creator, only when application metadata records one.

    This tree has no ``__author__``, package author, or creator field.
    """
    return None


def about_facts() -> dict:
    return {
        "title": PRODUCT_TITLE,
        "specs": product_spec_lines(),
        "creator": creator_name(),
        "version": current_version(),
    }


def about_text(info: dict, *, owner: bool, checked: bool = False) -> str:
    """Persian About body. Update lines only for the owner, and never a repo address."""
    facts = about_facts()
    lines = ["ℹ️ درباره ربات", f"نام: {facts['title']}", "مشخصات:"]
    lines += [f"• {item}" for item in facts["specs"]]
    if facts["creator"]:
        lines.append(f"سازنده: {facts['creator']}")
    else:
        lines.append("سازنده: در فرادادهٔ برنامه ثبت نشده است.")
    lines.append(f"نسخهٔ فعلی: {info.get('current') or facts['version']}")
    if owner:
        if checked and info.get("error"):
            lines.append(CHECK_FAIL_FA)
        elif info.get("available"):
            lines.append(f"نسخهٔ آمادهٔ نصب: {info.get('latest') or ''}")
            note = changelog_text(info)
            if note and note != "—":
                lines += ["", "تغییرات:", note[:1500]]
        elif checked:
            lines.append(NO_UPDATE_FA.format(current=info.get("current") or facts["version"]))
        st = status_fa(info.get("status") or {})
        if st:
            lines += ["", "وضعیت به‌روزرسانی:", st]
    return redact_public("\n".join(lines))


# --------------------------------------------------------------------------- status
def get_status(db: Any) -> dict:
    try:
        return json.loads(db.get_setting(STATUS_KEY) or "{}")
    except (ValueError, TypeError):
        return {}


def _set_status(db: Any, **kw) -> None:
    st = get_status(db)
    st.update(kw)
    st["at"] = datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M")
    db.set_setting(STATUS_KEY, json.dumps(st, ensure_ascii=False))


STATE_FA = {
    "running": "⏳ در حال به‌روزرسانی",
    "applied": "✅ به‌روزرسانی انجام شد",
    "rolled_back": "↩️ ناموفق — به نسخهٔ قبل برگشت داده شد",
    "failed": "❌ ناموفق (چیزی تغییر نکرد)",
}


def status_fa(st: dict) -> str:
    if not st:
        return ""
    s = f"{STATE_FA.get(st.get('state'), st.get('state'))} — نسخهٔ {st.get('version', '')} ({st.get('at', '')} تهران)"
    if st.get("message"):
        s += f"\n{st['message']}"
    return redact_public(s)


# --------------------------------------------------------------------------- apply
class UpdateError(Exception):
    pass


def _rel_ok(rel: Path) -> bool:
    parts = rel.parts
    return bool(parts) and parts[0] not in PROTECTED_TOP and "__pycache__" not in parts and ".." not in parts


def _backup_db(db_path: Path, dest: Path) -> Path | None:
    if not db_path.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    a = sqlite3.connect(str(db_path))
    b = sqlite3.connect(str(dest))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()
    return dest


def _extract(zip_path: Path, staging: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.startswith("/") or ".." in Path(n).parts:
                raise UpdateError(f"مسیر ناامن در بسته: {n}")
        z.extractall(staging)
    tops = [p for p in staging.iterdir()]
    src = tops[0] if len(tops) == 1 and tops[0].is_dir() else staging  # fix_source_folder
    if not (src / "VERSION").exists() or not (src / "main.py").exists():
        raise UpdateError("بستهٔ دانلودشده ساختار پروژه را ندارد (VERSION / main.py).")
    return src


def default_smoke(root: Path, log: Callable[[str], None]) -> bool:
    py = root / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    ok = True
    for t in sorted((root / "scripts").glob("smoke_*.py")):
        try:
            r = subprocess.run([py, str(t)], cwd=root, capture_output=True, text=True, timeout=900)
            passed = r.returncode == 0
        except subprocess.TimeoutExpired:
            passed = False
        log(f"{'PASS' if passed else 'FAIL'} {t.name}")
        ok = ok and passed
    return ok


def default_restart(log: Callable[[str], None]) -> str:
    cmd = os.getenv("NASOZ_RESTART_CMD")
    if cmd:
        subprocess.Popen(cmd, shell=True, start_new_session=True)
        return "custom"
    if shutil.which("systemctl") and shutil.which("systemd-run"):
        unit = f"nasoz-update-restart-{int(time.time())}"
        r = subprocess.run(
            ["systemd-run", f"--unit={unit}", "--collect", "--on-active=3",
             "systemctl", "restart", *SERVICES],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return "systemd-run"
        log("systemd-run failed: " + (r.stderr or "")[:200])
    if shutil.which("systemctl"):
        subprocess.Popen(["sh", "-c", "sleep 3; systemctl restart " + " ".join(SERVICES)],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "setsid"
    return "none"


def apply_update(
    db: Any,
    *,
    root: Path | None = None,
    db_path: Path | None = None,
    requested_by: str = "",
    smoke: Callable[[Path, Callable[[str], None]], bool] | None = None,
    restart: Callable[[Callable[[str], None]], str] | None = None,
    pip: bool = True,
) -> dict:
    root = Path(root or config.BASE_DIR)
    db_path = Path(db_path or config.DATABASE_PATH)
    smoke = smoke or default_smoke
    restart = restart or default_restart
    work = root / "data" / "updates"
    work.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    log = lines.append
    lock = work / "update.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 3600:
            return {"state": "busy", "message": "یک به‌روزرسانی دیگر در حال اجراست."}
        lock.unlink(missing_ok=True)
        return apply_update(db, root=root, db_path=db_path, requested_by=requested_by,
                            smoke=smoke, restart=restart, pip=pip)
    cur = current_version(root)
    info = get_remote(db, force=True)
    ver = info.get("version") or ""
    ts = datetime.now(TEHRAN).strftime("%Y%m%d_%H%M%S")
    snapshot = work / f"rollback_{cur}_{ts}"
    added: list[Path] = []
    overlaid = False
    try:
        if not is_newer(ver, cur):
            if info.get("error"):
                log("remote: " + redact_public(str(info.get("error"))))
                raise UpdateError("بررسی نسخهٔ جدید ممکن نشد.")
            raise UpdateError("نسخهٔ جدیدتری وجود ندارد.")
        _set_status(db, state="running", version=ver, previous=cur, requested_by=requested_by, message="")
        # 1) download + verify
        status, blob = _get(info["package"], accept="application/octet-stream", timeout=300)
        if status != 200 or not blob:
            raise UpdateError(f"دانلود بسته ناموفق بود (HTTP {status}).")
        digest = hashlib.sha256(blob).hexdigest()
        if info.get("sha256_url"):
            s2, sha_body = _get(info["sha256_url"], accept="application/octet-stream")
            expected = (sha_body.decode("utf-8", "ignore").split() or [""])[0].lower() if s2 == 200 else ""
            if not expected or expected != digest:
                raise UpdateError("چک‌سام SHA-256 بسته مطابقت ندارد؛ به‌روزرسانی انجام نشد.")
            log(f"sha256 OK {digest}")
        elif info.get("package_is_asset"):
            raise UpdateError("فایل چک‌سام nasoz-bot.zip.sha256 در ریلیز نیست؛ به‌روزرسانی انجام نشد.")
        else:
            log(f"zipball fallback (no checksum asset) sha256={digest}")
        zpath = work / f"nasoz-bot-{ver}.zip"
        zpath.write_bytes(blob)
        # 2) backups
        bdb = _backup_db(db_path, root / "data" / "backups_manual" / f"bot_pre_v{ver}_{ts}.db")
        log(f"db backup: {bdb}")
        staging = Path(tempfile.mkdtemp(prefix="stage_", dir=work))
        src = _extract(zpath, staging)
        new_ver = current_version(src)
        if new_ver != ver:
            raise UpdateError(f"نسخهٔ داخل بسته ({new_ver}) با ریلیز ({ver}) یکی نیست.")
        new_files = [p.relative_to(src) for p in src.rglob("*") if p.is_file() and _rel_ok(p.relative_to(src))]
        snapshot.mkdir(parents=True)
        for rel in new_files:
            dst = root / rel
            if dst.exists():
                (snapshot / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, snapshot / rel)
            else:
                added.append(rel)
        log(f"code snapshot: {snapshot} ({len(new_files) - len(added)} files)")
        old_req = (root / "requirements.txt").read_bytes() if (root / "requirements.txt").exists() else b""
        # 3) overlay (user files not in the release are left alone)
        overlaid = True
        for rel in new_files:
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst)
        shutil.rmtree(staging, ignore_errors=True)
        log(f"applied {len(new_files)} files ({len(added)} new)")
        # 4) dependencies (inside the project venv only; no system packages)
        req = root / "requirements.txt"
        vpip = root / ".venv" / "bin" / "pip"
        if pip and req.exists() and req.read_bytes() != old_req and vpip.exists():
            r = subprocess.run([str(vpip), "install", "-q", "-r", str(req)], cwd=root,
                               capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                raise UpdateError("نصب وابستگی‌های پایتون ناموفق بود.")
            log("pip install OK")
        # 5) smoke suite
        if not smoke(root, log):
            raise UpdateError("تست‌های دود (smoke) پس از به‌روزرسانی رد شدند.")
        # keep git's index in step so a later `git pull` stays clean (best effort)
        if (root / ".git").exists() and shutil.which("git"):
            subprocess.run(["git", "fetch", "-q", "--tags", "origin"], cwd=root, capture_output=True, timeout=120)
            refs = [info.get("tag") or "", f"v{ver}"]
            shown = subprocess.run(["git", "show", "origin/main:VERSION"], cwd=root, capture_output=True, text=True)
            if shown.returncode == 0 and shown.stdout.strip() == ver:
                refs.append("origin/main")
            for ref in refs:
                if ref and subprocess.run(["git", "reset", "-q", "--mixed", ref], cwd=root,
                                          capture_output=True, timeout=60).returncode == 0:
                    log(f"git index → {ref}")
                    break
        how = restart(log)
        log(f"restart: {how}")
        msg = f"از {cur} به {ver}." + ("" if how != "none" else " سرویس‌ها را دستی ری‌استارت کنید.")
        _set_status(db, state="applied", version=ver, previous=cur, message=msg, log=lines[-40:], reported=False)
        return {"state": "applied", "version": ver, "previous": cur, "message": msg, "log": lines}
    except Exception as e:  # noqa: BLE001
        reason = str(e) if isinstance(e, UpdateError) else f"خطای داخلی: {_safe_err(e)}"
        state = "failed"
        if overlaid:
            for rel in added:
                (root / rel).unlink(missing_ok=True)
            if snapshot.exists():
                for p in snapshot.rglob("*"):
                    if p.is_file():
                        dst = root / p.relative_to(snapshot)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, dst)
            state = "rolled_back"
            log("rolled back to " + cur)
        if ver and is_newer(ver, cur):
            _set_status(db, state=state, version=ver, previous=cur, message=reason, log=lines[-40:], reported=False)
        return {"state": state, "version": ver, "previous": cur, "message": reason, "log": lines}
    finally:
        lock.unlink(missing_ok=True)


def launch_detached(requested_by: str = "") -> str:
    """Start ``python -m services.updater apply`` outside the caller's service cgroup."""
    root = Path(config.BASE_DIR)
    py = root / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    args = [py, "-m", "services.updater", "apply", "--by", str(requested_by)]
    logf = root / "data" / "updates" / "update.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("systemd-run") and os.geteuid() == 0:
        r = subprocess.run(
            ["systemd-run", f"--unit=nasoz-updater-{int(time.time())}", "--collect",
             f"--working-directory={root}", "-p", f"StandardOutput=append:{logf}",
             "-p", f"StandardError=append:{logf}", *args],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return "systemd-run"
    with open(logf, "ab") as out:
        subprocess.Popen(args, cwd=root, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    return "setsid"


# --------------------------------------------------------------------------- bot hooks
def notify_text(info: dict) -> str:
    from bot import keyboards as kb

    cl = changelog_text(info)
    if len(cl) > 1500:
        cl = cl[:1500] + "…"
    return redact_public(
        f"🆕 نسخهٔ جدید ربات آماده است\n"
        f"نسخهٔ فعلی: {info['current']}\nنسخهٔ جدید: {info['latest']}\n\n"
        f"📝 تغییرات:\n{cl}\n\n"
        f"برای نصب، دکمهٔ «{kb.BTN_UPDATE}» را بزنید (فقط مالک)."
    )


def owner_recipients(db: Any) -> list[dict]:
    return [u for u in db.list_users(active_only=True) if u.get("role") == "owner" and u.get("active")]


def notify_tick(client: Any, db: Any, keyboard: dict | None = None) -> str | None:
    """Bot loop hook: tell the owner once per new version; report finished updates."""
    out = None
    st = get_status(db)
    if st and st.get("state") in ("applied", "rolled_back", "failed") and not st.get("reported"):
        text = redact_public("⬆️ به‌روزرسانی ربات\n" + status_fa(st))
        for u in owner_recipients(db):
            try:
                client.send_message(u["bale_user_id"], text)
            except Exception:  # noqa: BLE001
                pass
        _set_status(db, reported=True)
        out = "reported"
    info = update_info(db)
    if info["available"] and db.get_setting(NOTIFIED_KEY) != info["latest"]:
        for u in owner_recipients(db):
            try:
                client.send_message(u["bale_user_id"], notify_text(info), keyboard)
            except Exception:  # noqa: BLE001
                pass
        db.set_setting(NOTIFIED_KEY, info["latest"])
        out = "notified"
    return out


def _cli(argv: list[str]) -> int:
    from db.models import Database  # local import: keep module import light

    db = Database(config.DATABASE_PATH)
    if argv and argv[0] == "apply":
        by = argv[argv.index("--by") + 1] if "--by" in argv else ""
        res = apply_update(db, requested_by=by)
        print(json.dumps({k: v for k, v in res.items() if k != "log"}, ensure_ascii=False))
        print("\n".join(res.get("log") or []))
        return 0 if res["state"] == "applied" else 1
    info = update_info(db, force="--force" in argv)
    print(json.dumps({k: info[k] for k in ("current", "latest", "available", "error")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
