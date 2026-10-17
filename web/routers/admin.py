"""Web parity for bot «⚙️ تنظیمات»: 👥 کاربران, 🎨 ظاهر (دعوت/خوشامد/لوگو/سربرگ), 📣 گروه گزارش
موجودی, 🤖 دستیار صوتی (owner). Same db calls / rules as the bot (services.user_admin,
bot.settings_text, services.site_stock_notify, services.voice_assistant)."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse

from auth.rbac import require_owner, role_label
from bot.activity import log_activity
from bot.settings_text import PLACEHOLDER_HINT_INVITE, PLACEHOLDER_HINT_WELCOME, format_invite_text
from config import BOT_ASSETS_DIR, BOT_USERNAME, ROLES
from db.models import Database
from services import permissions as perm
from services import site_stock_notify
from services import user_admin
from web.deps import ForbiddenFa, current_user, get_db, require_feature
from web.templating import render

router = APIRouter(prefix="/settings", tags=["admin"])


# ------------------------------------------------------------------ 👥 کاربران
def _users_ctx(db, user, **kw):
    return {"user": user, "users": db.list_users(active_only=False), "role_label": role_label,
            "roles": [r for r in ROLES if r != "owner" or require_owner(user)], **kw}


@router.get("/users")
async def users_page(request: Request, user=Depends(require_feature(perm.USERS)), db: Database = Depends(get_db)):
    return render(request, "settings_users.html", _users_ctx(db, user))


@router.post("/users/invite")
async def users_invite(request: Request, user=Depends(require_feature(perm.USERS)), db: Database = Depends(get_db)):
    """«➕ اضافه کردن کاربر» = invite link (7 days), same as bot."""
    role = str((await request.form()).get("role") or "")
    if role == "owner" and not require_owner(user):
        return render(request, "settings_users.html", _users_ctx(db, user, error="فقط مالک می‌تواند نقش مالک بدهد."), status_code=400)
    try:
        inv = db.create_invite(role=role, scope=None, created_by=str(user["bale_user_id"]), expires_days=7)
    except ValueError as exc:
        return render(request, "settings_users.html", _users_ctx(db, user, error=str(exc)), status_code=400)
    log_activity(db, user, "user_add")
    return render(request, "settings_users.html", _users_ctx(
        db, user, message=f"لینک دعوت ساخته شد — نقش: {role_label(role)} — اعتبار ۷ روز.",
        invite_url=user_admin.invite_url(inv["token"], BOT_USERNAME),
        invite_text=format_invite_text(db.get_setting("invite_text"), role)))


@router.post("/users/{target_id}/role")
async def users_set_role(request: Request, target_id: str, user=Depends(require_feature(perm.USERS)),
                         db: Database = Depends(get_db)):
    role = str((await request.form()).get("role") or "")
    ok, err = user_admin.can_set_role(db, user, target_id, role)
    if ok and role not in ROLES:
        ok, err = False, "نقش نامعتبر."
    if not ok:
        return render(request, "settings_users.html", _users_ctx(db, user, error=err), status_code=400)
    upd = db.set_role(target_id, role)
    log_activity(db, user, "user_edit")
    return render(request, "settings_users.html", _users_ctx(db, user, message=f"✅ نقش به‌روز شد: {upd['bale_user_id']} → {role_label(upd['role'])}"))


@router.post("/users/{target_id}/delete")
async def users_delete(request: Request, target_id: str, user=Depends(require_feature(perm.USERS)),
                       db: Database = Depends(get_db)):
    ok, err = user_admin.can_deactivate(db, user, target_id)
    if not ok:
        return render(request, "settings_users.html", _users_ctx(db, user, error=err), status_code=400)
    db.deactivate_user(target_id)
    log_activity(db, user, "user_delete")
    return render(request, "settings_users.html", _users_ctx(db, user, message=f"✅ کاربر {target_id} غیرفعال شد."))


# ------------------------------------------------------------------ 🎨 ظاهر + 📣 گروه + 🤖 دستیار
def _bot_ctx(db, user, **kw):
    from services import voice_assistant as va

    files = {k: bool(db.get_setting(k) and Path(db.get_setting(k)).is_file())
             for k in ("logo_path", "letterhead_pdf", "invite_image_path", "welcome_image_path")}
    return {
        "user": user,
        "invite_text": db.get_setting("invite_text") or "",
        "welcome_text": db.get_setting("welcome_text") or "",
        "hint_invite": PLACEHOLDER_HINT_INVITE, "hint_welcome": PLACEHOLDER_HINT_WELCOME,
        "files": files,
        "stock_group": site_stock_notify.resolve_report_group_id(db),
        "stock_group_db": db.get_setting(site_stock_notify.SETTING_KEY),
        "is_owner": require_owner(user),
        "assistant_status": va.status_text_fa(db.get_setting(va.SETTING_ENABLED) == "1", db.get_setting(va.SETTING_MODEL))
        if require_owner(user) else "",
        **kw,
    }


def _need_any(user, *feats):
    if not perm.can_any(user, feats):
        raise ForbiddenFa("دسترسی ندارید.")


@router.get("/bot")
async def bot_settings(request: Request, user=Depends(current_user), db: Database = Depends(get_db)):
    _need_any(user, perm.APPEARANCE, perm.STOCK_GROUP)
    return render(request, "settings_bot.html", _bot_ctx(db, user))


@router.post("/bot/text/{which}")
async def bot_text(request: Request, which: str, user=Depends(require_feature(perm.APPEARANCE)),
                   db: Database = Depends(get_db)):
    if which not in {"invite", "welcome"}:
        raise ForbiddenFa("نامعتبر")
    raw = str((await request.form()).get("text") or "").strip()
    key = f"{which}_text"
    if raw:
        db.set_setting(key, raw, updated_by=str(user["bale_user_id"]))
    else:
        db.clear_setting(key, updated_by=str(user["bale_user_id"]))
    if which == "invite":
        log_activity(db, user, "settings_invite")
    return render(request, "settings_bot.html", _bot_ctx(db, user, message="✅ ذخیره شد."))


_ASSET = {"logo": ("logo_path", (".jpg", ".jpeg", ".png")), "letterhead": ("letterhead_pdf", (".pdf",)),
          "invite": ("invite_image_path", (".jpg", ".jpeg", ".png")),
          "welcome": ("welcome_image_path", (".jpg", ".jpeg", ".png"))}


@router.post("/bot/asset/{which}")
async def bot_asset(request: Request, which: str, file: UploadFile | None = File(None),
                    user=Depends(require_feature(perm.APPEARANCE)), db: Database = Depends(get_db)):
    """Upload (or clear with empty file) logo / PDF letterhead / invite & welcome images."""
    if which not in _ASSET:
        raise ForbiddenFa("نامعتبر")
    key, exts = _ASSET[which]
    uid = str(user["bale_user_id"])
    old = db.get_setting(key)
    if file is None or not file.filename:
        db.clear_setting(key, updated_by=uid)
        if old:
            Path(old).unlink(missing_ok=True)
        msg = "✅ حذف شد."
    else:
        ext = Path(file.filename).suffix.lower()
        if ext not in exts:
            return render(request, "settings_bot.html", _bot_ctx(db, user, error=f"فقط {'، '.join(exts)}"), status_code=400)
        BOT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        dest = BOT_ASSETS_DIR / (f"letterhead_{uid}.pdf" if which == "letterhead" else f"{which}_{uid}.jpg")
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        if old and Path(old).resolve() != dest.resolve():
            Path(old).unlink(missing_ok=True)
        db.set_setting(key, str(dest), updated_by=uid)
        msg = "✅ ذخیره شد."
    log_activity(db, user, {"logo": "settings_logo", "letterhead": "settings_letterhead"}.get(which, "settings_invite"))
    return render(request, "settings_bot.html", _bot_ctx(db, user, message=msg))


@router.post("/bot/stock-group/clear")
async def stock_group_clear(request: Request, user=Depends(require_feature(perm.STOCK_GROUP)),
                            db: Database = Depends(get_db)):
    """Setting the group stays in Bale (/set_stock_group inside the group, needs the chat id)."""
    db.clear_setting(site_stock_notify.SETTING_KEY, updated_by=str(user["bale_user_id"]))
    log_activity(db, user, "settings_stock_group")
    return render(request, "settings_bot.html", _bot_ctx(db, user, message="✅ شناسه گروه گزارش از تنظیمات حذف شد."))


@router.post("/bot/assistant")
async def assistant_toggle(request: Request, user=Depends(current_user), db: Database = Depends(get_db)):
    """Owner-only, same rule as bot: cannot enable while Whisper/Ollama are missing."""
    from services import voice_assistant as va

    if not require_owner(user):
        raise ForbiddenFa("فقط مالک.")
    enable = str((await request.form()).get("enable") or "") == "1"
    if enable and not va.is_installed():
        db.set_setting(va.SETTING_ENABLED, "0", updated_by=str(user["bale_user_id"]))
        return render(request, "settings_bot.html", _bot_ctx(db, user, error=va.NOT_INSTALLED_FA), status_code=400)
    db.set_setting(va.SETTING_ENABLED, "1" if enable else "0", updated_by=str(user["bale_user_id"]))
    log_activity(db, user, "assistant_enabled" if enable else "assistant_disabled")
    return render(request, "settings_bot.html", _bot_ctx(db, user, message="✅ ذخیره شد."))


# ------------------------------------------------------------------ 💾 بک‌آپ (same services.backup as bot)
def _bk_ctx(db, user, **kw):
    from services import backup as bk

    return {"user": user, "items": bk.list_backups(), "last_auto": bk.last_auto_status_fa(db),
            "is_owner": require_owner(user), **kw}


@router.get("/backups")
async def backups_page(request: Request, user=Depends(require_feature(perm.BACKUP)), db: Database = Depends(get_db)):
    return render(request, "settings_backups.html", _bk_ctx(db, user))


@router.post("/backups/create")
async def backups_create(request: Request, user=Depends(require_feature(perm.BACKUP)), db: Database = Depends(get_db)):
    from services import backup as bk

    try:
        path = bk.create_backup(bk.MANUAL, paths=bk.Paths(db=db.path), actor=str(user["bale_user_id"]))
    except Exception as exc:  # noqa: BLE001
        return render(request, "settings_backups.html", _bk_ctx(db, user, error=f"ساخت بک‌آپ ناموفق بود: {exc}"), status_code=500)
    log_activity(db, user, "backup_create", file=path.name)
    return render(request, "settings_backups.html", _bk_ctx(db, user, message=f"✅ بک‌آپ ساخته شد: {path.name}", new_name=path.name))


@router.get("/backups/file/{name}")
async def backups_download(name: str, user=Depends(require_feature(perm.BACKUP)), db: Database = Depends(get_db)):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from services import backup as bk

    path = bk.resolve(name)
    if not path:
        raise HTTPException(status_code=404)
    log_activity(db, user, "backup_download", file=path.name)
    return FileResponse(path, media_type="application/zip", filename=path.name)


def _incoming(user) -> Path:
    from services import backup as bk

    d = bk.Paths().backups / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"restore_web_{user['bale_user_id']}.zip"


@router.post("/backups/restore/upload")
async def backups_restore_upload(request: Request, file: UploadFile = File(...), user=Depends(current_user),
                                 db: Database = Depends(get_db)):
    """Owner only: upload → validate manifest/sha256 → summary (confirm step 1)."""
    from services import backup as bk

    if not require_owner(user):
        raise ForbiddenFa("بازگردانی فقط توسط مالک.")
    dest = _incoming(user)
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    man, err = bk.validate(dest)
    if not man:
        dest.unlink(missing_ok=True)
        return render(request, "settings_backups.html", _bk_ctx(db, user, error=f"⛔ فایل رد شد: {err}"), status_code=400)
    return render(request, "settings_backups.html", _bk_ctx(db, user, restore_summary=bk.summary_fa(man), restore_step=1))


@router.post("/backups/restore/confirm")
async def backups_restore_confirm(request: Request, user=Depends(current_user), db: Database = Depends(get_db)):
    """Step 2 (+3): owner confirms twice (checkbox + typed word) → pre-restore backup → restore."""
    from services import backup as bk

    if not require_owner(user):
        raise ForbiddenFa("بازگردانی فقط توسط مالک.")
    form = await request.form()
    dest = _incoming(user)
    if not dest.is_file():
        return render(request, "settings_backups.html", _bk_ctx(db, user, error="ابتدا فایل بک‌آپ را بارگذاری کنید."), status_code=400)
    if form.get("confirm1") != "1" or str(form.get("confirm2") or "").strip() != "بازگردانی":
        man, _ = bk.validate(dest)
        return render(request, "settings_backups.html", _bk_ctx(
            db, user, restore_summary=bk.summary_fa(man) if man else "", restore_step=1,
            error="برای تأیید، تیک را بزنید و کلمهٔ «بازگردانی» را تایپ کنید."), status_code=400)
    try:
        ok, text, _pre = bk.restore(dest, paths=bk.Paths(db=db.path), actor=str(user["bale_user_id"]))
    finally:
        dest.unlink(missing_ok=True)
    perm.invalidate()
    if ok:
        log_activity(db, user, "backup_restore")
    return render(request, "settings_backups.html", _bk_ctx(db, user, message=text if ok else None, error=None if ok else text),
                  status_code=200 if ok else 400)
