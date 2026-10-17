#!/usr/bin/env python3
"""Helpers for the installer's interactive menu (bash install.sh → منو).

  status            DB / backup facts for «وضعیت» (owners, admins, DB size, last + nightly backup)
  list              numbered backups in data/backups (newest first): name|size|type|date
  backup [--send]   manual full backup (services.backup) → path, size, sha256; --send → owners on Bale

Terminal output only: ZWNJ/zero-width chars are replaced (PuTTY/CMD glue words), and the bot
token is never printed (errors are redacted)."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_TR = {0x200C: " ", 0x200B: " ", **{c: None for c in (0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x00AD, 0x0654)}}


def out(text: object = "") -> None:
    s = str(text).translate(_TR)
    tok = os.getenv("BALE_BOT_TOKEN", "").strip()
    if tok:
        s = s.replace(tok, "<توکن مخفی>")
    print(s, flush=True)


def _db():
    from db.models import Database
    return Database()


def cmd_status() -> int:
    from config import DATABASE_PATH
    from services import backup as bk
    db_path = Path(DATABASE_PATH)
    if db_path.is_file():
        out(f"DB_SIZE={bk.human_size(db_path.stat().st_size)}")
        try:
            db = _db()
            users = db.list_users(active_only=True)
            owners = [str(u["bale_user_id"]) for u in users if u.get("role") == "owner"]
            admins = [str(u["bale_user_id"]) for u in users if u.get("role") == "manager"]
            out(f"OWNERS={'، '.join(owners) or '-'}")
            out(f"ADMINS={'، '.join(admins) or '-'}")
            out(f"USERS={len(users)}")
            out(f"NIGHTLY={bk.last_auto_status_fa(db)}")
        except Exception as exc:  # noqa: BLE001
            out(f"DB_ERROR={type(exc).__name__}")
    else:
        out("DB_SIZE=-")
    items = bk.list_backups()
    if items:
        b = items[0]
        when = datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M")
        out(f"LAST_BACKUP={b['name']} ({b['size_fa']}، {b['kind_fa']}، {when})")
    else:
        out("LAST_BACKUP=-")
    out(f"BACKUP_COUNT={len(items)}")
    return 0


def cmd_list() -> int:
    from services import backup as bk
    for b in bk.list_backups():
        out(f"{b['path']}|{b['size_fa']}|{b['kind_fa']}|{b['date_fa']}")
    return 0


def cmd_backup(send: bool) -> int:
    from services import backup as bk
    try:
        path = bk.create_backup(bk.MANUAL, actor="install.sh menu")
    except Exception as exc:  # noqa: BLE001
        out(f"✗ ساخت بک آپ ناموفق بود: {exc}")
        return 2
    out(f"✓ بک آپ ساخته شد: {path}")
    out(f"  حجم:   {bk.human_size(path.stat().st_size)}")
    out(f"  sha256: {bk._sha(path)}")
    if not send:
        return 0
    try:
        from bot.bale_api import BaleClient
        db = _db()
        owners = [u for u in db.list_users(active_only=True) if u.get("role") == "owner"]
        if not owners:
            out("⚠ هنوز مالکی در ربات ثبت نشده؛ ارسال در بله انجام نشد.")
            return 0
        client = BaleClient(timeout=120)
        sent = 0
        for o in owners:
            if bk.send_to_chat(client, o["bale_user_id"], path, caption=f"💾 بک آپ دستی (منوی سرور) — {path.name}"):
                sent += 1
        out(f"✓ برای {sent} مالک در بله فرستاده شد." if sent else "⚠ فایل در بله فرستاده نشد (حجم زیاد یا خطای شبکه)؛ از پنل وب دانلود کنید.")
    except Exception as exc:  # noqa: BLE001
        out(f"⚠ ارسال در بله ناموفق بود: {type(exc).__name__}: {exc}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "status":
        return cmd_status()
    if cmd == "list":
        return cmd_list()
    if cmd == "backup":
        return cmd_backup("--send" in argv)
    out(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
