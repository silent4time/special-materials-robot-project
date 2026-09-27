#!/usr/bin/env python3
"""Seed web login for the existing Bale owner (first /start / try_claim_first_owner).

Does NOT create a separate web-owner role. Links username/password to users row
where role='owner'. Optional WEB_ADMIN_USERNAME / WEB_ADMIN_PASSWORD only when
no web_credentials exist yet.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import WEB_ADMIN_PASSWORD, WEB_ADMIN_USERNAME  # noqa: E402
from db.models import Database  # noqa: E402
from web.auth_web import generate_password, set_credential  # noqa: E402


def find_owner(db: Database) -> dict | None:
    owners = [u for u in db.list_users(active_only=True) if u.get("role") == "owner"]
    return owners[0] if owners else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed web credentials for existing owner")
    parser.add_argument("--username", default=WEB_ADMIN_USERNAME or "admin")
    parser.add_argument("--password", default=WEB_ADMIN_PASSWORD or "")
    parser.add_argument(
        "--write-file",
        default=str(ROOT / "data" / "web_admin_credentials.txt"),
        help="Write username/password here (mode 600); do not commit",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing owner web login")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = Database()
    owner = find_owner(db)
    if not owner:
        print("هیچ کاربر owner فعالی در users نیست. ابتدا در بله /start بزنید.", file=sys.stderr)
        return 1

    existing = db.get_web_credential_by_bale_id(owner["bale_user_id"])
    if existing and not args.force:
        if db.count_web_credentials() > 0 and not (WEB_ADMIN_USERNAME and WEB_ADMIN_PASSWORD):
            print(
                f"Owner already has web login username={existing.get('username')}. "
                "Use --force to reset."
            )
            return 0

    password = args.password or generate_password()
    username = (args.username or "admin").strip()

    if args.dry_run:
        print(
            f"DRY-RUN: would set web login for owner "
            f"bale_user_id={owner['bale_user_id']} "
            f"display={owner.get('display_name')} username={username}"
        )
        return 0

    set_credential(
        db,
        bale_user_id=owner["bale_user_id"],
        username=username,
        password=password,
    )
    out = Path(args.write_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"username={username}\n"
        f"password={password}\n"
        f"bale_user_id={owner['bale_user_id']}\n"
        f"display_name={owner.get('display_name') or ''}\n"
        f"role=owner\n",
        encoding="utf-8",
    )
    os.chmod(out, 0o600)
    print(f"Web admin seeded for owner {owner['bale_user_id']} username={username}")
    print(f"Credentials written to {out} (mode 600 — do not commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
