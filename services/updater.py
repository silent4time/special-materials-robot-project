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
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

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


def _download(
    url: str,
    on_progress: Callable[[int, int], None] | None = None,
    timeout: float = 300,
) -> tuple[int, bytes]:
    """GET a release asset. ``on_progress(done, total)`` — total is 0 when the length is unknown."""
    if _http_get is not None:
        status, blob = _http_get(url, _headers("application/octet-stream"))
        if on_progress and status == 200 and blob:
            on_progress(len(blob), len(blob))
        return status, blob
    import httpx

    timeout_cfg = httpx.Timeout(timeout, connect=20.0)
    with httpx.Client(timeout=timeout_cfg, follow_redirects=True) as client:
        with client.stream("GET", url, headers=_headers("application/octet-stream")) as resp:
            if resp.status_code != 200:
                resp.read()
                return resp.status_code, b""
            total = int(resp.headers.get("content-length") or 0)
            parts: list[bytes] = []
            done = 0
            last_emit = 0.0
            for chunk in resp.iter_bytes(64 * 1024):
                if not chunk:
                    continue
                parts.append(chunk)
                done += len(chunk)
                if on_progress:
                    now = time.monotonic()
                    if (total and done >= total) or now - last_emit >= 0.4:
                        on_progress(done, total)
                        last_emit = now
            if on_progress and done and (not total or done < total):
                on_progress(done, total)
            return 200, b"".join(parts)


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
        "status": settle_status(db),
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
# Single place for the About line on bot and web. Change this string only.
CREATOR_NAME = "واحد نسوز تاندیش فولاد خوزستان"
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
    """Creator shown in About. Empty string hides the name (placeholder instead)."""
    name = (CREATOR_NAME or "").strip()
    return name or None


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
        line = about_status_line(info)
        if line:
            lines += ["", "وضعیت به‌روزرسانی:", line]
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
    if st.get("state") == "running" and not st.get("started_epoch"):
        st["started_epoch"] = time.time()
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


INSTALLED_FA = "✅ آخرین نسخه نصب است — نسخهٔ {version}"


def settle_status(db: Any, *, root: Path | None = None) -> dict:
    """Close a finished install that the database still calls «running».

    A live lock is left alone (the install really is still going). When the
    files are already the target version and nothing is installing, the row
    becomes «applied» so About stops saying «در حال به‌روزرسانی».
    """
    st = get_status(db)
    if st.get("state") != "running":
        return st
    root = Path(root or config.BASE_DIR)
    cur = current_version(root)
    target = str(st.get("version") or "")
    if not cur or not target or update_in_progress(root):
        return st
    if cur == target or is_newer(cur, target):
        _set_status(
            db,
            state="applied",
            version=cur,
            previous=st.get("previous") or "",
            message="آخرین نسخه نصب است",
            reported=True,
        )
        return get_status(db)
    return st


def about_status_line(info: dict) -> str:
    """The «وضعیت به‌روزرسانی» line. Finished + nothing newer → latest is installed."""
    st = info.get("status") or {}
    if st.get("state") == "running":
        return status_fa(st)
    if st.get("state") in ("failed", "rolled_back"):
        return status_fa(st)
    if not info.get("available"):
        cur = info.get("current") or st.get("version") or ""
        return INSTALLED_FA.format(version=cur) if cur else ""
    return status_fa(st)


# --------------------------------------------------------------------------- progress + lock
# In-process flag blocks a second click in this process before the lock file exists.
# The lock file blocks another process (the other service, or a second user).
# A reserve that the child never adopts expires quickly; a live apply expires after an hour.
LOCK_STALE_SECONDS = 3600
RESERVE_STALE_SECONDS = 90
STATUS_STALE_SECONDS = 30 * 60
BAR_WIDTH = 6
BUSY_FA = "به‌روزرسانی در حال انجام است"
SUCCESS_FA = "✅ به‌روزرسانی کامل شد — نسخهٔ فعال: {version}"
STAGE_PERCENT = {"verify": 60, "backup": 75, "install": 88, "smoke_start": 88, "smoke_end": 95, "restart": 96}
STAGE_FA = {
    "download": "در حال دانلود",
    "verify": "در حال بررسی چک‌سام",
    "backup": "در حال تهیهٔ نسخهٔ پشتیبان از نصب فعلی",
    "install": "در حال نصب و استخراج",
    "smoke": "در حال اجرای آزمون‌های دود",
    "restart": "در حال راه‌اندازی مجدد سرویس‌ها",
}

