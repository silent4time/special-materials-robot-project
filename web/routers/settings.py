"""تنظیم ورود وب + ویرایش منبع اصلی (shared services.main_source)."""
from __future__ import annotations

from services import user_errors

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
from services import main_source_approvals as msa
from web.auth_web import set_credential
from services import permissions as perm
from web.deps import get_db, require_admin_web, require_catalog_admin, require_feature
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
        "is_approver": msa.is_approver(user),
        "pending_requests": msa.list_pending(db) if msa.is_approver(user) else [],
        "needs_approval": msa.needs_approval(user),
        "category_codes": db.list_category_codes(active_only=True),
        "can_edit": perm.can(user, perm.MAIN_SOURCE_EDIT),
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
    user=Depends(require_feature(perm.MAIN_SOURCE_EDIT)),
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
        log_activity(
            db, user, "web_edit_main_source_record",
            item_id=str(result.get("id") or item_id),
            changes_fa=main_source_svc.changes_fa(result.get("changes") or []),
        )
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
    user=Depends(require_feature(perm.MAIN_SOURCE_EDIT)),
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
            raise ValueError("اولویت الزامی است؛ مقدار اولویت را تعیین کنید.")
        overwrite = str(form.get("overwrite") or "").strip() in {"1", "on", "true", "yes"}
        if msa.needs_approval(user):
            if not overwrite:
                frame = main_source_svc.load_primary_frame(db, bale_user_id=user["bale_user_id"])
                if frame is not None and main_source_svc.find_row_by_id(frame, record.get("id"))[0] is not None:
                    raise ValueError(f"شناسه «{record.get('id')}» از قبل در منبع اصلی هست؛ برای درخواست جایگزینی تیک جایگزینی را بزنید.")
            req_id = msa.submit(db, user, record, allow_update=overwrite)
            log_activity(db, user, "main_source_request_submitted", item_id=record.get("id") or "")
            msa.bale_send([u["bale_user_id"] for u in msa.approvers(db)],
                          msa.request_text_fa(msa.get(db, req_id)), msa.inline_markup(req_id))
            message = f"📨 درخواست #{req_id} ثبت شد؛ پس از تأیید مدیر/مالک اعمال می‌شود."
            return render(request, "settings_main_source.html",
                          _main_source_context(db, user, message=message), status_code=200)
        result = main_source_svc.add_row(
            db,
            record,
            bale_user_id=user["bale_user_id"],
            allow_update=overwrite,
        )
        log_activity(
            db, user, "web_add_main_source_record", item_id=str(result.get("id") or ""),
            action_fa="به‌روزرسانی" if result.get("action") == "updated" else "رکورد جدید",
        )
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


@router.post("/main-source/requests/{req_id}/{action}")
async def main_source_request_decide(
    request: Request,
    req_id: int,
    action: str,
    user=Depends(require_feature(perm.MAIN_SOURCE_EDIT)),
    db: Database = Depends(get_db),
):
    if action == "approve":
        req, text = msa.approve(db, req_id, user)
    elif action == "reject":
        req, text = msa.reject(db, req_id, user)
    else:
        req, text = None, "عملیات نامعتبر."
    ok = req is not None and req.get("decided_by") == str(user["bale_user_id"]) and text.startswith(("✅", "❌", "اعمال"))
    if ok:
        log_activity(db, user, "main_source_request_" + ("approved" if req["status"] == "approved" else "rejected"),
                     item_id=str((req.get("record") or {}).get("id") or ""))
        msa.bale_send([req["requester_id"]],
                      f"درخواست #{req['id']} شما برای «{(req.get('record') or {}).get('id')}»: {text}")
    return render(request, "settings_main_source.html",
                  _main_source_context(db, user, message=text if ok else None, error=None if ok else text),
                  status_code=200 if ok else 400)


@router.post("/main-source/upload")
async def main_source_upload(
    request: Request,
    user=Depends(require_feature(perm.MAIN_SOURCE_EDIT)),
    db: Database = Depends(get_db),
    file: UploadFile = File(...),
):
    """«📦 جایگزینی کامل منبع اصلی» — same shared pipeline as the bot (services.file_upload)."""
    from web.routers.files import run_excel_upload

    out = await run_excel_upload(db, user, file, "product_inventory", origin="main_source")
    return render(
        request,
        "settings_main_source.html",
        _main_source_context(db, user, message=out.text if out.ok else None, error=None if out.ok else out.text),
        status_code=200 if out.ok else 400,
    )


