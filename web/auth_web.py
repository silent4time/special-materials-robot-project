"""Web login credentials — maps username/password to existing users.bale_user_id.

Roles are never stored here; permission checks use auth.rbac on the users row.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any, Optional

from db.models import Database


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """scrypt hash as ``scrypt$<salt_hex>$<hash_hex>`` (stdlib, no extra deps)."""
    if not password:
        raise ValueError("رمز عبور خالی است.")
    salt_b = salt if salt is not None else os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_b,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt${salt_b.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, salt_hex, hash_hex = (password_hash or "").split("$", 2)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        salt_b = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_b,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return hmac.compare_digest(digest, expected)


def authenticate(db: Database, username: str, password: str) -> Optional[dict[str, Any]]:
    """Return active users row if username/password match a web_credentials link."""
    cred = db.get_web_credential_by_username(username)
    if not cred:
        return None
    if not cred.get("active"):
        return None
    if not verify_password(password, cred.get("password_hash") or ""):
        return None
    user = db.get_user(cred["bale_user_id"])
    if not user or not user.get("active"):
        return None
    return user


def set_credential(
    db: Database,
    *,
    bale_user_id: str | int,
    username: str,
    password: str,
) -> dict[str, Any]:
    """Create or update web login for an existing Bale users row."""
    user = db.get_user(bale_user_id)
    if not user:
        raise KeyError("کاربر بله یافت نشد.")
    if not user.get("active"):
        raise ValueError("کاربر غیرفعال است.")
    uname = (username or "").strip()
    if len(uname) < 3:
        raise ValueError("نام کاربری باید حداقل ۳ کاراکتر باشد.")
    if len(password or "") < 6:
        raise ValueError("رمز عبور باید حداقل ۶ کاراکتر باشد.")
    other = db.get_web_credential_by_username(uname)
    if other and str(other["bale_user_id"]) != str(bale_user_id):
        raise ValueError("این نام کاربری قبلاً استفاده شده است.")
    return db.upsert_web_credential(
        bale_user_id=bale_user_id,
        username=uname,
        password_hash=hash_password(password),
    )


def generate_password(length: int = 14) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
