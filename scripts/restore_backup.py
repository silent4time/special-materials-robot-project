#!/usr/bin/env python3
"""Server-side restore (used by `bash install.sh --restore <file.zip>`; services must be stopped).

--check only validates and prints the summary. Exit 0 = ok, 2 = rejected/failed."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str]) -> int:
    from services import backup as bk

    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("usage: restore_backup.py [--check] <nasoz_backup_….zip>", file=sys.stderr)
        return 2
    zp = Path(args[0]).expanduser().resolve()
    if not zp.is_file():
        print(f"✗ فایل پیدا نشد: {zp}", file=sys.stderr)
        return 2
    man, err = bk.validate(zp)
    if not man:
        print(f"✗ فایل رد شد: {err}", file=sys.stderr)
        return 2
    print(bk.summary_fa(man, zp))
    if "--check" in argv:
        return 0
    ok, text, pre = bk.restore(zp, actor="install.sh")
    print(text)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
