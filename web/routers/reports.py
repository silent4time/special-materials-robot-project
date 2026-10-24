"""Report PDF / Excel downloads — denied for technicians (mirror bot analytics)."""
from __future__ import annotations

from services import user_errors

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
    from services.critical_items_report import get_today_counts, today_counts_line

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
        "today_counts": get_today_counts(db),
        "today_counts_line": today_counts_line(db),
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
    _dl = request.query_params.get("daily_latest") or ""
    ctx["daily_latest"] = _dl if _dl.startswith("/reports/daily") else ""
    ctx["daily_latest_label"] = request.query_params.get("daily_latest_label") or ""
    ctx.update(_critical_context(db, request))
    from services import critical_report_archive as _cra

    ctx["critical_approved"] = _cra.list_approved(db, limit=20)
    ctx["critical_drafts"] = _cra.list_drafts(db, limit=10)
    # t221u: «گزارش اقلام ورودی به انبار» — same stored reports as the bot
    ctx["inbound_latest"] = inbound_svc.latest_report(db)
    ctx["inbound_table"] = inbound_svc.preview_table(ctx["inbound_latest"]) if ctx["inbound_latest"] else None
    ctx["inbound_notes"] = inbound_svc.rejected_breakdown_fa(ctx["inbound_latest"]) if ctx["inbound_latest"] else []
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
        return None, user_errors.error_fa("خطا در تولید گزارش اقلام ورودی", exc)
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
    count_slab: int = Form(...),
    count_billet: int = Form(...),
    count_bloom: int = Form(...),
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    """v1.2.1: monthly tundish counts «از امروز» — no year/month; dated by entry date."""
    from services.critical_items_report import save_today_counts

    try:
        saved = save_today_counts(db, slab=count_slab, billet=count_billet, bloom=count_bloom,
                                  user=user)
    except ValueError as exc:
        return RedirectResponse(f"/reports?err={quote(str(exc))}#critical", status_code=303)
    log_activity(db, user, "critical_tundish_counts_saved", entry_date=saved["entry_date"],
                 source="web")
    msg = f"تعداد تاندیش ماهانه از امروز {saved['entry_date']} ذخیره شد."
    return RedirectResponse(f"/reports?msg={quote(msg)}#critical", status_code=303)


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
            f"({res.reno_label}) با کسری (نیاز مثبت) در افق ۳/۶ ماه یافت نشد. "
            f"{res.rates_lines.get(segment, '')}"
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