@router.get("/main-source/download.xlsx")
async def main_source_download(
    user=Depends(require_feature(perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT)),
    db: Database = Depends(get_db),
):
    """«📄 دانلود اکسل منبع اصلی» — same export as the bot."""
    from fastapi.responses import FileResponse, RedirectResponse

    from analytics.frames import load_primary_inventory
    from config import REPORT_DIR
    from excel.simple_report import export_dataframe_xlsx
    from services.file_names import fa_stem, unique_dir

    frame = load_primary_inventory(db, user)
    if frame is None or frame.empty:
        return RedirectResponse("/settings/main-source", status_code=303)
    out = unique_dir(REPORT_DIR, "main_source") / f"{fa_stem('منبع اصلی')}.xlsx"
    export_dataframe_xlsx(frame, out, columns=list(main_source_svc.INVENTORY_COLUMNS),
                          header_map=main_source_svc.FIELD_LABELS_FA, sheet_name="ریز اطلاعات")
    log_activity(db, user, "download_main_source")
    return FileResponse(out, filename=out.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/main-source/sibling-defaults")
async def main_source_sibling_defaults(
    code: str = "",
    item_id: str = "",
    user=Depends(require_feature(perm.MAIN_SOURCE_EDIT)),
    db: Database = Depends(get_db),
):
    """JSON for the add-record form: values shared by ALL rows of the 4-digit code (same rule as bot)."""
    from fastapi.responses import JSONResponse

    from excel.id_parse import contractor_or_company_for_id

    frame = main_source_svc.load_primary_frame(db, bale_user_id=user["bale_user_id"])
    prefill, differing = main_source_svc.sibling_defaults(frame, code)
    prefill = {k: (v.item() if hasattr(v, "item") else v) for k, v in prefill.items()}
    contractor = contractor_or_company_for_id(item_id) if item_id.strip() else ""
    return JSONResponse({
        "code": code, "known": code.strip() in db.active_category_code_set(),
        "prefill": prefill, "differing": differing,
        "differing_fa": [main_source_svc.FIELD_LABELS_FA.get(c, c) for c in differing],
        "contractor_or_company": contractor,
    })


@router.post("/main-source/categories/add")
async def main_source_category_add(
    request: Request,
    user=Depends(require_feature(perm.MAIN_SOURCE_EDIT)),
    db: Database = Depends(get_db),
):
    """«➕ افزودن کد دسته» (same db.add_category_code as bot)."""
    form = await request.form()
    try:
        row = db.add_category_code(str(form.get("code") or "").strip(),
                                   label=str(form.get("label") or "").strip() or None,
                                   created_by=user["bale_user_id"])
        log_activity(db, user, "add_category_code", code=row.get("code"))
        msg, err = f"✅ کد دسته «{row.get('code')}» ثبت شد.", None
    except (ValueError, KeyError) as exc:
        msg, err = None, str(exc)
    return render(request, "settings_main_source.html", _main_source_context(db, user, message=msg, error=err),
                  status_code=400 if err else 200)


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
            {"label": format_month_year(y, m), "ok": ok, "parts": [] if ok else status.parts_for(y, m)}
            for y, m, ok in status.required
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
    user=Depends(require_feature(perm.REMINDERS)),
    db: Database = Depends(get_db),
):
    return render(request, "settings_reminders.html", _reminder_context(db, user))


@router.post("/reminders")
async def reminders_save(
    request: Request,
    user=Depends(require_feature(perm.REMINDERS)),
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
    user=Depends(require_feature(perm.REMINDERS)),
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


# ---------------------------------------------------------------- 🔐 دسترسی نقش‌ها
def _perm_context(user: dict, role: str, *, message: str | None = None, error: str | None = None) -> dict:
    from config import ROLES

    if role not in perm.EDITABLE_ROLES:
        role = perm.EDITABLE_ROLES[0]
    return {
        "user": user,
        "roles": [(r, ROLES.get(r, r)) for r in perm.EDITABLE_ROLES],
        "role": role,
        "role_fa": ROLES.get(role, role),
        "rows": perm.matrix_rows(role),
        "matrix": perm.matrix_text_fa().split("\n"),
        "message": message,
        "error": error,
    }


@router.get("/permissions")
async def permissions_page(
    request: Request,
    role: str = "",
    user=Depends(require_feature(perm.ROLE_PERMISSIONS)),
):
    return render(request, "settings_permissions.html", _perm_context(user, role))


@router.post("/permissions")
async def permissions_save(
    request: Request,
    user=Depends(require_feature(perm.ROLE_PERMISSIONS)),
    db: Database = Depends(get_db),
):
    form = await request.form()
    role = str(form.get("role") or "")
    if role not in perm.EDITABLE_ROLES:
        return render(request, "settings_permissions.html", _perm_context(
            user, role, error="دسترسی‌های مالک قابل تغییر نیست." if role == "owner" else "نقش نامعتبر است."
        ), status_code=400)
    enabled = {str(v) for v in form.getlist("feature")}
    changed = 0
    current = perm.role_features(role)
    for f in perm.TOGGLABLE_FEATURES:
        want = f in enabled
        if want != (f in current):
            ok, msg = perm.set_permission(db, user, role, f, want)
            if not ok:
                return render(request, "settings_permissions.html", _perm_context(user, role, error=msg), status_code=400)
            changed += 1
    return render(request, "settings_permissions.html", _perm_context(
        user, role, message=f"ذخیره شد ({changed} تغییر) — فوراً در ربات و وب اعمال می‌شود."
    ))


@router.post("/permissions/reset")
async def permissions_reset(
    request: Request,
    role: str = Form(...),
    user=Depends(require_feature(perm.ROLE_PERMISSIONS)),
    db: Database = Depends(get_db),
):
    ok, msg = perm.reset_role(db, user, role)
    ctx = _perm_context(user, role, message=msg if ok else None, error=None if ok else msg)
    return render(request, "settings_permissions.html", ctx, status_code=200 if ok else 400)
