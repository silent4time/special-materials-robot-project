"""data/ housekeeping (phase 3 item 19e) — shared by bot (startup + daily) and scripts.

* logging: one rotating log file per process (data/bot.log, data/web.log; 5×2 MB);
* DB backups live in data/backups/: keep the newest 5 and anything ≤ 30 days old;
* generated reports/ files older than 60 days are purged (all are re-generated on
  demand from the DB; letterhead / assets are not under reports/).
Every function is best-effort and never raises.
"""
from __future__ import annotations

import logging
import shutil
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import BASE_DIR, REPORT_DIR

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 5
BACKUP_KEEP_N = 5
BACKUP_KEEP_DAYS = 30
REPORT_KEEP_DAYS = 60
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(name: str, *, console: bool = False, level: int = logging.INFO) -> Path:
    """Root logger → data/<name>.log (rotating). ``console`` also logs to stderr."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{name}.log"
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(sh)
    return path


def backup_path(phase: str) -> Path:
    """data/backups/bot_backup_<YYYYmmdd_HHMMSS>_<phase>.db (standing rule name)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"bot_backup_{time.strftime('%Y%m%d_%H%M%S')}_{phase}.db"


def backup_db(db_path: Path, phase: str) -> Path:
    """Consistent online copy via sqlite3 backup API (works while bot/web run)."""
    import sqlite3

    dst = backup_path(phase)
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
    finally:
        src.close()
    return dst


def consolidate_backups() -> list[Path]:
    """Move legacy data/bot_backup_*.db (and stray data/backups siblings) into data/backups/."""
    moved: list[Path] = []
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(DATA_DIR.glob("bot_backup_*.db")):
        dst = BACKUP_DIR / p.name
        if dst.exists():
            continue
        try:
            shutil.move(str(p), dst)
            moved.append(dst)
        except OSError as exc:
            logger.warning("backup move failed %s: %s", p.name, exc)
    return moved


def prune_backups(*, keep_n: int = BACKUP_KEEP_N, keep_days: int = BACKUP_KEEP_DAYS, now: float | None = None) -> list[Path]:
    """Delete data/backups/*.db that are BOTH older than keep_days AND not among the newest keep_n."""
    now = now or time.time()
    files = sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for i, p in enumerate(files):
        if i < keep_n:
            continue
        if now - p.stat().st_mtime > keep_days * 86400:
            try:
                p.unlink()
                removed.append(p)
            except OSError as exc:
                logger.warning("backup prune failed %s: %s", p.name, exc)
    return removed


def purge_reports(*, days: int = REPORT_KEEP_DAYS, root: Path | None = None, now: float | None = None) -> int:
    root = Path(root or REPORT_DIR)
    now = now or time.time()
    n = 0
    if not root.is_dir():
        return 0
    for p in root.rglob("*"):
        if p.is_file() and p.name != ".gitkeep" and now - p.stat().st_mtime > days * 86400:
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    # per-report private folders (services.file_names.unique_dir) left empty
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            if not any(d.iterdir()) and now - d.stat().st_mtime > days * 86400:
                d.rmdir()
        except OSError:
            pass
    return n


def run_all() -> dict:
    out: dict = {}
    try:
        out["moved"] = len(consolidate_backups())
        out["pruned"] = len(prune_backups())
        out["reports_purged"] = purge_reports()
    except Exception as exc:  # noqa: BLE001
        logger.warning("housekeeping failed: %s", exc)
    logger.info("housekeeping: %s", out)
    return out
