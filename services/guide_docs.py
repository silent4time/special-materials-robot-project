"""v1.7.1 — PDFs sent to a new user after an invite is redeemed (bot + web share this).

Two kinds of document:
  * ``intro``  — «معرفی ربات مواد نسوز تاندیش» (same file for every role)
  * <role>     — «راهنمای کار با ربات» for one of ``config.ROLES``

Packaged defaults are committed in ``docs/default_guides/<target>.pdf``.  An owner/manager
can upload a replacement (bot «👥 کاربران» → «📘 معرفی ربات» / «📄 راهنمای نقش», or web
/settings/users); it is saved under ``BOT_ASSETS_DIR`` (``data/bot_assets``, never committed,
included in backups) and its path is kept in ``bot_settings``:
  intro → ``intro_pdf_path``      role → ``guide_pdf_<role>``
(plus ``<key>_name`` = the original upload file name, for display only).
If the setting is empty or its file is gone, the packaged default is used.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import config
from config import BASE_DIR, ROLES

logger = logging.getLogger(__name__)

INTRO = "intro"
DEFAULT_DIR = BASE_DIR / "docs" / "default_guides"
TARGETS: tuple[str, ...] = (INTRO, *ROLES.keys())
MAX_PDF_BYTES = 20 * 1024 * 1024  # Bale getFile/sendDocument stay safe below 20 MB
INTRO_TITLE_FA = "معرفی ربات مواد نسوز تاندیش"


def is_target(target: str | None) -> bool:
    return str(target or "") in TARGETS


def setting_key(target: str) -> str:
    if target == INTRO:
        return "intro_pdf_path"
    if target in ROLES:
        return f"guide_pdf_{target}"
    raise ValueError(f"unknown guide target: {target!r}")


def title_fa(target: str) -> str:
    """Short Persian title of the document (menus / web)."""
    if target == INTRO:
        return INTRO_TITLE_FA
    return f"راهنمای نقش {ROLES.get(target, target)}"


def caption_fa(target: str) -> str:
    """Caption used when the bot sends the document."""
    if target == INTRO:
        return INTRO_TITLE_FA
    return f"راهنمای کار با ربات — نقش {ROLES.get(target, target)}"


def download_name(target: str) -> str:
    """Persian file name shown to the receiver (bot document / web download)."""
    if target == INTRO:
        return "معرفی-ربات-مواد-نسوز-تاندیش.pdf"
    return f"راهنمای-{ROLES.get(target, target).replace(' ', '-')}.pdf"


def default_path(target: str) -> Path:
    setting_key(target)  # validates
    return DEFAULT_DIR / f"{target}.pdf"


def _assets_dir() -> Path:
    # read at call time: smokes / deployments may repoint config.BOT_ASSETS_DIR
    return Path(getattr(config, "BOT_ASSETS_DIR"))


def custom_path(db: Any, target: str) -> Path | None:
    """Uploaded replacement if configured and still on disk (else None)."""
    raw = db.get_setting(setting_key(target))
    if not raw:
        return None
    p = Path(raw)
    if p.is_file():
        return p
    alt = _assets_dir() / p.name  # e.g. restored backup on another install path
    if alt.is_file():
        return alt
    logger.warning("guide doc %s: configured file missing (%s); using packaged default", target, raw)
    return None


def resolve(db: Any, target: str) -> Path | None:
    """File to send for ``target``: uploaded replacement, else packaged default, else None."""
    p = custom_path(db, target)
    if p is not None:
        return p
    d = default_path(target)
    if d.is_file():
        return d
    logger.warning("guide doc %s: no uploaded file and packaged default missing (%s)", target, d)
    return None


def _size_fa(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} مگابایت"
    return f"{max(1, round(n / 1024))} کیلوبایت"


def status(db: Any, target: str) -> dict[str, Any]:
    """Display info (bot status text + web table)."""
    cp = custom_path(db, target)
    path = cp or resolve(db, target)
    if cp is not None:
        name = db.get_setting(setting_key(target) + "_name") or cp.name
        source_fa = "فایل بارگذاری‌شده"
    elif path is not None:
        name = path.name
        source_fa = "فایل پیش‌فرض"
    else:
        name = ""
        source_fa = "فایلی موجود نیست"
    size = path.stat().st_size if path is not None else 0
    return {
        "target": target,
        "title": title_fa(target),
        "is_custom": cp is not None,
        "exists": path is not None,
        "name": name,
        "size_fa": _size_fa(size) if size else "",
        "source_fa": source_fa,
    }


def status_text_fa(db: Any, target: str) -> str:
    s = status(db, target)
    if not s["exists"]:
        return f"{s['title']}\nوضعیت: فایلی موجود نیست."
    return f"{s['title']}\nوضعیت: {s['source_fa']} — {s['name']} ({s['size_fa']})"


def all_status(db: Any) -> list[dict[str, Any]]:
    return [status(db, t) for t in TARGETS]


def validate_pdf(path: Path, original_name: str = "") -> str | None:
    """Return a Persian error, or None when ``path`` is an acceptable PDF."""
    name = (original_name or path.name or "").strip()
    if not name.lower().endswith(".pdf"):
        return f"فقط فایل PDF پذیرفته می‌شود. (دریافت شد: {name or 'بدون‌نام'})"
    try:
        size = path.stat().st_size
    except OSError:
        return "فایل دریافت نشد."
    if size <= 0:
        return "فایل خالی است."
    if size > MAX_PDF_BYTES:
        return f"حجم فایل بیش از حد مجاز است (حداکثر {MAX_PDF_BYTES // (1024 * 1024)} مگابایت)."
    with path.open("rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        return "محتوای فایل PDF معتبر نیست."
    return None


def save_upload(db: Any, target: str, src: Path, original_name: str, updated_by: str | int | None) -> Path:
    """Validate ``src`` (a temp file) and make it the active document for ``target``.

    Raises ``ValueError`` (Persian message) when invalid; ``src`` is removed in that case.
    """
    key = setting_key(target)
    src = Path(src)
    err = validate_pdf(src, original_name)
    if err:
        src.unlink(missing_ok=True)
        raise ValueError(err)
    d = _assets_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"guide_{target}.pdf"
    old = db.get_setting(key)
    os.replace(src, dest)
    if old and Path(old).resolve() != dest.resolve():
        try:
            Path(old).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not delete old guide doc %s", old)
    db.set_setting(key, str(dest), updated_by=updated_by)
    db.set_setting(key + "_name", Path(original_name).name or dest.name, updated_by=updated_by)
    return dest


def restore_default(db: Any, target: str, updated_by: str | int | None) -> bool:
    """Drop the uploaded replacement (if any). Returns True if something was removed."""
    key = setting_key(target)
    old = db.get_setting(key)
    db.clear_setting(key, updated_by=updated_by)
    db.clear_setting(key + "_name", updated_by=updated_by)
    if old:
        try:
            Path(old).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not delete old guide doc %s", old)
    return bool(old)


def temp_upload_path(target: str, uid: str | int) -> Path:
    d = _assets_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f".upload_guide_{target}_{uid}.tmp"


def invite_documents(db: Any, role: str) -> list[tuple[str, Path | None]]:
    """[(target, path|None)] sent after an invite is redeemed: role guide, then intro."""
    out: list[tuple[str, Path | None]] = []
    if role in ROLES:
        out.append((role, resolve(db, role)))
    out.append((INTRO, resolve(db, INTRO)))
    return out
