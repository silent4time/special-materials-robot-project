"""Report PDF / Excel downloads — denied for technicians (mirror bot analytics)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from bot.activity import log_activity
from analytics.critical_items import RENO_LABEL_FA, RENO_MODES, normalize_reno_mode
from bot.jalali import PERSIAN_MONTH_NAMES, format_month_year, jalali_today
from db.models import Database
from services import permissions as perm
from web.deps import get_db, require_admin_web, require_feature, require_materials_user, require_non_technician
from services import inbound_report as inbound_svc
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
    from services.critical_items_report import critical_basis

    basis = critical_basis(db)  # report date = today
    basis_note = horizon_header_note(basis) if basis is not None else (
        "برای ۳ ماه کامل گذشته نه لاگ توالی تاندیش هست و نه تعداد تاندیش ماهانه."
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
    # t221u: «گزارش اقلام ورودی به انبار» — same stored reports as the bot
    ctx["inbound_latest"] = inbound_svc.latest_report(db)
    ctx["inbound_history"] = inbound_svc.list_reports(db, limit=20)
    try:
        from services import period_consumption as _pc

        ctx["period_availability"] = _pc.availability_text_fa(_pc.data_availability(db))
    except Exception:  # noqa: BLE001
        ctx["period_availability"] = ""
    return render(request, "reports.html", ctx)


def _inbound_file(db: Database, report_id: int, kind: str):
    report = inbound_svc.get_report(db, int(report_id))
    if not report:
        return None, "گزارش اقلام ورودی یافت نشد."
    try:
        pdf_path, xlsx_path = inbound_svc.build_report_files(db, report)
    except Exception as exc:  # noqa: BLE001
        return None, f"خطا در تولید گزارش اقلام ورودی: {exc}"
    return (pdf_path if kind == "pdf" else xlsx_path), None


@router.get("/inbound/{report_id}.pdf")
async def inbound_pdf(
    report_id: int,
    user=Depends(require_feature(perm.REPORT_INBOUND)),
    db: Database = Depends(get_db),
):
    path, err = _inbound_file(db, report_id, "pdf")
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#inbound", status_code=303)
    log_activity(db, user, "report_inbound", report_id=int(report_id), source="web")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/inbound/{report_id}.xlsx")
async def inbound_xlsx(
    report_id: int,
    user=Depends(require_feature(perm.REPORT_INBOUND)),
    db: Database = Depends(get_db),
):
    path, err = _inbound_file(db, report_id, "xlsx")
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#inbound", status_code=303)
    log_activity(db, user, "report_inbound_xlsx", report_id=int(report_id), source="web")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.post("/critical-items/counts")
async def save_critical_counts(
    request: Request,
    jalali_year: int = Form(...),
    jalali_month: int = Form(...),
    count_billet: int = Form(...),
    count_bloom: int = Form(...),
    count_slab: int = Form(...),
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
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
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    """PDF اقلام بحرانی. ``segment=company`` (default, primary) | ``contractor``;
    ``renovation=with`` («با نوسازی», default) | ``without`` («بدون نوسازی»)."""
    from services.critical_items_report import SEGMENT_LABEL_FA, SEGMENTS

    # Report date = today; basis = 3 complete months before it (no month selection;
    # legacy jalali_year/jalali_month query params are ignored).
    segment = (request.query_params.get("segment") or "company").strip().lower()
    if segment not in SEGMENTS:
        segment = "company"
    reno = normalize_reno_mode(request.query_params.get("renovation") or "with")
    res = report_svc.generate_critical_items_files(db, user, reno_mode=reno)
    path = res.pdfs.get(segment) if not res.error else None
    err = res.error
    if not err and path is None:
        err = (
            f"قلم بحرانیِ «{SEGMENT_LABEL_FA[segment]}» "
            f"({res.reno_label}) با کسری (نیاز مثبت) در افق ۳/۶ ماه یافت نشد."
        )
    if err or not path:
        return RedirectResponse(
            f"/reports?err={quote(err or 'خطا')}&renovation={reno}#critical-items",
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
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    reno = normalize_reno_mode(request.query_params.get("renovation") or "with")
    res = report_svc.generate_critical_items_files(db, user, reno_mode=reno)
    path, err = res.xlsx, res.error
    if err or not path:
        return RedirectResponse(
            f"/reports?err={quote(err or 'خطا')}&renovation={reno}#critical-items",
            status_code=303,
        )
    log_activity(db, user, "report_critical_items_xlsx", renovation=reno)
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/remaining-critical")
async def remaining_critical(
    user=Depends(require_feature(perm.REPORT_SHORT_COVER)),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_remaining_critical_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_remaining")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/remaining-critical.xlsx")
async def remaining_critical_xlsx(
    user=Depends(require_feature(perm.REPORT_SHORT_COVER)),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_remaining_critical_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_remaining_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/surplus")
async def surplus(
    user=Depends(require_feature(perm.REPORT_SURPLUS)),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_surplus_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_surplus")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/surplus.xlsx")
async def surplus_xlsx(
    user=Depends(require_feature(perm.REPORT_SURPLUS)),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_surplus_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_surplus_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/user-activity")
async def user_activity(
    user=Depends(require_feature(perm.USER_ACTIVITY)),  # moved to ⚙️ تنظیمات — owner/manager only
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_user_activity_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_user_activity")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/user-activity.xlsx")
async def user_activity_xlsx(
    user=Depends(require_feature(perm.USER_ACTIVITY)),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_user_activity_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_user_activity_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


@router.get("/monthly-summary")
async def monthly_summary(
    user=Depends(require_feature(perm.REPORT_MONTHLY_SUMMARY)),
    db: Database = Depends(get_db),
):
    path, _xlsx, err = report_svc.generate_monthly_summary_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_monthly_summary")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/monthly-summary.xlsx")
async def monthly_summary_xlsx(
    user=Depends(require_feature(perm.REPORT_MONTHLY_SUMMARY)),
    db: Database = Depends(get_db),
):
    _pdf, path, err = report_svc.generate_monthly_summary_files(db, user)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}", status_code=303)
    log_activity(db, user, "report_monthly_summary_xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)


# ---------------------------------------------------------------- 🧮 نیاز مواد برای N تاندیش
def _n_tundish(request: Request, db: Database, user: dict):
    from services import n_tundish_report as nt
    from web.services.reports import letterhead_path

    q = request.query_params
    section = (q.get("section") or "").strip().lower()
    n = nt.parse_count(q.get("n"))
    if section not in nt.SECTIONS or n is None:
        return None, "بخش (اسلب/بلوم/بیلت) و تعداد تاندیش (عدد صحیح مثبت) را وارد کنید."
    reno = normalize_reno_mode(q.get("renovation") or "with")
    res = nt.generate_files(db, user, section, n, reno, letterhead_path=letterhead_path(db))
    return res, res.error


@router.get("/n-tundish")
async def n_tundish_pdf(request: Request, user=Depends(require_feature(perm.REPORT_N_TUNDISH)), db: Database = Depends(get_db)):
    res, err = _n_tundish(request, db, user)
    if err or not res or not res.pdf:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#n-tundish", status_code=303)
    log_activity(db, user, "report_n_tundish", section=res.section, n=res.n, renovation=res.reno_mode)
    return FileResponse(res.pdf, media_type="application/pdf", filename=res.pdf.name)


@router.get("/n-tundish.xlsx")
async def n_tundish_xlsx(request: Request, user=Depends(require_feature(perm.REPORT_N_TUNDISH)), db: Database = Depends(get_db)):
    res, err = _n_tundish(request, db, user)
    if err or not res or not res.xlsx:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#n-tundish", status_code=303)
    log_activity(db, user, "report_n_tundish_xlsx", section=res.section, n=res.n, renovation=res.reno_mode)
    return FileResponse(res.xlsx, media_type=_XLSX, filename=res.xlsx.name)


# ---------------------------------------------------------------- 📅 گزارش مصرف بازه‌ای
def _period(request: Request, db: Database):
    from bot.jalali import format_month_year_range, month_year_to_gregorian_bounds
    from services import period_consumption as pc
    from web.services.reports import letterhead_path

    q = request.query_params
    today = jalali_today()
    try:
        fy, fm = int(q.get("from_year") or today.year), int(q.get("from_month") or today.month)
        ty, tm = int(q.get("to_year") or today.year), int(q.get("to_month") or today.month)
    except ValueError:
        return None, "بازهٔ ماه نامعتبر است."
    start_ym, end_ym = (fy, fm), (ty, tm)
    if ty * 12 + tm < fy * 12 + fm:
        start_ym, end_ym = end_ym, start_ym
    start, end = month_year_to_gregorian_bounds(start_ym, end_ym)
    section = (q.get("section") or "").strip().lower() or None
    res = pc.generate_files(
        db, start, end, range_label=format_month_year_range(start_ym, end_ym),
        section=section, letterhead_path=letterhead_path(db),
    )
    return res, res.error


@router.get("/period")
async def period_pdf(request: Request, user=Depends(require_feature(perm.REPORT_PERIOD)), db: Database = Depends(get_db)):
    res, err = _period(request, db)
    if err or not res or not res.pdf:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#period", status_code=303)
    log_activity(db, user, "report_period")
    return FileResponse(res.pdf, media_type="application/pdf", filename=res.pdf.name)


@router.get("/period.xlsx")
async def period_xlsx(request: Request, user=Depends(require_feature(perm.REPORT_PERIOD)), db: Database = Depends(get_db)):
    res, err = _period(request, db)
    if err or not res or not res.xlsx:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#period", status_code=303)
    log_activity(db, user, "report_period_xlsx")
    return FileResponse(res.xlsx, media_type=_XLSX, filename=res.xlsx.name)


# ---------------------------------------------------------------- 📊 گزارش جامع (shared core, 19f)
def _comprehensive(request: Request, db: Database, user: dict):
    from bot.jalali import format_month_year_range, month_year_to_gregorian_bounds
    from services import comprehensive_report as cr
    from web.services.reports import letterhead_path

    q = request.query_params
    today = jalali_today()
    try:
        fy, fm = int(q.get("from_year") or today.year), int(q.get("from_month") or today.month)
        ty, tm = int(q.get("to_year") or today.year), int(q.get("to_month") or today.month)
    except ValueError:
        return None, "بازهٔ ماه نامعتبر است."
    start_ym, end_ym = (fy, fm), (ty, tm)
    if ty * 12 + tm < fy * 12 + fm:
        start_ym, end_ym = end_ym, start_ym
    start, end = month_year_to_gregorian_bounds(start_ym, end_ym)
    section = (q.get("section") or "").strip().lower() or None
    if section not in (None, "slab", "bloom", "billet"):
        section = None
    res = cr.generate_files(
        db, user, start, end, range_label=format_month_year_range(start_ym, end_ym),
        section=section, letterhead_path=letterhead_path(db),
    )
    return res, res.error


@router.get("/comprehensive")
async def comprehensive_pdf(request: Request, user=Depends(require_feature(perm.REPORT_COMPREHENSIVE)), db: Database = Depends(get_db)):
    res, err = _comprehensive(request, db, user)
    if err or not res or not res.pdf:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#comprehensive", status_code=303)
    log_activity(db, user, "report_comprehensive")
    return FileResponse(res.pdf, media_type="application/pdf", filename=res.pdf.name)


@router.get("/comprehensive.xlsx")
async def comprehensive_xlsx(request: Request, user=Depends(require_feature(perm.REPORT_COMPREHENSIVE)), db: Database = Depends(get_db)):
    res, err = _comprehensive(request, db, user)
    if err or not res or not res.xlsx:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#comprehensive", status_code=303)
    log_activity(db, user, "report_comprehensive")
    return FileResponse(res.xlsx, media_type=_XLSX, filename=res.xlsx.name)
