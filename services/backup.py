"""💾 پشتیبان‌گیری کامل / بازگردانی (ربات + وب + install.sh --restore) — 1405/07/25.

Zip = db/bot.db (sqlite backup API, consistent while running) + uploads/ + bot_assets/ +
config/env_public.txt (.env WITHOUT secrets) + manifest.json (app, version/commit, dates,
counts, sha256 of every file). Stored in data/backups/. Restore validates the manifest and
every sha256, rejects unsafe paths, ALWAYS takes a pre-restore backup first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import BASE_DIR, BOT_ASSETS_DIR, DATABASE_PATH, UPLOAD_DIR
from services.housekeeping import BACKUP_DIR, backup_db

logger = logging.getLogger(__name__)

APP_ID = "nasoz"
FORMAT = 1
AUTO, MANUAL, PRE_RESTORE = "auto", "manual", "pre_restore"
TYPE_FA = {AUTO: "خودکار شبانه", MANUAL: "دستی", PRE_RESTORE: "پیش از بازگردانی"}
NAME_RE = re.compile(r"^nasoz_backup_(\d{4}-\d{2}-\d{2})_(\d{4,6})(?:_(manual|pre-restore))?\.zip$")
KEEP_DAILY, KEEP_WEEKLY, KEEP_PROTECTED_DAYS = 7, 4, 30
NIGHTLY_HOUR = 2
BALE_MAX_UPLOAD = 50 * 1024 * 1024  # Bale bot document limit (~50 MB)
MAX_UNCOMPRESSED = 8 * 1024 ** 3
SECRET_KEY_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASS|KEY|CREDENTIAL)", re.I)
ALLOWED_PREFIXES = ("db/", "uploads/", "bot_assets/", "config/")
# settings rows
S_LAST_AUTO_DAY, S_LAST_AUTO_AT, S_LAST_AUTO_STATUS = "backup_last_auto_day", "backup_last_auto_at", "backup_last_auto_status"
S_LAST_ATTEMPT = "backup_last_auto_attempt"


# module defaults (smoke tests re-point these to temp dirs so live data is never touched)
DEFAULTS: dict[str, Path] = {"db": DATABASE_PATH, "uploads": UPLOAD_DIR, "assets": BOT_ASSETS_DIR,
                             "backups": Path(os.getenv("NASOZ_BACKUP_DIR") or BACKUP_DIR), "env": BASE_DIR / ".env"}


@dataclass
class Paths:
    db: Path = field(default_factory=lambda: Path(DEFAULTS["db"]))
    uploads: Path = field(default_factory=lambda: Path(DEFAULTS["uploads"]))
    assets: Path = field(default_factory=lambda: Path(DEFAULTS["assets"]))
    backups: Path = field(default_factory=lambda: Path(DEFAULTS["backups"]))
    env: Path = field(default_factory=lambda: Path(DEFAULTS["env"]))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def _tehran_now() -> datetime:
    from bot.jalali import tehran_now

    return tehran_now()


def _jalali(dt: datetime) -> str:
    import jdatetime

    return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y-%m-%d")


def env_public(env_path: Path) -> str:
    """.env without secrets (token, passwords, keys) — never put the token in a backup."""
    if not env_path.is_file():
        return ""
    out = []
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        k = line.split("=", 1)[0].strip()
        if not k or k.startswith("#") or "=" not in line:
            continue
        if SECRET_KEY_RE.search(k):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _db_counts(db_file: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                                "('users','extracted_datasets','main_goal_months','site_stock_entries','inventory_rows','main_source_requests')"):
            out[t] = int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
    finally:
        con.close()
    return out


def _unique_name(backups: Path, kind: str, now: datetime) -> Path:
    suffix = {AUTO: "", MANUAL: "_manual", PRE_RESTORE: "_pre-restore"}[kind]
    stamp = now.strftime("%H%M")
    p = backups / f"nasoz_backup_{_jalali(now)}_{stamp}{suffix}.zip"
    if p.exists():
        p = backups / f"nasoz_backup_{_jalali(now)}_{now.strftime('%H%M%S')}{suffix}.zip"
    i = 1
    while p.exists():
        p = backups / f"nasoz_backup_{_jalali(now)}_{now.strftime('%H%M%S')}{i}{suffix}.zip"
        i += 1
    return p


def create_backup(kind: str = MANUAL, *, paths: Paths | None = None, actor: str = "") -> Path:
    paths = paths or Paths()
    paths.backups.mkdir(parents=True, exist_ok=True)
    now = _tehran_now()
    target = _unique_name(paths.backups, kind, now)
    with tempfile.TemporaryDirectory(dir=paths.backups) as tmp:
        # consistent DB copy (reuses housekeeping.backup_db → sqlite backup API)
        db_copy = backup_db(paths.db, kind, dst=Path(tmp) / "bot.db")
        entries: list[tuple[str, Path]] = [("db/bot.db", db_copy)]
        for prefix, root in (("uploads/", paths.uploads), ("bot_assets/", paths.assets)):
            if root.is_dir():
                for f in sorted(root.rglob("*")):
                    if f.is_file() and not f.is_symlink():
                        entries.append((prefix + f.relative_to(root).as_posix(), f))
        envp = Path(tmp) / "env_public.txt"
        envp.write_text(env_public(paths.env), encoding="utf-8")
        entries.append(("config/env_public.txt", envp))
        files = {}
        total = 0
        for arc, src in entries:
            size = src.stat().st_size
            total += size
            files[arc] = {"sha256": _sha(src), "size": size}
        manifest = {
            "app": APP_ID, "format": FORMAT, "type": kind, "commit": _commit(),
            "created_at": now.isoformat(), "created_jalali": f"{_jalali(now)} {now.strftime('%H:%M')}",
            "created_by": str(actor or ""),
            "counts": {"files": len(files), "uploads": sum(a.startswith("uploads/") for a in files),
                       "bot_assets": sum(a.startswith("bot_assets/") for a in files),
                       "bytes": total, "db": _db_counts(db_copy)},
            "files": files,
        }
        part = target.with_suffix(".zip.part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for arc, src in entries:
                zf.write(src, arc)
        os.replace(part, target)
    logger.info("backup created: %s (%s)", target.name, kind)
    return target


# ---------------------------------------------------------------- validate / restore
def _safe_name(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or ":" in name.split("/")[0]:
        return False
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts[:-1]) or parts[-1] in (".", ".."):
        return False
    return name == "manifest.json" or name.startswith(ALLOWED_PREFIXES)


def validate(zip_path: Path) -> tuple[dict | None, str]:
    """→ (manifest, "") or (None, Persian error). Never extracts anything."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        return None, "فایل ZIP معتبر نیست."
    with zf:
        infos = zf.infolist()
        names = [i.filename for i in infos if not i.is_dir()]
        if "manifest.json" not in names:
            return None, "این فایل پشتیبان این ربات نیست (manifest.json ندارد)."
        bad = [n for n in names if not _safe_name(n)]
        if bad:
            return None, f"مسیر ناامن یا ناشناخته در ZIP: {bad[0]}"
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            return None, "حجم بازشدهٔ فایل بیش از حد مجاز است."
        try:
            man = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None, "manifest.json خراب است."
        if man.get("app") != APP_ID or int(man.get("format") or 0) != FORMAT:
            return None, "این فایل پشتیبان این ربات نیست (شناسهٔ برنامه/نسخهٔ قالب ناهمخوان)."
        listed = man.get("files") or {}
        if "db/bot.db" not in listed:
            return None, "پایگاه داده در پشتیبان نیست."
        if set(listed) != set(names) - {"manifest.json"}:
            return None, "فهرست فایل‌های manifest با محتوای ZIP یکی نیست."
        for n, meta in listed.items():
            h = hashlib.sha256()
            with zf.open(n) as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != meta.get("sha256"):
                return None, f"sha256 ناهمخوان: {n} (فایل دست‌کاری یا خراب شده است)."
        with zf.open("db/bot.db") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                return None, "فایل پایگاه داده در پشتیبان معتبر نیست."
    return man, ""