_proc_guard = threading.Lock()
_proc_busy = False


def updates_dir(root: Path | None = None) -> Path:
    """Lock and progress files.

    ``NASOZ_UPDATES_DIR`` redirects the *process-default* directory (no explicit
    root). The updater sets it for smoke subprocesses, and ``smoke_updater``
    sets it itself, so a smoke run cannot see or delete the lock the updater
    is holding. An explicit ``root`` (a real apply, or a temp project in tests)
    always uses ``<root>/data/updates``.
    """
    if root is None:
        override = os.environ.get("NASOZ_UPDATES_DIR", "").strip()
        if override:
            return Path(override)
    return Path(root or config.BASE_DIR) / "data" / "updates"


def lock_path(root: Path | None = None) -> Path:
    return updates_dir(root) / "update.lock"


def progress_state_path(root: Path | None = None) -> Path:
    return updates_dir(root) / "update_progress.json"


def _read_lock(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    try:
        pid = int((raw or "").strip() or 0)
    except ValueError:
        pid = 0
    try:
        started = path.stat().st_mtime
    except OSError:
        started = 0
    return {"pid": pid, "started": started, "token": "", "role": "apply"}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_info_blocks(info: dict, now: float) -> bool:
    try:
        started = float(info.get("started") or 0)
    except (TypeError, ValueError):
        started = 0.0
    if started <= 0:
        return False
    age = now - started
    if age >= LOCK_STALE_SECONDS:
        return False
    if str(info.get("role") or "") == "reserve" and age >= RESERVE_STALE_SECONDS:
        return False
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid and not _pid_alive(pid):
        return False
    return True


def _lock_blocks(root: Path | None = None, now: float | None = None) -> bool:
    path = lock_path(root)
    if not path.exists():
        return False
    return _lock_info_blocks(_read_lock(path), time.time() if now is None else now)


def clear_proc_flag() -> None:
    """Drop the in-process flag without deleting the lock file (the child still holds it)."""
    global _proc_busy
    with _proc_guard:
        _proc_busy = False


def update_in_progress(root: Path | None = None) -> bool:
    with _proc_guard:
        if _proc_busy:
            return True
    return _lock_blocks(root)


def try_acquire_update_lock(root: Path | None = None, *, adopt_token: str | None = None) -> str | None:
    """Reserve the lock, or adopt a reservation with the same token. None → busy."""
    global _proc_busy
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    token = adopt_token or secrets.token_hex(8)
    payload = {
        "pid": os.getpid(),
        "started": now,
        "token": token,
        "role": "apply" if adopt_token else "reserve",
    }

    def _create() -> None:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, json.dumps(payload).encode())
        finally:
            os.close(fd)

    with _proc_guard:
        if _proc_busy and not adopt_token:
            return None
        try:
            _create()
        except FileExistsError:
            info = _read_lock(path)
            if adopt_token and info.get("token") == adopt_token and _lock_info_blocks(info, now):
                payload["started"] = float(info.get("started") or now)
                payload["token"] = adopt_token
                path.write_text(json.dumps(payload), encoding="utf-8")
            elif not _lock_info_blocks(info, now):
                path.unlink(missing_ok=True)
                try:
                    _create()
                except FileExistsError:
                    return None
            else:
                return None
        _proc_busy = True
        return token


def release_update_lock(root: Path | None = None, *, token: str | None = None, force: bool = False) -> None:
    global _proc_busy
    path = lock_path(root)
    with _proc_guard:
        if path.exists() and token and not force:
            held = str(_read_lock(path).get("token") or "")
            if held and held != token:
                _proc_busy = False
                return
        path.unlink(missing_ok=True)
        _proc_busy = False


