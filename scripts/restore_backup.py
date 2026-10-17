#!/usr/bin/env python3
"""Server-side restore (used by `bash install.sh --restore <file.zip>`; services must be stopped).

--check only validates and prints the summary. Exit 0 = ok, 2 = rejected/failed."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# SSH terminals (PuTTY/CMD) don't render ZWNJ/zero-width chars and glue words together
# (e.g. "بک\u200cآپ" → "بکآپ"); the shared bot text in services/backup.py keeps them for Bale.
_ZW_SPACE = {0x200C: " ", 0x200B: " "}
_ZW_DROP = {c: None for c in (0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x00AD, 0x0654)}


def _tty(text: str) -> str:
    return str(text).translate({**_ZW_SPACE, **_ZW_DROP})


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
        print(_tty(f"✗ فایل رد شد: {err}"), file=sys.stderr)
        return 2
    print(_tty(bk.summary_fa(man, zp)))
    if "--check" in argv:
        return 0
    ok, text, pre = bk.restore(zp, actor="install.sh")
    print(_tty(text))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