def summary_fa(man: dict, path: Path | None = None) -> str:
    c = man.get("counts") or {}
    db = c.get("db") or {}
    lines = [
        f"🗂 پشتیبان: {path.name if path else ''}".strip(),
        f"تاریخ: {man.get('created_jalali')} (به وقت تهران) — نوع: {TYPE_FA.get(man.get('type'), man.get('type'))}",
        f"نسخهٔ برنامه (commit): {man.get('commit')}",
        f"فایل‌ها: {c.get('files', 0)} (آپلودها {c.get('uploads', 0)}، دارایی‌های ربات {c.get('bot_assets', 0)}) — حجم {human_size(int(c.get('bytes') or 0))}",
    ]
    if db:
        lines.append("پایگاه داده: " + "، ".join(f"{k}={v}" for k, v in db.items()))
    return "\n".join(lines)


def restore(zip_path: Path, *, paths: Paths | None = None, actor: str = "") -> tuple[bool, str, Path | None]:
    """Validate → pre-restore backup → replace DB (sqlite backup API) + uploads + bot_assets → migrate.

    Returns (ok, Persian message, pre_restore_backup_path).
    """
    paths = paths or Paths()
    man, err = validate(zip_path)
    if not man:
        return False, err, None
    pre = create_backup(PRE_RESTORE, paths=paths, actor=actor)
    with tempfile.TemporaryDirectory(dir=paths.backups) as tmp:
        tmpd = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            for n in man["files"]:
                dest = (tmpd / n).resolve()
                if tmpd.resolve() not in dest.parents:
                    return False, f"مسیر ناامن: {n}", pre
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(n) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        # DB: copy INTO the live file with the backup API (safe while bot/web hold connections)
        src = sqlite3.connect(tmpd / "db" / "bot.db")
        try:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False, "پایگاه دادهٔ پشتیبان سالم نیست (integrity_check).", pre
            paths.db.parent.mkdir(parents=True, exist_ok=True)
            dst = sqlite3.connect(paths.db)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        for sub, live in (("uploads", paths.uploads), ("bot_assets", paths.assets)):
            new = tmpd / sub
            old = tmpd / f"_old_{sub}"
            if live.exists():
                shutil.move(str(live), old)
            if new.is_dir():
                shutil.move(str(new), live)
            else:
                live.mkdir(parents=True, exist_ok=True)
    # migrate schema to the running code version
    from db.models import Database

    Database(paths.db)
    logger.info("restore done from %s by %s (pre-restore %s)", zip_path.name, actor, pre.name)
    return True, f"✅ بازگردانی انجام شد.\n{summary_fa(man, zip_path)}\nپشتیبان پیش از بازگردانی: {pre.name}", pre


