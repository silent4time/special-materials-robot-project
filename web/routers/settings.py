"""تنظیم ورود وب + ویرایش منبع اصلی (shared services.main_source)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from auth.rbac import role_label
from bot.activity import log_activity
from bot.jalali import format_month_year
from db.models import Database
from excel.processor import (
    ExcelValidationError,
    extract_and_save_clean,
    load_excel,
    merge_clean_frames,
)
from services import main_source as main_source_svc
from web.auth_web import set_credential
from web.deps import get_db, require_admin_web, require_catalog_admin
from web.templating import render

router = APIRouter(prefix="/settings", tags=["settings"])


def _main_source_context(
    db: Database,
    user: dict,
    *,
    message: str | None = None,
    error: str | None = None,
) -> dict:
    frame = main_source_svc.load_primary_frame(db, bale_user_id=user["bale_user_id"])
    preview = []
    row_count = 0
    if frame is not None and not frame.empty:
        row_count = int(len(frame))
        preview = frame.head(40).fillna("").to_dict(orient="records")
    return {
        "user": user,
        "row_count": row_count,
        "preview": preview,
        "columns": list(main_source_svc.INVENTORY_COLUMNS),
        "labels": main_source_svc.FIELD_LABELS_FA,
        "message": message,
        "error": error,
    }


@router.get("/web-login")
async def web_login_settings(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
):
    return render(
        request,
        "settings_web.html",
        {
            "user": user,
            "users": db.list_users(active_only=True),
            "credentials": db.list_web_credentials(),
            "role_label": role_label,
            "message": None,
            "error": None,
        },
    )


@router.post("/web-login")
async def web_login_save(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
    bale_user_id: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    error = None
    message = None
    try:
        target = db.get_user(bale_user_id)
        if not target or not target.get("active"):
            raise KeyError("کاربر بله یافت نشد.")
        set_credential(
            db,
            bale_user_id=bale_user_id,
            username=username,
            password=password,
        )
        message = (
            f"✅ ورود وب برای «{target.get('display_name') or bale_user_id}» "
            f"(نقش: {role_label(target.get('role') or '')}) با نام کاربری «{username.strip()}» تنظیم شد."
        )
    except (KeyError, ValueError) as exc:
        error = str(exc)
    return render(
        request,
        "settings_web.html",
        {
            "user": user,
            "users": db.list_users(active_only=True),
            "credentials": db.list_web_credentials(),
            "role_label": role_label,
            "message": message,
            "error": error,
        },
        status_code=400 if error else 200,
    )


@router.get("/main-source")
async def main_source_settings(
    request: Request,
    user=Depends(require_catalog_admin),
    db: Database = Depends(get_db),
):
    return render(
        request,
        "settings_main_source.html",
        _main_source_context(db, user),
    )


@router.post("/main-source/edit")
async def main_source_edit(
    request: Request,
    user=Depends(require_catalog_admin),
    db: Database = Depends(get_db),
):
    form = await request.form()
    item_id = str(form.get("id") or "").strip()
    updates = {}
    for key in main_source_svc.INVENTORY_COLUMNS:
        if key == "id":
            continue
        raw = str(form.get(key) or "").strip()
        if raw:
            updates[key] = raw
    error = None
    message = None
    try:
        if not updates:
            raise ValueError("حداقل یک فیلد برای ویرایش پر کنید.")
        if not item_id:
            raise ValueError("شناسه مواد الزامی است.")
        result = main_source_svc.upsert_row(
            db, item_id, updates, bale_user_id=user["bale_user_id"]
        )
        log_activity(db, user, "web_edit_main_source_record")
        message = f"✅ رکورد «{result.get('id')}» به‌روز شد."
    except (KeyError, ValueError) as exc:
        error = str(exc)
    return render(
        request,
        "settings_main_source.html",
        _main_source_context(db, user, message=message, error=error),
        status_code=400 if error else 200,
    )


@router.post("/main-source/add")
async def main_source_add(
    request: Request,
    user=Depends(require_catalog_admin),
    db: Database = Depends(get_db),
):
    error = None
    message = None
    form = await request.form()
    try:
        record = {
            key: str(form.get(key) or "").strip()
            for key in main_source_svc.INVENTORY_COLUMNS
        }
        if not record.get("priority"):
            record["priority"] = "1"
        overwrite = str(form.get("overwrite") or "").strip() in {"1", "on", "true", "yes"}
        result = main_source_svc.add_row(
            db,
            record,
            bale_user_id=user["bale_user_id"],
            allow_update=overwrite,
        )
        log_activity(db, user, "web_add_main_source_record")
        action = "به‌روز" if result.get("action") == "updated" else "اضافه"
        message = f"✅ رکورد {action} شد («{result.get('id')}»)."
    except (KeyError, ValueError) as exc:
        error = str(exc)
    return render(
        request,
        "settings_main_source.html",
        _main_source_context(db, user, message=message, error=error),
        status_code=400 if error else 200,
    )


@router.post("/main-source/upload")
async def main_source_upload(
    request: Request,
    user=Depends(require_catalog_admin),
    db: Database = Depends(get_db),
    file: UploadFile = File(...),
):
    error = None
    message = None
    try:
        suffix = Path(file.filename or "upload.xlsx").suffix.lower() or ".xlsx"
        if suffix not in {".xlsx", ".xlsm"}:
            raise ValueError("فقط فایل Excel با پسوند .xlsx پذیرفته می‌شود.")
        allowlist = db.active_category_code_set()
        if not allowlist:
            raise ValueError(
                "لیست کدهای دسته‌بندی خالی است. ابتدا از ربات کد دسته اضافه کنید."
            )
        old = main_source_svc.load_primary_frame(db, bale_user_id=user["bale_user_id"])
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / f"product_inventory{suffix}"
            content = await file.read()
            raw_path.write_bytes(content)
            # Peek / normalize so FA headers (محل استفاده) map correctly
            _ = load_excel(raw_path, strict_tundish=False)
            result = extract_and_save_clean(
                raw_path,
                "product_inventory",
                category_allowlist=allowlist,
                clean_dir=Path(tmp) / "cleaned",
            )
            new_df = pd.read_excel(result.clean_path, engine="openpyxl")
            if old is not None and not old.empty:
                merged = merge_clean_frames(old, new_df, "product_inventory")
            else:
                merged = new_df
            path = main_source_svc.persist_primary_frame(
                db,
                merged,
                bale_user_id=user["bale_user_id"],
                raw_path=raw_path,
            )
            # Keep a copy of raw under uploads for audit
            try:
                session = db.get_or_create_session(user["bale_user_id"])
                audit = Path(path).parent.parent / "product_inventory_upload.xlsx"
                shutil.copy2(raw_path, audit)
            except Exception:
                pass
        log_activity(db, user, "web_upload_main_source")
        message = (
            f"✅ فایل منبع اصلی دریافت و همسان‌سازی شد "
            f"({int(len(merged))} ردیف)."
        )
    except (ExcelValidationError, ValueError, KeyError) as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"آپلود ناموفق بود: {exc}"
    return render(
        request,
        "settings_main_source.html",
        _main_source_context(db, user, message=message, error=error),
        status_code=400 if error else 200,
    )


# ---------------------------------------------------------------- یادآور گزارش‌های الزامی
def _reminder_context(
    db: Database,
    user: dict,
    *,
    message: str | None = None,
    error: str | None = None,
) -> dict:
    from config import ROLES
    from services import mandatory_reminders as rem

    cfg = rem.load_config(db)
    status = rem.compute_status(db, cfg)
    state = rem.load_state(db)
    return {
        "user": user,
        "cfg": cfg,
        "roles": ROLES,
        "users": db.list_users(active_only=True),
        "recipients": rem.resolve_recipients(db, cfg),
        "status": status,
        "required": [
            {"label": format_month_year(y, m), "ok": ok} for y, m, ok in status.required
        ],
        "preview": rem.build_reminder_text(status),
        "field_labels": rem.FIELD_LABELS_FA,
        "limits": rem.LIMITS,
        "last_result": state.get("last_result"),
        "force_pending": bool(db.get_setting(rem.FORCE_KEY)),
        "role_label": role_label,
        "message": message,
        "error": error,
    }


@router.get("/reminders")
async def reminders_page(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
):
    return render(request, "settings_reminders.html", _reminder_context(db, user))


@router.post("/reminders")
async def reminders_save(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
):
    from services import mandatory_reminders as rem

    form = await request.form()
    clean, errors = rem.validate_numbers({k: form.get(k) for k in rem.LIMITS if form.get(k) is not None})
    if errors:
        return render(
            request,
            "settings_reminders.html",
            _reminder_context(db, user, error="\n".join(errors)),
            status_code=400,
        )
    cfg = rem.load_config(db)
    cfg.update(clean)
    cfg["enabled"] = form.get("enabled") in {"1", "on", "true"}
    cfg["roles"] = [str(r) for r in form.getlist("roles")]
    cfg["user_ids"] = [str(u) for u in form.getlist("user_ids")]
    extra = str(form.get("extra_ids") or "").strip()
    if extra:
        import re as _re

        for tok in _re.findall(r"\d+", extra.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))):
            if tok not in cfg["user_ids"]:
                cfg["user_ids"].append(tok)
    rem.save_config(db, cfg, updated_by=user["bale_user_id"])
    log_activity(db, user, "settings_reminders")
    return render(request, "settings_reminders.html", _reminder_context(db, user, message="✅ تنظیمات یادآور ذخیره شد."))


@router.post("/reminders/send-now")
async def reminders_send_now(
    request: Request,
    user=Depends(require_admin_web),
    db: Database = Depends(get_db),
):
    from services import mandatory_reminders as rem

    cfg = rem.load_config(db)
    if not rem.resolve_recipients(db, cfg):
        return render(
            request,
            "settings_reminders.html",
            _reminder_context(db, user, error="هیچ دریافت‌کننده‌ای تنظیم نشده است."),
            status_code=400,
        )
    rem.request_force_send(db, requested_by=user["bale_user_id"])
    log_activity(db, user, "reminder_send_now")
    return render(
        request,
        "settings_reminders.html",
        _reminder_context(db, user, message="📨 درخواست ارسال ثبت شد؛ ربات بله حداکثر تا حدود یک دقیقه دیگر یادآور را می‌فرستد."),
    )
