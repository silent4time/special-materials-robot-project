"""Report PDF downloads — denied for technicians (mirror bot analytics)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from bot.activity import log_activity
from db.models import Database
from web.deps import get_db, require_non_technician
from web.services import reports as report_svc
from web.services.data import frames_completeness, load_frames
from web.templating import render

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
@router.get("/")
async def reports_page(
    request: Request,
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    frames = load_frames(db, user)
    return render(
        request,
        "reports.html",
        {
            "user": user,
            "completeness": frames_completeness(frames),
            "site_stock_date": db.get_latest_site_stock_date(),
            "message": request.query_params.get("msg"),
            "error": request.query_params.get("err"),
        },
    )


@router.get("/remaining-critical")
async def remaining_critical(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, err = report_svc.generate_remaining_critical_pdf(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_remaining")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/surplus")
async def surplus(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, err = report_svc.generate_surplus_pdf(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_surplus")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/user-activity")
async def user_activity(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, err = report_svc.generate_user_activity_pdf(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_user_activity")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/monthly-summary")
async def monthly_summary(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, err = report_svc.generate_monthly_summary_web(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_monthly_summary")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