# ---------------------------------------------------------------- list / retention
def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def kind_of(p: Path) -> str:
    m = NAME_RE.match(p.name)
    if not m:
        return ""
    return {None: AUTO, "manual": MANUAL, "pre-restore": PRE_RESTORE}[m.group(3)]


def list_backups(paths: Paths | None = None) -> list[dict[str, Any]]:
    paths = paths or Paths()
    out = []
    if not paths.backups.is_dir():
        return out
    for p in paths.backups.glob("nasoz_backup_*.zip"):
        k = kind_of(p)
        if not k:
            continue
        st = p.stat()
        m = NAME_RE.match(p.name)
        hhmm = m.group(2)
        out.append({"name": p.name, "path": p, "kind": k, "kind_fa": TYPE_FA[k], "size": st.st_size,
                    "size_fa": human_size(st.st_size), "mtime": st.st_mtime,
                    "date_fa": f"{m.group(1).replace('-', '/')} {hhmm[:2]}:{hhmm[2:4]}"})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def resolve(name: str, paths: Paths | None = None) -> Path | None:
    """Only plain backup names inside data/backups (no traversal)."""
    paths = paths or Paths()
    if not NAME_RE.match(name or ""):
        return None
    p = paths.backups / name
    return p if p.is_file() else None


def prune(paths: Paths | None = None, *, now: float | None = None) -> list[str]:
    """Keep last 7 daily auto + newest auto of each of the last 4 weeks; manual/pre-restore
    are never auto-deleted within 30 days."""
    paths = paths or Paths()
    now = now or time.time()
    items = list_backups(paths)
    autos = [b for b in items if b["kind"] == AUTO]
    keep = {b["name"] for b in autos[:KEEP_DAILY]}
    weeks: dict[tuple, str] = {}
    for b in autos:  # newest first → first seen per ISO week
        wk = datetime.fromtimestamp(b["mtime"]).isocalendar()[:2]
        weeks.setdefault(wk, b["name"])
    keep |= set(list(weeks.values())[:KEEP_WEEKLY])
    removed = []
    for b in items:
        if b["kind"] == AUTO and b["name"] in keep:
            continue
        if b["kind"] != AUTO and now - b["mtime"] < KEEP_PROTECTED_DAYS * 86400:
            continue
        try:
            b["path"].unlink()
            removed.append(b["name"])
        except OSError as exc:
            logger.warning("backup prune %s: %s", b["name"], exc)
    return removed