# ------------------------------------------- 🚨 اقلام بحرانی: draft → توضیحات → تأیید → بایگانی
# Same workflow/storage as the bot (services.critical_report_archive): a draft started
# in the bot can be finished here and vice versa. Permission: report.critical.
def _crit_page(report_id: int, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    q = f"?msg={quote(msg)}" if msg else (f"?err={quote(err)}" if err else "")
    return RedirectResponse(f"/reports/critical-items/report/{int(report_id)}{q}", status_code=303)


@router.post("/critical-items/draft")
async def critical_items_draft(
    renovation: str = Form("with"),
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    from services import critical_report_archive as cra
    from web.services.reports import letterhead_path

    reno = normalize_reno_mode(renovation)
    try:
        report, res = cra.create_draft(db, user, reno_mode=reno, source="web",
                                       letterhead_path=letterhead_path(db))
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            f"/reports?err={quote(user_errors.error_fa('خطا در تولید گزارش اقلام بحرانی', exc))}"
            f"&renovation={reno}#critical-items", status_code=303)
    if report is None:
        return RedirectResponse(f"/reports?err={quote(res.error or 'خطا')}&renovation={reno}#critical-items",
                                status_code=303)
    log_activity(db, user, "report_critical_items_draft", report_id=int(report["id"]), renovation=reno,
                 source="web")
    return _crit_page(report["id"], msg="پیش‌نویس ساخته شد. توضیحات را بنویسید (اختیاری) و تأیید کنید.")


@router.get("/critical-items/report/{report_id}")
async def critical_items_report_page(
    report_id: int,
    request: Request,
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    from services import critical_report_archive as cra

    report = cra.get_report(db, report_id)
    if report is None or report["status"] == cra.STATUS_CANCELLED:
        return RedirectResponse(f"/reports?err={quote('گزارش اقلام بحرانی یافت نشد.')}#critical-items",
                                status_code=303)
    return render(request, "critical_report.html", {
        "user": user,
        "report": report,
        "sheets": cra.preview_rows(report),
        "is_draft": report["status"] == cra.STATUS_DRAFT,
        "kinds": [(k, cra.KIND_FA[k]) for k in cra.KINDS
                  if k == "xlsx" or any(sh.get("segment") == k and sh.get("rows") for sh in cra.sheets_of(report))],
        "max_lines": cra.MAX_NOTE_LINES,
        "message": request.query_params.get("msg"),
        "error": request.query_params.get("err"),
    })


@router.post("/critical-items/report/{report_id}/notes")
async def critical_items_report_notes(
    report_id: int,
    notes: str = Form(""),
    action: str = Form("save"),
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    """«ذخیرهٔ توضیحات» (action=save) or «تأیید و ذخیرهٔ نهایی» (action=approve; the
    textarea is saved first, so typed notes are never lost)."""
    from services import critical_report_archive as cra
    from web.services.reports import letterhead_path

    lh = letterhead_path(db)
    report = cra.get_report(db, report_id)
    if report and report["status"] == cra.STATUS_DRAFT and cra.clean_notes(notes) != (report.get("user_notes") or ""):
        _r, err = cra.set_notes(db, report_id, notes, user, letterhead_path=lh)
        if err:
            return _crit_page(report_id, err=err)
        log_activity(db, user, "report_critical_items_notes", report_id=int(report_id), source="web")
    if action == "approve":
        _r, err = cra.approve(db, report_id, user, letterhead_path=lh)
        if err:
            return _crit_page(report_id, err=err)
        log_activity(db, user, "report_critical_items_approved", report_id=int(report_id), source="web")
        return _crit_page(report_id, msg="گزارش تأیید و ذخیره شد؛ از فهرست «گزارش‌های تأییدشده» هم قابل دریافت است.")
    if action == "clear":
        _r, err = cra.set_notes(db, report_id, "", user, letterhead_path=lh)
        return _crit_page(report_id, err=err) if err else _crit_page(report_id, msg="توضیحات حذف شد.")
    if action == "cancel":
        _r, err = cra.cancel(db, report_id, user)
        if err:
            return _crit_page(report_id, err=err)
        log_activity(db, user, "report_critical_items_cancelled", report_id=int(report_id), source="web")
        return RedirectResponse(f"/reports?msg={quote(f'پیش‌نویس #{int(report_id)} لغو شد.')}#critical-items",
                                status_code=303)
    return _crit_page(report_id, msg="توضیحات ذخیره شد؛ پیش‌نویس به‌روز شد.")


@router.get("/critical-items/report/{report_id}/file/{kind}")
async def critical_items_report_file(
    report_id: int,
    kind: str,
    user=Depends(require_feature(perm.REPORT_CRITICAL)),
    db: Database = Depends(get_db),
):
    from services import critical_report_archive as cra
    from web.services.reports import letterhead_path

    report = cra.get_report(db, report_id)
    if report is None or report["status"] == cra.STATUS_CANCELLED or kind not in cra.KINDS:
        return RedirectResponse(f"/reports?err={quote('گزارش اقلام بحرانی یافت نشد.')}#critical-items",
                                status_code=303)
    path = cra.report_files(db, report, letterhead_path=letterhead_path(db)).get(kind)
    if path is None:
        return _crit_page(report_id, err="این بخش قلمی ندارد.")
    if report["status"] == cra.STATUS_APPROVED:
        log_activity(db, user, "report_critical_items_archive", report_id=int(report_id), source="web")
    media = _XLSX if kind == "xlsx" else "application/pdf"
    return FileResponse(path, media_type=media, filename=path.name)


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


def _daily_period(request: Request, db: Database, user: dict, section: str, action: str):
    """``period=current`` → calendar Jalali month (no data → message + link to the latest month
    with data); ``period=month&y=&m=`` → that month; otherwise the «N روز اخیر» window."""
    from services import data_months as dmo

    q = request.query_params
    period = (q.get("period") or "").strip()
    if period == "current":
        cur = dmo.resolve_current_month(db, user, "daily", section or None)
        if cur["has_data"]:
            return {"month": cur["ym"]}, None
        msg = cur["message"].replace("\n", " ")
        if cur.get("latest"):
            y, m = cur["latest"]
            link = f"{action}?period=month&y={y}&m={m}&section={quote(section or '')}&latest=1"
            return {}, RedirectResponse(
                f"/reports?err={quote(msg)}&daily_latest={quote(link)}&daily_latest_label="
                f"{quote(cur['offer_label'])}#daily", status_code=303)
        return {}, RedirectResponse(f"/reports?err={quote(msg)}#daily", status_code=303)
    if period == "month":
        try:
            ym = (int(q.get("y") or 0), int(q.get("m") or 0))
        except ValueError:
            ym = (0, 0)
        if not (1300 <= ym[0] <= 1500 and 1 <= ym[1] <= 12):
            return {}, RedirectResponse(f"/reports?err={quote('ماه نامعتبر است.')}#daily", status_code=303)
        note = (f"ماه جاری داده ندارد — آخرین ماه دارای داده: {dmo.month_name_fa(ym)}"
                if q.get("latest") else "")
        return {"month": ym, "basis_note": note}, None
    return {}, None


@router.get("/daily")
async def daily_pdf(
    request: Request,
    days: int = 30,
    section: str = "",
    user=Depends(require_feature(perm.REPORT_DAILY)),
    db: Database = Depends(get_db),
):
    kw, redirect = _daily_period(request, db, user, section, "/reports/daily")
    if redirect:
        return redirect
    path, _x, err = report_svc.generate_daily_files(db, user, days=max(1, min(days, 365)), section=section, **kw)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#daily", status_code=303)
    log_activity(db, user, "report_daily")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/daily.xlsx")
async def daily_xlsx(
    request: Request,
    days: int = 30,
    section: str = "",
    user=Depends(require_feature(perm.REPORT_DAILY)),
    db: Database = Depends(get_db),
):
    kw, redirect = _daily_period(request, db, user, section, "/reports/daily.xlsx")
    if redirect:
        return redirect
    _p, path, err = report_svc.generate_daily_files(db, user, days=max(1, min(days, 365)), section=section, **kw)
    if err or not path:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#daily", status_code=303)
    log_activity(db, user, "report_daily")
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


def _n_tundish_mix(request: Request, db: Database, user: dict):
    from services import n_tundish_report as nt
    from web.services.reports import letterhead_path

    q = request.query_params
    mix = {}
    for k in nt.MIX_ORDER:
        raw = nt.to_ascii_digits(q.get(k) or "0").strip() or "0"
        if not raw.isdigit():
            return None, "تعداد تاندیش هر بخش باید عدد صحیح (۰ یا بیشتر) باشد."
        mix[k] = int(raw)
    reno = normalize_reno_mode(q.get("renovation") or "with")
    res = nt.generate_mix_files(db, user, mix, reno, letterhead_path=letterhead_path(db))
    return res, res.error


@router.get("/n-tundish-mix")
async def n_tundish_mix_pdf(request: Request, user=Depends(require_feature(perm.REPORT_N_TUNDISH)), db: Database = Depends(get_db)):
    from services import n_tundish_report as nt

    res, err = _n_tundish_mix(request, db, user)
    if err or not res or not res.pdf:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#n-tundish", status_code=303)
    log_activity(db, user, "report_n_tundish_mix", mix=nt.mix_label(res.mix), renovation=res.reno_mode)
    return FileResponse(res.pdf, media_type="application/pdf", filename=res.pdf.name)


@router.get("/n-tundish-mix.xlsx")
async def n_tundish_mix_xlsx(request: Request, user=Depends(require_feature(perm.REPORT_N_TUNDISH)), db: Database = Depends(get_db)):
    from services import n_tundish_report as nt

    res, err = _n_tundish_mix(request, db, user)
    if err or not res or not res.xlsx:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#n-tundish", status_code=303)
    log_activity(db, user, "report_n_tundish_mix_xlsx", mix=nt.mix_label(res.mix), renovation=res.reno_mode)
    return FileResponse(res.xlsx, media_type=_XLSX, filename=res.xlsx.name)


# ---------------------------------------------------------------- 📅 گزارش مصرف بازه‌ای
def _period(request: Request, db: Database, user: dict | None = None):
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
    section = (q.get("section") or "").strip().lower() or None
    note = ""
    preset = (q.get("preset") or "").strip()
    if preset == "current":
        # «ماه جاری» = calendar month (bug 1405/08/02); no data → clear message, latest month offered
        from services import data_months as dmo

        cur = dmo.resolve_current_month(db, user, "period", section)
        if not cur["has_data"]:
            return None, cur["message"].replace("\n", " ")
        start_ym = end_ym = cur["ym"]
    elif preset or not (q.get("from_year") or q.get("from_month")):
        # T14b: preset / no range → latest N months THAT HAVE DATA
        from services import data_months as dmo

        got = dmo.resolve_preset_by_data(preset or "3m", dmo.report_months(db, user, "period", section))
        if got:
            start_ym, end_ym, _used, note = got
    start, end = month_year_to_gregorian_bounds(start_ym, end_ym)
    res = pc.generate_files(
        db, start, end, range_label=format_month_year_range(start_ym, end_ym),
        section=section, letterhead_path=letterhead_path(db), user=user, basis_note=note,
    )
    return res, res.error


@router.get("/period")
async def period_pdf(request: Request, user=Depends(require_feature(perm.REPORT_PERIOD)), db: Database = Depends(get_db)):
    res, err = _period(request, db, user)
    if err or not res or not res.pdf:
        return RedirectResponse(f"/reports?err={quote(err or 'خطا')}#period", status_code=303)
    log_activity(db, user, "report_period")
    return FileResponse(res.pdf, media_type="application/pdf", filename=res.pdf.name)


@router.get("/period.xlsx")
async def period_xlsx(request: Request, user=Depends(require_feature(perm.REPORT_PERIOD)), db: Database = Depends(get_db)):
    res, err = _period(request, db, user)
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
