"""Official mark: ``assets/logo.png``.

Bale's documented Bot API (https://docs.bale.ai/, method index checked
2026-11-10) does not include ``setMyProfilePhoto``, ``setMyPhoto``, or any
other method that sets the bot's own profile photo. ``setChatPhoto`` only
changes a group or channel picture; the same page says private-chat photos
cannot be changed, and the bot must be an administrator of that chat.

Startup therefore does not call those methods and does not report success.
The web panel uses the file as the favicon and header mark. The avatar itself
is set by hand in Bale's ``@botfather`` (steps in ``MANUAL_STEPS_FA``).

``PROFILE_PHOTO_API_SUPPORTED`` stays false until Bale documents a method.
The hash gate below is what startup will use if that flag is turned on: the
file is uploaded only when its sha256 differs from the last successful apply
(``data/updates/profile_photo.json``), and a failed call is logged without
aborting startup or recording the hash.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

# See module docstring. Do not flip this on without a documented Bale method.
PROFILE_PHOTO_API_SUPPORTED = False

MANUAL_STEPS_FA = (
    "عکس پروفایل بازو از API بله قابل تنظیم نیست (متد setMyProfilePhoto در مستندات docs.bale.ai نیست؛ "
    "setChatPhoto فقط عکس گروه و کانال است و چت خصوصی را عوض نمی‌کند).\n"
    "تنظیم دستی در بازوی پدر (@botfather) در بله:\n"
    "۱) گفتگو با @botfather را باز کنید.\n"
    "۲) دستور /setuserpic را بفرستید.\n"
    "۳) بازوی «ربات مدیریت مواد نسوز تاندیش» را انتخاب کنید.\n"
    "۴) فایل assets/logo.png را به‌صورت تصویر بفرستید (۱۰۲۴×۱۰۲۴).\n"
    "اگر آن دستور در منو نبود: /mybots ← انتخاب همان بازو ← ویرایش ← تصویر پروفایل، و همان فایل را بفرستید."
)

LOGO_UPLOAD_HINT_FA = (
    "عکس پروفایل بازو از API بله پشتیبانی نمی‌شود. "
    "در @botfather دستور /setuserpic را بفرستید، همین بازو را انتخاب کنید و این تصویر را بفرستید."
)


def logo_path(root: Path | None = None) -> Path:
    return Path(root or config.BASE_DIR) / "assets" / "logo.png"


def _state_path(root: Path) -> Path:
    return root / "data" / "updates" / "profile_photo.json"


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_official_logo(client: Any = None, *, root: Path | None = None) -> dict:
    """Idempotent. Never raises. Does not call Bale while the API is unsupported."""
    try:
        root = Path(root or config.BASE_DIR)
        path = logo_path(root)
        if not path.is_file():
            logger.info("official logo missing: %s", path)
            return {"supported": PROFILE_PHOTO_API_SUPPORTED, "applied": False, "reason": "missing"}
        if not PROFILE_PHOTO_API_SUPPORTED:
            logger.info(
                "Bale Bot API cannot set the bot avatar "
                "(no setMyProfilePhoto in docs.bale.ai; setChatPhoto is not the bot profile). "
                "Set assets/logo.png manually via @botfather /setuserpic."
            )
            return {"supported": False, "applied": False, "reason": "unsupported"}
        digest = file_sha256(path)
        state_file = _state_path(root)
        prev = str(_read_state(state_file).get("sha256") or "")
        if prev == digest:
            return {"supported": True, "applied": False, "reason": "unchanged"}
        if client is None or not hasattr(client, "try_set_my_photo"):
            logger.warning("profile photo client unavailable; avatar not changed")
            return {"supported": True, "applied": False, "reason": "no-client"}
        try:
            ok, detail = client.try_set_my_photo(path)
        except Exception as exc:  # noqa: BLE001 — startup must not fail
            logger.warning("set bot profile photo failed: %s", exc)
            return {"supported": True, "applied": False, "reason": "error"}
        if not ok:
            logger.warning("set bot profile photo rejected: %s", detail)
            return {"supported": True, "applied": False, "reason": str(detail or "rejected")}
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"sha256": digest, "method": str(detail or "")}, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("bot profile photo updated (%s)", detail)
        return {"supported": True, "applied": True, "reason": str(detail or "ok")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("official logo sync failed: %s", exc)
        return {"supported": False, "applied": False, "reason": "error"}