# ---------------------------------------------------------------- nightly job (bot process)
def last_auto_status_fa(db: Any) -> str:
    at, st = db.get_setting(S_LAST_AUTO_AT), db.get_setting(S_LAST_AUTO_STATUS)
    if not at:
        return "آخرین پشتیبان خودکار: هنوز انجام نشده (هر شب ساعت ۰۲:۰۰ به وقت تهران)."
    return f"آخرین پشتیبان خودکار: {at} (به وقت تهران) — {st or ''}"


def owner_recipients(db: Any) -> list[dict]:
    """Who may receive a backup file (or its failure notice): active «مالک» users ONLY.
    Hard lock (1405/07/26): managers and other roles never get backup files, whatever
    the role-permission toggles say (services.permissions.LOCKED_OWNER_ONLY)."""
    from services import permissions as perm

    return [u for u in db.list_users(active_only=True) if perm.is_owner(u) and perm.can(u, perm.BACKUP)]


def nightly_tick(client: Any, db: Any, *, paths: Paths | None = None, now: datetime | None = None) -> str | None:
    """Call every minute from the bot loop. Runs once per Tehran day after 02:00; a bot that
    was down at 02:00 catches up on start. Failure → owner notified, retry after 30 min."""
    now = now or _tehran_now()
    if now.hour < NIGHTLY_HOUR:
        return None
    day = now.strftime("%Y-%m-%d")
    if db.get_setting(S_LAST_AUTO_DAY) == day:
        return None
    last_try = db.get_setting(S_LAST_ATTEMPT) or ""
    if last_try:
        try:
            if now - datetime.fromisoformat(last_try) < timedelta(minutes=30):
                return None
        except ValueError:
            pass
    db.set_setting(S_LAST_ATTEMPT, now.isoformat())
    owners = owner_recipients(db)
    stamp = f"{_jalali(now).replace('-', '/')} {now.strftime('%H:%M')}"
    try:
        path = create_backup(AUTO, paths=paths, actor="nightly")
        removed = prune(paths)
    except Exception as exc:  # noqa: BLE001
        logger.exception("nightly backup failed")
        db.set_setting(S_LAST_AUTO_STATUS, f"❌ ناموفق: {exc}")
        db.set_setting(S_LAST_AUTO_AT, stamp)
        for o in owners:
            try:
                client.send_message(o["bale_user_id"], f"❌ پشتیبان خودکار شبانه ناموفق بود: {exc}\nتلاش دوباره تا ۳۰ دقیقهٔ دیگر.")
            except Exception:  # noqa: BLE001
                pass
        return None
    db.set_setting(S_LAST_AUTO_DAY, day)
    db.set_setting(S_LAST_AUTO_AT, stamp)
    db.set_setting(S_LAST_AUTO_STATUS, f"✅ موفق — {path.name} ({human_size(path.stat().st_size)})")
    for o in owners:
        send_to_chat(client, o["bale_user_id"], path, caption=f"💾 پشتیبان خودکار شبانه — {stamp}"
                     + (f"\n(حذف طبق نگهداری: {len(removed)} فایل قدیمی)" if removed else ""))
    return path.name


def send_to_chat(client: Any, chat_id: Any, path: Path, *, caption: str) -> bool:
    """Send zip as document; too big for Bale → text with the web download path."""
    if path.stat().st_size > BALE_MAX_UPLOAD:
        client.send_message(chat_id, f"{caption}\n⚠ حجم فایل ({human_size(path.stat().st_size)}) بیش از سقف ارسال بله است؛"
                                     f" از پنل وب دانلود کنید: /settings/backups (فایل {path.name}).")
        return False
    try:
        client.send_document(chat_id, path, caption=caption)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("send backup failed: %s", exc)
        client.send_message(chat_id, f"{caption}\n⚠ ارسال فایل در بله ناموفق بود؛ از پنل وب دانلود کنید: /settings/backups ({path.name}).")
        return False

