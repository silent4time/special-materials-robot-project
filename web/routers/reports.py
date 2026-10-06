"""Report PDF / Excel downloads — denied for technicians (mirror bot analytics)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from bot.activity import log_activity
from analytics.critical_items import RENO_LABEL_FA, RENO_MODES, normalize_reno_mode
from bot.jalali import PERSIAN_MONTH_NAMES, format_month_year, jalali_today
from db.models import Database
from web.deps import get_db, require_materials_user, require_non_technician
from web.services import reports as report_svc
from web.services.data import data_completeness
from web.templating import render

router = APIRouter(prefix="/reports", tags=["reports"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _critical_context(db: Database, request: Request) -> dict:
    today = jalali_today()
    try:
        year = int(request.query_params.get("jalali_year") or today.year)
    except ValueError:
        year = today.year
    try:
        month = int(request.query_params.get("jalali_month") or today.month)
    except ValueError:
        month = today.month
    if month < 1 or month > 12:
        month = today.month
    counts = db.get_monthly_tundish_counts(year, month)
    from analytics.critical_items import horizon_header_note
    from services.critical_items_report import month_counts

    basis = month_counts(db, year, month)
    basis_note = horizon_header_note(basis) if basis is not None else (
        "برای ۳ ماه منتهی به این ماه هیچ تعداد تاندیش ماهانه‌ای ثبت نشده است."
    )
    recent = []
    for r in db.list_monthly_tundish_counts(limit=6):
        recent.append(
            {
                **r,
                "label": format_month_year(
                    int(r["jalali_year"]), int(r["jalali_month"]), named=True
                ),
            }
        )
    return {
        "critical_year": year,
        "critical_month": month,
        "critical_reno": normalize_reno_mode(request.query_params.get("renovation") or "with"),
        "reno_modes": [(m, RENO_LABEL_FA[m]) for m in RENO_MODES],
        "counts": counts,
        "basis_note": basis_note,
        "recent_counts": recent,
        "month_names": [(n, PERSIAN_MONTH_NAMES[n]) for n in range(1, 13)],
    }


@router.get("")
@router.get("/")
async def reports_page(
    request: Request,
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    ctx = {
        "user": user,
        "completeness": data_completeness(db, user),
        "site_stock_date": db.get_latest_site_stock_date(),
        "message": request.query_params.get("msg"),
        "error": request.query_params.get("err"),
    }
    ctx.update(_critical_context(db, request))
    return render(request, "reports.html", ctx)


@router.post("/critical-items/counts")
async def save_critical_counts(
    request: Request,
    jalali_year: int = Form(...),
    jalali_month: int = Form(...),
    count_billet: int = Form(...),
    count_bloom: int = Form(...),
    count_slab: int = Form(...),
    user=Depends(require_materials_user),
    db: Database = Depends(get_db),
):
    try:
        db.upsert_monthly_tundish_counts(
            jalali_year=jalali_year,
            jalali_month=jalali_month,
            count_billet=count_billet,
            count_bloom=count_bloom,
            count_slab=count_slab,
            updated_by=user["bale_user_id"],
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/reports?err={quote(str(exc))}"
            f"&jalali_year={jalali_year}&jalali_month={jalali_month}",
            status_code=303,
        )
    log_activity(
        db,
        user,
        "critical_tundish_counts_saved",
        jalali_year=jalali_year,
        jalali_month=jalali_month,
    )
    label = format_month_year(jalali_year, jalali_month, named=True)
    return RedirectResponse(
        f"/reports?msg={quote('تعداد تاندیش برای ' + label + ' ذخیره شد.')}"
        f"&jalali_year={jalali_year}&jalali_month={jalali_month}",
        status_code=303,
    )


@router.get("/critical-items")
async def critical_items_pdf(
    request: Request,
    user=Depends(require_materials_user),
    db: Database = Depends(get_db),
):
    """PDF اقلام بحرانی. ``segment=company`` (default, primary) | ``contractor``;
    ``renovation=with`` («با نوسازی», default) | ``without`` («بدون نوسازی»)."""
    from services.critical_items_report import SEGMENT_LABEL_FA, SEGMENTS

    today = jalali_today()
    try:
        year = int(request.query_params.get("jalali_year") or today.year)
        month = int(request.query_params.get("jalali_month") or today.month)
    except ValueError:
        return RedirectResponse(
            f"/reports?err={quote('سال/ماه نامعتبر')}", status_code=303
        )
    segment = (request.query_params.get("segment") or "company").strip().lower()
    if segment not in SEGMENTS:
        segment = "company"
    reno = normalize_reno_mode(request.query_params.get("renovation") or "with")
    res = report_svc.generate_critical_items_files(
        db, user, jalali_year=year, jalali_month=month, reno_mode=reno
    )
    path = res.pdfs.get(segment) if not res.error else None
    err = res.error
    if not err and path is None:
        err = (
            f"قلم بحرانیِ «{SEGMENT_LABEL_FA[segment]}» "
            f"({res.reno_label}) با کسری (نیاز مثبت) در افق ۳/۶ ماه یافت نشد."
        )
    if err or not path:
        return RedirectResponse(
            f"/reports?err={quote(err or 'خطا')}"
            f"&jalali_year={year}&jalali_month={month}&renovation={reno}#critical-items",
            status_code=303,
        )
    log_activity(
        db,
        user,
        "report_critical_items" if segment == "company" else "report_critical_items_contractor",
        renovation=reno,
    )
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/critical-items.xlsx")
async def critical_items_xlsx(
    request: Request,
    user=Depends(require_materials_user),
    db: Database = Depends(get_db),
):
    today = jalali_today()
    try:
        year = int(request.query_params.get("jalali_year") or today.year)
        month = int(request.query_params.get("jalali_month") or today.month)
    except ValueError:
        return RedirectResponse(
            f"/reports?err={quote('سال/ماه نامعتبر')}", status_code=303
        )
    reno = normalize_reno_mode(request.query_params.get("renovation") or "with")
    res = report_svc.generate_critical_items_files(
        db, user, jalali_year=year, jalali_month=month, reno_mode=reno
    )
    path, err = res.xlsx, res.error
    if err or not path:
        return RedirectResponse(
            f"/reports?err={quote(err or 'خطا')}"
            f"&jalali_year={year}&jalali_month={month}",
            status_code=303,
        )
    log_activity(db, user, "report_critical_items_xlsx", renovation=reno)
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/remaining-critical")
async def remaining_critical(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_remaining_critical_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_remaining")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/remaining-critical.xlsx")
async def remaining_critical_xlsx(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_remaining_critical_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_remaining_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/surplus")
async def surplus(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_surplus_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_surplus")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/surplus.xlsx")
async def surplus_xlsx(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_surplus_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_surplus_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/user-activity")
async def user_activity(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_user_activity_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_user_activity")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/user-activity.xlsx")
async def user_activity_xlsx(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_user_activity_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_user_activity_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/monthly-summary")
async def monthly_summary(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_monthly_summary_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_monthly_summary")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/monthly-summary.xlsx")
async def monthly_summary_xlsx(
    user=Depends(require_non_technician),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_monthly_summary_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_monthly_summary_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)
