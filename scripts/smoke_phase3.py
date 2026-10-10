"""Phase-3 smoke (1405-07-18 spec items 19–20). Temp dirs/DBs only — never live data."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402  — temp DB/reports/uploads before config import

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = _smoke_env.SMOKE_TMP


def test_housekeeping() -> None:
    import config
    from services import housekeeping as hk

    assert str(config.REPORT_DIR).startswith(str(TMP)) and str(config.DATABASE_PATH).startswith(str(TMP))
    data = TMP / "hk_data"
    (data / "backups").mkdir(parents=True)
    hk.DATA_DIR, hk.BACKUP_DIR = data, data / "backups"
    now = time.time()
    for i in range(8):  # 8 legacy backups in data/, ages 0..70 days
        p = data / f"bot_backup_2026{i:02d}_x.db"
        p.write_bytes(b"x")
        os.utime(p, (now - i * 10 * 86400, now - i * 10 * 86400))
    (data / "backups" / "web_admin_credentials.txt").write_text("u")
    assert len(hk.consolidate_backups()) == 8 and not list(data.glob("bot_backup_*.db"))
    removed = {p.name for p in hk.prune_backups(now=now)}
    # newest 5 (0..40 d) kept; 50/60/70 d are >30 d and not newest-5 → removed
    assert removed == {"bot_backup_202605_x.db", "bot_backup_202606_x.db", "bot_backup_202607_x.db"}, removed
    assert (data / "backups" / "web_admin_credentials.txt").exists()
    rep = TMP / "hk_reports"
    (rep / "sub").mkdir(parents=True)
    old, new = rep / "sub" / "old.pdf", rep / "new.pdf"
    old.write_text("o"); new.write_text("n"); (rep / ".gitkeep").write_text("")
    os.utime(old, (now - 61 * 86400, now - 61 * 86400))
    os.utime(rep / ".gitkeep", (now - 99 * 86400, now - 99 * 86400))
    assert hk.purge_reports(root=rep, now=now) == 1 and new.exists() and (rep / ".gitkeep").exists()
    # rotating single log
    import logging
    from logging.handlers import RotatingFileHandler

    hk.DATA_DIR = TMP
    path = hk.setup_logging("smoke_bot")
    hs = logging.getLogger().handlers
    assert len(hs) == 1 and isinstance(hs[0], RotatingFileHandler)
    assert hs[0].maxBytes == 2 * 1024 * 1024 and hs[0].backupCount == 5 and path.parent == TMP
    # getUpdates read-timeouts are transient (WARNING, not ERROR)
    import main as bot_main
    from bot.bale_api import BaleAPIError

    assert bot_main._is_transient(BaleAPIError("getUpdates", "The read operation timed out"))
    assert not bot_main._is_transient(BaleAPIError("getUpdates", "Unauthorized"))
    logging.getLogger().handlers.clear()
    print("  19e housekeeping OK (backups keep 5/30d, reports 60d, rotating log, transient timeouts)")


def main() -> int:
    print("smoke phase3…")
    test_housekeeping()
    print("SMOKE_PHASE3_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
