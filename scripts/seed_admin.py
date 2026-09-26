#!/usr/bin/env python3
"""Bootstrap / update the admin user from ADMIN_BALE_USER_ID."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import ADMIN_BALE_USER_ID  # noqa: E402
from db.models import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed admin manager user")
    parser.add_argument("--user-id", default=ADMIN_BALE_USER_ID, help="Bale user id")
    parser.add_argument("--name", default="مدیر سیستم")
    args = parser.parse_args()
    if not args.user_id:
        print("ADMIN_BALE_USER_ID یا --user-id لازم است.", file=sys.stderr)
        return 1
    db = Database()
    user = db.upsert_user(args.user_id, role="manager", display_name=args.name, scope=None)
    print(f"Admin OK: {user}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
