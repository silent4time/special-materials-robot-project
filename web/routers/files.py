"""📤 ورود فایل‌ها (web) — same 4 inputs, same permission (file_inputs) and the SAME pipeline as
the bot: services.file_upload.process_upload (stock update / monthly consumption) and
services.main_goal_persist (production photo OCR / unified sequence log, via /reports/main-goal).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from bot.activity import resolve_display_name
from config import UPLOAD_DIR
from db.models import Database
from services import file_guides
from services import file_upload
from services import inbound_report as inbound_svc
from services import permissions as perm
from web.deps import get_db, require_feature
from web.templating import render

router = APIRouter(prefix="/files", tags=["files"])

_FLASH = "web_flash:"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def set_flash(db: Database, user: dict, **kw) -> None:
    db.set_setting(_FLASH + str(user["bale_user_id"]), json.dumps({k: v for k, v in kw.items() if v}, ensure_ascii=False))


def pop_flash(db: Database, user: dict) -> dict:
    key = _FLASH + str(user["bale_user_id"])
    raw = db.get_setting(key)
    if not raw:
        return {}
    db.clear_setting(key)
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def actor_fa(user: dict) -> str:
    return f"{resolve_display_name(user)} ({user['bale_user_id']})"


async def run_excel_upload(db: Database, user: dict, upload: UploadFile, pending: str, *, origin: str | None):
    """Save the browser upload exactly where the bot saves Bale downloads, then run the shared pipeline."""
    name = (upload.filename or "").strip()
    if not name.lower().endswith(".xlsx"):
        return file_upload.UploadOutcome(ok=False, kind="ext", pending=pending,
                                         text=f"فقط .xlsx پذیرفته می‌شود. (دریافت شد: {name or 'بدون‌نام'})")
    session = db.get_or_create_session(user["bale_user_id"])
    dest = UPLOAD_DIR / str(user["bale_user_id"]) / str(session["id"]) / f"{pending}.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return file_upload.process_upload(db, user, dest, pending, session=session, upload_origin=origin,
                                      actor_fa=actor_fa(user))


def _page(request: Request, db: Database, user: dict, *, status_code: int = 200, **kw):
    ctx = {
        "user": user,
        "inputs": [(k, file_guides.TITLES.get(k, k)) for k in file_guides.MAIN_INPUTS],
        "latest_inbound": inbound_svc.latest_report(db),
        **pop_flash(db, user),
        **{k: v for k, v in kw.items() if v is not None},
    }
    return render(request, "files.html", ctx, status_code=status_code)


@router.get("")
@router.get("/")
async def files_page(request: Request, user=Depends(require_feature(perm.FILE_INPUTS)), db: Database = Depends(get_db)):
    return _page(request, db, user)


async def _excel(request, db, user, file, pending):
    out = await run_excel_upload(db, user, file, pending, origin="upload")
    if not out.ok:
        return _page(request, db, user, error=out.text, status_code=400)
    inbound = out.inbound_report if (out.inbound_report or {}).get("id") else None
    return _page(request, db, user, message=f"✅ «{file_guides.TITLES.get(pending, pending)}» ثبت شد.",
                 summary=out.text, new_inbound=inbound)


@router.post("/stock")
async def upload_stock(request: Request, file: UploadFile = File(...),
                       user=Depends(require_feature(perm.FILE_INPUTS)), db: Database = Depends(get_db)):
    """«📥 به‌روزرسانی موجودی انبار» — stock-only merge + new known-code ids + inbound report."""
    return await _excel(request, db, user, file, "product_inventory")


@router.post("/monthly")
async def upload_monthly(request: Request, file: UploadFile = File(...),
                         user=Depends(require_feature(perm.FILE_INPUTS)), db: Database = Depends(get_db)):
    """«📥 مصرف ماهیانه اقلام»."""
    return await _excel(request, db, user, file, "monthly_consumption")


@router.get("/inbound/{report_id}.{ext}")
async def inbound_file(report_id: int, ext: str, user=Depends(require_feature(perm.FILE_INPUTS, perm.REPORT_INBOUND)),
                       db: Database = Depends(get_db)):
    """The bot sends the inbound report to the uploader right after a stock upload; web = same files."""
    report = inbound_svc.get_report(db, report_id)
    if not report or ext not in {"pdf", "xlsx"}:
        return RedirectResponse("/files", status_code=303)
    pdf, xlsx = inbound_svc.build_report_files(db, report)
    path = Path(pdf if ext == "pdf" else xlsx)
    return FileResponse(path, media_type="application/pdf" if ext == "pdf" else _XLSX, filename=path.name)