def write_progress_state(
    chat_id: Any,
    message_id: Any,
    version: str,
    root: Path | None = None,
    started: float | None = None,
) -> None:
    path = progress_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mid = int(message_id or 0)
    except (TypeError, ValueError):
        mid = 0
    path.write_text(
        json.dumps(
            {
                "chat_id": chat_id,
                "message_id": mid,
                "target_version": str(version or ""),
                "started": float(time.time() if started is None else started),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def read_progress_state(root: Path | None = None) -> dict | None:
    path = progress_state_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear_progress_state(root: Path | None = None) -> None:
    progress_state_path(root).unlink(missing_ok=True)


def progress_bar(percent: int, width: int = BAR_WIDTH) -> str:
    pct = max(0, min(100, int(percent)))
    filled = (pct * width + 50) // 100
    if 0 < pct < 100:
        filled = min(width - 1, max(1, filled))
    elif pct >= 100:
        filled = width
    else:
        filled = 0
    return "▰" * filled + "▱" * (width - filled) + f" {pct}%"


def _fmt_bytes(n: int) -> str:
    n = max(0, int(n))
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} مگابایت"
    if n >= 1024:
        return f"{n / 1024:.0f} کیلوبایت"
    return f"{n} بایت"


def download_progress(done: int, total: int) -> tuple[int, str]:
    """Bar position for the download stage (5–40) and a Persian detail line.

    The real percent is included only when the total size is known.
    """
    done = max(0, int(done))
    total = max(0, int(total))
    if total > 0:
        done_c = min(done, total)
        real = int(done_c * 100 / total)
        overall = 5 + int(35 * done_c / total)
        return overall, f"{_fmt_bytes(done_c)} از {_fmt_bytes(total)} ({real}٪)"
    return 8, f"{_fmt_bytes(done)} دریافت شد"


def progress_text(stage: str, percent: int, version: str = "", detail: str = "") -> str:
    lines = [f"⬆️ به‌روزرسانی به نسخهٔ {version}" if version else "⬆️ به‌روزرسانی ربات"]
    lines.append(STAGE_FA.get(stage, stage))
    if detail:
        lines.append(detail)
    lines.append(progress_bar(percent))
    return "\n".join(lines)


def failure_text(reason: str) -> str:
    reason = (reason or "خطای نامشخص").strip()
    return f"❌ به‌روزرسانی ناموفق بود\n{reason}\nقفل آزاد شد. می‌توانید دوباره تلاش کنید."


def final_failure_text(current: str, target: str) -> str:
    cur = current or "نامشخص"
    tgt = target or "نامشخص"
    return f"❌ به‌روزرسانی کامل نشد — نسخهٔ فعال: {cur} (هدف: {tgt})"


def _emit(progress: Callable[..., None] | None, stage: str, percent: int, version: str, detail: str = "") -> None:
    if progress is None:
        return
    try:
        progress(stage, int(percent), str(version or ""), str(detail or ""))
    except Exception:  # noqa: BLE001 — progress must not abort the install
        logger.warning("progress callback failed", exc_info=True)


def _menu_for_chat(db: Any, chat_id: Any) -> dict | None:
    """Main menu of the owner who is watching the update, so buttons come back."""
    from bot import keyboards as kb

    owners = owner_recipients(db)
    user = next((u for u in owners if str(u.get("bale_user_id")) == str(chat_id)), None)
    if user is None and owners:
        user = owners[0]
    return kb.main_menu(user) if user else None


def _send_result_with_menu(client: Any, db: Any, chat_id: Any, text: str) -> bool:
    """The completion message carries the menu; until then only the wait button is shown."""
    if client is None or chat_id in (None, ""):
        return False
    try:
        client.send_message(chat_id, text, _menu_for_chat(db, chat_id))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("update result menu send failed", exc_info=True)
        return False


def bale_progress_editor(chat_id: Any, message_id: int) -> Callable[..., None]:
    """Edit one Bale message. Download ticks are throttled; errors always go through."""
    holder: dict[str, Any] = {"client": None, "last_pct": -1, "last_t": 0.0, "stage": ""}

    def progress(stage: str, percent: int, version: str, detail: str = "") -> None:
        now = time.monotonic()
        if stage == "download" and holder["stage"] == "download":
            if percent < 40 and now - holder["last_t"] < 1.0 and abs(percent - holder["last_pct"]) < 4:
                return
        holder["stage"] = stage
        holder["last_pct"] = percent
        holder["last_t"] = now
        if stage == "error":
            text = detail or failure_text("")
        elif stage == "done":
            text = SUCCESS_FA.format(version=version)
        else:
            text = progress_text(stage, percent, version, detail)
        try:
            if holder["client"] is None:
                from bot.bale_api import BaleClient

                holder["client"] = BaleClient()
            holder["client"].edit_message_text(chat_id, int(message_id), text)
            if stage in ("done", "error"):
                from db.models import Database

                import config

                db = Database(config.DATABASE_PATH)
                if _send_result_with_menu(holder["client"], db, chat_id, text):
                    _set_status(db, reported=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not edit update progress: %s", exc)

    return progress


def bale_progress_editor_from_state(root: Path | None = None) -> Callable[..., None] | None:
    state = read_progress_state(root)
    if not state:
        return None
    chat_id = state.get("chat_id")
    try:
        message_id = int(state.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    if not chat_id or not message_id:
        return None
    return bale_progress_editor(chat_id, message_id)


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


def smoke_failure_text(smoke: Callable | None = None, root: Path | None = None) -> str:
    """Persian reason naming the failed smoke scripts and the log file."""
    names = [str(n) for n in (getattr(smoke, "last_failures", None) or []) if n]
    path = str(getattr(smoke, "last_log", None) or (updates_dir(root) / "smoke.log"))
    if names:
        shown = "، ".join(names[:8])
        extra = f" و {len(names) - 8} مورد دیگر" if len(names) > 8 else ""
        return f"تست‌های دود (smoke) پس از به‌روزرسانی رد شدند: {shown}{extra}\nخروجی: {path}"
    return f"تست‌های دود (smoke) پس از به‌روزرسانی رد شدند.\nخروجی: {path}"


SMOKE_TIMEOUT_SECONDS = 900
SMOKE_PROGRESS_INTERVAL = 3.0


def smoke_worker_count() -> int:
    """Bounded pool. Each script is already its own process; threads only wait."""
    return max(1, min(4, os.cpu_count() or 1))


def smoke_progress_percent(done: int, total: int) -> int:
    """Map completed tests onto 88% … 95% (install is 88, restart is 96)."""
    if total <= 0:
        return STAGE_PERCENT["smoke_end"]
    span = STAGE_PERCENT["smoke_end"] - STAGE_PERCENT["smoke_start"]
    done = max(0, min(int(done), int(total)))
    return STAGE_PERCENT["smoke_start"] + (span * done) // int(total)


def _smoke_output(stdout: str | bytes | None, stderr: str | bytes | None, *, prefix: str = "") -> str:
    def _text(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value or "")

    parts = [prefix, _text(stdout).rstrip(), _text(stderr).rstrip()]
    return "\n".join(part for part in parts if part)


def _run_smoke_script(py: str, script: Path, root: Path) -> tuple[str, bool, str]:
    """One smoke script. Its own updates dir; the script's _smoke_env isolates DB/reports/uploads."""
    scratch = Path(tempfile.mkdtemp(prefix=f"nasoz-smoke-{script.stem}-"))
    env = os.environ.copy()
    env["NASOZ_UPDATES_DIR"] = str(scratch)
    try:
        try:
            r = subprocess.run(
                [py, str(script)], cwd=str(root), capture_output=True, text=True,
                timeout=SMOKE_TIMEOUT_SECONDS, env=env,
            )
            passed = r.returncode == 0
            detail = _smoke_output(r.stdout, r.stderr)
        except subprocess.TimeoutExpired as exc:
            passed = False
            detail = _smoke_output(exc.stdout, exc.stderr, prefix="TIMEOUT after 900s")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return script.name, passed, detail


def default_smoke(
    root: Path,
    log: Callable[[str], None],
    *,
    progress: Callable[..., None] | None = None,
    version: str = "",
    workers: int | None = None,
) -> bool:
    """Run ``scripts/smoke_*.py`` in a bounded pool.

    Every current smoke script is hermetic: ``scripts/_smoke_env.py`` gives it a
    private temp database, ``reports/`` and ``uploads/``, and this runner gives
    it a private ``NASOZ_UPDATES_DIR``. Nothing in the suite writes the install's
    ``data/bot.db`` or a shared reports/uploads directory, so none are kept serial.
    ``smoke_phase3`` copies samples to ``/tmp/phase3_style_samples``; only that
    one script does, so it can share the pool.

    PASS/FAIL lines and ``last_failures`` follow sorted script names, not finish
    order. Progress (stage ``smoke``) moves from 88% toward 95% and is emitted
    at most about every 3 seconds, plus the first and last result.
    """
    py = root / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    scripts = sorted((root / "scripts").glob("smoke_*.py"))
    pool = smoke_worker_count() if workers is None else max(1, int(workers))
    log_path = updates_dir(root) / "smoke.log"
    total = len(scripts)
    results: dict[str, tuple[bool, str]] = {}
    done = 0
    last_emit = 0.0

    def _maybe_emit(force: bool = False) -> None:
        nonlocal last_emit
        if progress is None or total == 0:
            return
        now = time.monotonic()
        if not force and done not in (1, total) and now - last_emit < SMOKE_PROGRESS_INTERVAL:
            return
        _emit(
            progress, "smoke", smoke_progress_percent(done, total), version,
            f"آزمون {done} از {total}",
        )
        last_emit = now

    if scripts:
        with ThreadPoolExecutor(max_workers=min(pool, total)) as executor:
            futures = [executor.submit(_run_smoke_script, py, script, root) for script in scripts]
            for future in as_completed(futures):
                try:
                    name, passed, detail = future.result()
                except Exception as exc:  # noqa: BLE001
                    name, passed, detail = "smoke_unknown.py", False, f"خطای داخلی: {exc}"
                results[name] = (passed, detail)
                done += 1
                _maybe_emit(force=(done == total))

    failed: list[str] = []
    chunks: list[str] = []
    ok = True
    for script in scripts:
        passed, detail = results.get(script.name, (False, "نتیجه‌ای برنگشت"))
        log(f"{'PASS' if passed else 'FAIL'} {script.name}")
        if not passed:
            failed.append(script.name)
            chunks.append(f"===== FAIL {script.name} =====\n{detail[-8000:]}\n")
            ok = False
    if chunks:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(chunks), encoding="utf-8")
    default_smoke.last_failures = failed  # type: ignore[attr-defined]
    default_smoke.last_log = str(log_path)  # type: ignore[attr-defined]
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
    adopt_token: str | None = None,
    progress: Callable[..., None] | None = None,
) -> dict:
    root = Path(root or config.BASE_DIR)
    db_path = Path(db_path or config.DATABASE_PATH)
    smoke = smoke or default_smoke
    restart = restart or default_restart
    work = root / "data" / "updates"
    work.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    log = lines.append
    token = try_acquire_update_lock(root, adopt_token=adopt_token)
    if not token:
        return {"state": "busy", "message": BUSY_FA}
    cur = ""
    ver = ""
    snapshot = work / "rollback_pending"
    added: list[Path] = []
    overlaid = False
    try:
        cur = current_version(root)
        info = get_remote(db, force=True)
        ver = info.get("version") or ""
        ts = datetime.now(TEHRAN).strftime("%Y%m%d_%H%M%S")
        snapshot = work / f"rollback_{cur}_{ts}"
        if not is_newer(ver, cur):
            if info.get("error"):
                log("remote: " + redact_public(str(info.get("error"))))
                raise UpdateError("بررسی نسخهٔ جدید ممکن نشد.")
            raise UpdateError("نسخهٔ جدیدتری وجود ندارد.")
        _set_status(db, state="running", version=ver, previous=cur, requested_by=requested_by, message="")
        # 1) download + verify
        _emit(progress, "download", 5, ver, "آماده‌سازی دانلود…")

        def _on_dl(done: int, total: int) -> None:
            pct, detail = download_progress(done, total)
            _emit(progress, "download", pct, ver, detail)

        status, blob = _download(info["package"], on_progress=_on_dl, timeout=300)
        if status != 200 or not blob:
            raise UpdateError(f"دانلود بسته ناموفق بود (HTTP {status}).")
        digest = hashlib.sha256(blob).hexdigest()
        _emit(progress, "verify", STAGE_PERCENT["verify"], ver)
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
        _emit(progress, "backup", STAGE_PERCENT["backup"], ver)
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
        _emit(progress, "install", STAGE_PERCENT["install"], ver)
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
        # 5) smoke suite (parallel; progress 88% → 95%)
        if smoke is default_smoke:
            smoke_ok = default_smoke(root, log, progress=progress, version=ver)
        else:
            smoke_ok = smoke(root, log)
        if not smoke_ok:
            raise UpdateError(smoke_failure_text(smoke, root))
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
        _emit(progress, "restart", STAGE_PERCENT["restart"], ver)
        how = restart(log)
        log(f"restart: {how}")
        msg = f"از {cur} به {ver}." + ("" if how != "none" else " سرویس‌ها را دستی ری‌استارت کنید.")
        _set_status(db, state="applied", version=ver, previous=cur, message=msg, log=lines[-40:], reported=False)
        if how == "none":
            # Nothing is going to restart the process; close the progress message here.
            _emit(progress, "done", 100, ver, "")
            clear_progress_state(root)
        return {"state": "applied", "version": ver, "previous": cur, "message": msg, "log": lines}
    except Exception as e:  # noqa: BLE001
        reason = str(e) if isinstance(e, UpdateError) else f"خطای داخلی: {_safe_err(e)}"
        state = "failed"
        if overlaid:
            # Restore the pre-update bytes first. Deleting files the zip added
            # before that restore leaves VERSION at the new release and the new
            # files gone if the process stops between the two steps (what the
            # 1.7.1 updater did).
            restored = 0
            if snapshot.exists():
                for p in snapshot.rglob("*"):
                    if not p.is_file():
                        continue
                    dst = root / p.relative_to(snapshot)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
                    restored += 1
            for rel in added:
                (root / rel).unlink(missing_ok=True)
            state = "rolled_back"
            log(f"rolled back to {cur} ({restored} files restored, {len(added)} new removed)")
        if ver and is_newer(ver, cur):
            _set_status(db, state=state, version=ver, previous=cur, message=reason, log=lines[-40:], reported=False)
        _emit(progress, "error", 0, ver, failure_text(reason))
        clear_progress_state(root)
        return {"state": state, "version": ver, "previous": cur, "message": reason, "log": lines}
    finally:
        release_update_lock(root, token=token)


def launch_detached(requested_by: str = "", lock_token: str = "") -> str:
    """Start ``python -m services.updater apply`` outside the caller's service cgroup."""
    root = Path(config.BASE_DIR)
    py = root / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    args = [py, "-m", "services.updater", "apply", "--by", str(requested_by)]
    if lock_token:
        args += ["--lock-token", lock_token]
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
                client.send_message(u["bale_user_id"], text, _menu_for_chat(db, u["bale_user_id"]))
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


def status_started_epoch(st: dict) -> float | None:
    raw = st.get("started_epoch")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    at = str(st.get("at") or "")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(at, fmt).replace(tzinfo=TEHRAN).timestamp()
        except ValueError:
            continue
    return None


def reconcile_running_status(db: Any, *, root: Path | None = None, now: float | None = None) -> str | None:
    """Close a stuck ``running`` row on startup.

    * Running version equals the target → completed (✅, completion time is ``at``).
      A still-live lock younger than 30 minutes is left alone (install in progress).
    * Otherwise, version mismatch or a row older than 30 minutes → failed, and the lock is released.
    """
    st = get_status(db)
    if st.get("state") != "running":
        return None
    now = time.time() if now is None else now
    root = Path(root or config.BASE_DIR)
    target = str(st.get("version") or "")
    cur = current_version(root)
    started = status_started_epoch(st)
    age = (now - started) if started is not None else None
    timed_out = age is not None and age >= STATUS_STALE_SECONDS
    live = _lock_blocks(root, now)
    version_match = bool(cur) and cur == target
    if version_match:
        if live and not timed_out:
            return None
        _set_status(
            db,
            state="applied",
            version=target,
            previous=st.get("previous") or "",
            message=f"نسخهٔ فعال: {cur}",
            reported=False,
        )
        release_update_lock(root, force=True)
        return "completed"
    if timed_out or not live:
        if timed_out:
            reason = "مهلت به‌روزرسانی (۳۰ دقیقه) تمام شد و کار ناتمام ماند."
            kind = "stale"
        else:
            reason = f"نسخهٔ فعال ({cur or 'نامشخص'}) با نسخهٔ هدف ({target or 'نامشخص'}) یکی نیست."
            kind = "failed"
        _set_status(
            db,
            state="failed",
            version=target,
            previous=st.get("previous") or cur,
            message=reason,
            reported=False,
        )
        release_update_lock(root, force=True)
        return kind
    return None


def _deliver_final(client: Any, chat_id: Any, message_id: Any, text: str) -> bool:
    if client is None or chat_id in (None, ""):
        return False
    edited = False
    try:
        mid = int(message_id or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid:
        try:
            client.edit_message_text(chat_id, mid, text)
            edited = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("final update edit failed: %s", exc)
    if edited:
        return True
    try:
        client.send_message(chat_id, text)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("final update send failed", exc_info=True)
        return False


def finalize_after_restart(client: Any, db: Any, *, root: Path | None = None, now: float | None = None) -> str | None:
    """After boot: fix a stuck status and edit/send the saved progress message."""
    root = Path(root or config.BASE_DIR)
    outcome = reconcile_running_status(db, root=root, now=now)
    state = read_progress_state(root)
    st = get_status(db)
    if st.get("state") == "running" and _lock_blocks(root, now) and outcome is None:
        return None
    cur = current_version(root)
    target = str((state or {}).get("target_version") or "") or str(st.get("version") or "")
    if not state and outcome is None:
        return None
    success = bool(cur) and cur == target and outcome not in ("failed", "stale")
    text = SUCCESS_FA.format(version=cur) if success else final_failure_text(cur, target)
    delivered = False
    if state:
        delivered = _deliver_final(client, state.get("chat_id"), state.get("message_id"), text)
        if delivered:
            _send_result_with_menu(client, db, state.get("chat_id"), text)
            clear_progress_state(root)
    elif outcome and client is not None:
        owners = owner_recipients(db)
        if not owners:
            delivered = True
        for u in owners:
            try:
                client.send_message(u["bale_user_id"], text, _menu_for_chat(db, u["bale_user_id"]))
                delivered = True
            except Exception:  # noqa: BLE001
                logger.warning("update result notify failed", exc_info=True)
    if delivered and get_status(db):
        _set_status(db, reported=True)
    return outcome or ("notified" if delivered else None)


def _opt(argv: list[str], name: str) -> str:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return ""


def _cli(argv: list[str]) -> int:
    from db.models import Database  # local import: keep module import light

    db = Database(config.DATABASE_PATH)
    if argv and argv[0] == "apply":
        by = _opt(argv, "--by")
        token = _opt(argv, "--lock-token")
        res = apply_update(
            db,
            requested_by=by,
            adopt_token=token or None,
            progress=bale_progress_editor_from_state(),
        )
        print(json.dumps({k: v for k, v in res.items() if k != "log"}, ensure_ascii=False))
        print("\n".join(res.get("log") or []))
        return 0 if res["state"] == "applied" else 1
    info = update_info(db, force="--force" in argv)
    print(json.dumps({k: info[k] for k in ("current", "latest", "available", "error")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
