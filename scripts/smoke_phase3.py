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


def test_cache_and_audit() -> None:
    import sqlite3

    sys.path.insert(0, str(ROOT / "scripts"))
    import smoke_phase2 as p2
    import services.main_source as ms
    from bot.activity import log_activity
    from db.models import Database
    from services import frame_cache

    ms.UPLOAD_DIR = TMP / "uploads_ms"
    db = Database(TMP / "audit.db")
    db.upsert_user("31", role="owner", display_name="A")
    db.upsert_user("32", role="responsible_officer", display_name="B")
    p1 = ms.persist_primary_frame(db, p2._inv_frame(5.0), bale_user_id="31")
    n_rows = lambda: sqlite3.connect(db.path).execute("SELECT COUNT(*) FROM extracted_datasets").fetchone()[0]
    assert n_rows() == 1
    frame_cache.clear()
    ms.load_primary_frame(db); ms.load_primary_frame(db)
    assert frame_cache.stats == {"hits": 1, "misses": 1}, frame_cache.stats
    item = p2._inv_frame().iloc[0]["id"]
    # same user edits 3× → same clean_path → extract row updated, not inserted
    for q in (6, 7, 8):
        res = ms.upsert_row(db, item, {"quantity": q, "unit": "کیلو"}, bale_user_id="31")
    assert n_rows() == 1, n_rows()
    df = ms.load_primary_frame(db)  # cache key changed with mtime → fresh value
    assert float(df.loc[df["id"] == item, "quantity"].iloc[0]) == 8
    assert res["changes"] == [{"field": "quantity", "before": "7", "after": "8"}], res["changes"]
    first = ms.upsert_row(db, item, {"quantity": 9, "unit": "عدد"}, bale_user_id="31")["changes"]
    assert {c["field"] for c in first} == {"quantity", "unit"}
    txt = ms.changes_fa(first)
    assert "→" in txt and "8" in txt and "9" in txt, txt
    log_activity(db, db.get_user("31"), "edit_main_source_record", item_id=item, changes_fa=txt)
    msg = sqlite3.connect(db.path).execute("SELECT message_fa FROM user_activity ORDER BY id DESC").fetchone()[0]
    assert item in msg and "8 → 9" in msg, msg
    # another user's edit → own file → new extract row (and becomes factory-wide latest)
    ms.upsert_row(db, item, {"quantity": 1}, bale_user_id="32")
    assert n_rows() == 2
    # timestamped snapshots, newest N kept
    for q in range(15):
        ms.upsert_row(db, item, {"quantity": 100 + q}, bale_user_id="32")
    snaps = list((Path(ms.load_primary_frame.__globals__["resolve_primary_inventory_path"](db)).parent / "snapshots").glob("edit_snapshot_*.xlsx"))
    assert len(snaps) == ms.SNAPSHOT_KEEP, len(snaps)
    assert n_rows() == 2
    # add_row activity phrase is Persian (was raw key before)
    from bot.activity import format_action_phrase

    assert "افزود" in format_action_phrase("add_main_source_record", item_id="X", action_fa="رکورد جدید")
    print("  19a/19b OK (frame cache hits, extract row updated in place, edit audit before→after, snapshots keep 10)")


def main() -> int:
    print("smoke phase3…")
    test_housekeeping()
    test_cache_and_audit()
    print("SMOKE_PHASE3_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
