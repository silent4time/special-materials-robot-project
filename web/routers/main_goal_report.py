"""گزارش هدف اصلی — web: ثبت ورودی (عکس تولید / اکسل تاندیش) + گزارش بازه از DB + سناریوها.

Thin wrapper over ``services.main_goal_report`` / ``main_goal_history`` / ``main_goal_persist``.
"""
from __future__ import annotations

from services import user_errors

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from analytics.frames import load_primary_inventory
from bot.activity import log_activity
from bot.jalali import tehran_now
from config import REPORT_DIR, UPLOAD_DIR, ensure_dirs
from db.models import Database
from services import main_goal_history as mgh
from services import main_goal_persist as mgp
from services import main_goal_production_ocr as mgocr
from services import main_goal_report as mg
from services import mandatory_reminders as rem
from web.deps import ForbiddenFa, current_user, get_db
from web.services.data import letterhead_path
from web.templating import render

router = APIRouter(prefix="/reports/main-goal", tags=["main-goal"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF = "application/pdf"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def require_main_goal(user=Depends(current_user)) -> dict:
    if not mg.can_run(user):
        raise ForbiddenFa("دسترسی گزارش هدف اصلی ندارید (مالک / مدیر / کاردان مسئول).")
    return user


def _ctx(
    db: Database,
    user: dict,
    *,
    message: str | None = None,
    error: str | None = None,
    summary: str | None = None,
    download_stem: str | None = None,
    ocr_preview: str | None = None,
) -> dict:
    months = mgh.load_history(db)
    try:
        rstatus = rem.compute_status(db, rem.load_config(db))
        missing = rstatus.missing_labels
    except Exception:  # noqa: BLE001
        missing = []
    completeness = mgp.month_completeness(db)
    return {
        "user": user,
        "title_fa": mg.TITLE_FA,
        "subtitle_fa": mg.SUBTITLE_FA,
        "file_kinds": [(k, mg.FILE_KINDS[k]) for k in mg.FILE_KIND_ORDER],
        "message": message,
        "error": error,
        "summary": summary,
        "download_stem": download_stem,
        "ocr_preview": ocr_preview,
        "recent": db.list_main_goal_reports(limit=8),
        "months": months,
        "completeness": completeness,
        "min_months": mgh.MIN_RECOMMENDED_MONTHS,
        "history_line": mgh.history_count_line(len(months)),
        "can_delete": mgh.can_delete_month(user),
        "missing_required": missing,
        "max_forecast": mgh.MAX_FORECAST_MONTHS,
        "ccm_note": "CCM1/2=اسلب، CCM3=بلوم، CCM4/5=بیلت",
        "sections_fa": mg.SECTION_LABEL_FA,
    }


def _page(request: Request, db: Database, user: dict, *, status_code: int = 200, **kw):
    return render(request, "main_goal_report.html", _ctx(db, user, **kw), status_code=status_code)


@router.get("")
@router.get("/")
async def page(
    request: Request,
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    return _page(
        request, db, user,
        message=request.query_params.get("msg"),
        error=request.query_params.get("err"),
    )


async def _save_upload(upload: UploadFile | None, dest: Path, *, allow_image: bool = False) -> str | None:
    if upload is None or not upload.filename:
        return None
    name = upload.filename
    lower = name.lower()
    if allow_image:
        if not (lower.endswith(".xlsx") or Path(lower).suffix in _IMAGE_EXTS):
            raise ValueError(f"فقط تصویر یا .xlsx: {name}")
    elif not lower.endswith(".xlsx"):
        raise ValueError(f"فقط .xlsx: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return name


def _inventory(db: Database, user: dict):
    try:
        return load_primary_inventory(db, user)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- production photo / excel
@router.post("/inputs/production-photo")
async def upload_production_photo(
    request: Request,
    photo: UploadFile = File(...),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    batch = tehran_now().strftime("%Y%m%d_%H%M%S")
    dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / f"web_prod_{batch}"
    suffix = Path(photo.filename or "production.png").suffix.lower() or ".png"
    if suffix not in _IMAGE_EXTS:
        return _page(request, db, user, error="فقط فایل تصویری (png/jpg/webp) بفرستید.", status_code=400)
    path = dest_dir / f"production{suffix}"
    try:
        fname = await _save_upload(photo, path, allow_image=True)
    except ValueError as exc:
        return _page(request, db, user, error=str(exc), status_code=400)
    ocr_res = mgocr.ocr_production_image(path)
    out = mgp.store_production_from_ocr(
        db, path, user=user, source="web", filename=fname, ocr_result=ocr_res
    )
    if out.tab_rejected:
        # Furnace / undeterminable tab → alarm only, nothing stored (same rule as bot).
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        log_activity(db, user, "web_main_goal_production_photo_rejected_tab")
        ev = ocr_res.tab_evidence or {}
        detail = (
            f"تشخیص: {mgocr.TAB_LABEL_FA.get(ocr_res.report_tab, ocr_res.report_tab)}"
            + (f" | {ocr_res.period_label}" if ocr_res.period_label else "")
            + (f" | نشانه‌های تب کوره: {'، '.join(ev.get('furnace') or [])}" if ev.get("furnace") else "")
        )
        return _page(
            request, db, user,
            error=out.error_fa or mgocr.ALARM_UNKNOWN_FA,
            summary=detail,
            status_code=400,
        )
    preview = mgocr.result_summary_fa(ocr_res)
    if ocr_res.ocr_raw_text:
        preview += "\n\n— متن خام OCR —\n" + ocr_res.ocr_raw_text[:1500]
    if not out.ok:
        return _page(
            request, db, user,
            error=(out.error_fa or "OCR ناموفق") + " — می‌توانید اصلاح دستی را در فرم زیر بزنید.",
            summary=out.summary or preview,
            ocr_preview=preview,
            status_code=400,
        )
    log_activity(db, user, "web_main_goal_store_production_ocr")
    miss = out.missing_parts
    extra = ("\n⚠ هنوز ناقص: " + "، ".join(miss)) if miss else "\n✅ ماه از نظر ورودی‌ها کامل است."
    return _page(
        request, db, user,
        message="آمار تولید از عکس ذخیره شد.",
        summary=out.summary + extra,
        ocr_preview=preview,
    )


@router.post("/inputs/production-excel")
async def upload_production_excel(
    request: Request,
    production: UploadFile = File(...),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    batch = tehran_now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / f"web_prod_{batch}" / "production.xlsx"
    try:
        fname = await _save_upload(production, dest)
    except ValueError as exc:
        return _page(request, db, user, error=str(exc), status_code=400)
    out = mgp.store_production_from_excel(db, dest, user=user, source="web", filename=fname)
    if not out.ok:
        return _page(request, db, user, error=out.error_fa, status_code=400)
    log_activity(db, user, "web_main_goal_store_production_xlsx")
    miss = out.missing_parts
    extra = ("\n⚠ هنوز ناقص: " + "، ".join(miss)) if miss else ""
    return _page(request, db, user, message="آمار تولید Excel ذخیره شد.", summary=out.summary + extra)


@router.post("/inputs/production-manual")
async def production_manual(
    request: Request,
    period_text: str = Form(...),
    slab: str = Form(""),
    bloom: str = Form(""),
    billet: str = Form(""),
    melt: str = Form(""),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    from bot.jalali import parse_month_year_token

    tok = parse_month_year_token(period_text)
    if not tok:
        return _page(request, db, user, error="ماه جلالی نامعتبر (مثلاً شهریور ۱۴۰۵).", status_code=400)
    year, month = tok
    def _num(raw):
        raw = (raw or "").strip()
        return mgh.parse_number(raw) if raw else None
    fixed = mgocr.apply_manual_corrections(
        None,
        year=year,
        month=month,
        slab_tons=_num(slab),
        bloom_tons=_num(bloom),
        billet_tons=_num(billet),
        melt_count=_num(melt),
    )
    if not fixed.ok:
        return _page(request, db, user, error=fixed.error_fa, status_code=400)
    dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / "manual"
    dest_dir.mkdir(parents=True, exist_ok=True)
    placeholder = dest_dir / f"{fixed.period_key}.txt"
    placeholder.write_text(period_text, encoding="utf-8")
    out = mgp.store_production_from_ocr(
        db, placeholder, user=user, source="web", filename=placeholder.name,
        ocr_result=fixed, manual_corrected=True,
    )
    if not out.ok:
        return _page(request, db, user, error=out.error_fa, status_code=400)
    log_activity(db, user, "web_main_goal_store_production_manual")
    return _page(request, db, user, message="تولید با اصلاح دستی ذخیره شد.", summary=out.summary)


@router.post("/inputs/consumption")
async def upload_consumption(
    request: Request,
    section: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    if section not in mgh.SECTIONS:
        return _page(request, db, user, error="بخش نامعتبر.", status_code=400)
    batch = tehran_now().strftime("%Y%m%d_%H%M%S")
    dest = (
        UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / f"web_cons_{batch}"
        / f"{section}_consumption.xlsx"
    )
    try:
        fname = await _save_upload(file, dest)
    except ValueError as exc:
        return _page(request, db, user, error=str(exc), status_code=400)
    from services import main_goal_sequences as seq
    parsed = seq.parse_sequence_excel(dest)
    if parsed.ok:
        out = mgp.store_sequences_from_excel(
            db, dest, user=user, source="web", filename=fname, section=section
        )
    else:
        out = mgp.store_consumption_from_excel(
            db, dest, section, user=user, source="web", filename=fname, inventory=_inventory(db, user)
        )
    if not out.ok:
        return _page(request, db, user, error=out.error_fa, status_code=400)
    log_activity(db, user, "web_main_goal_store_consumption")
    miss = out.missing_parts
    extra = ("\n⚠ هنوز ناقص: " + "، ".join(miss)) if miss else ""
    return _page(request, db, user, message="مصرف تاندیش ذخیره شد.", summary=out.summary + extra)


# ---------------------------------------------------------------- range report from DB
@router.post("/report/range")
async def report_range(
    request: Request,
    range_kind: str = Form("3"),
    custom_from: str = Form(""),
    custom_to: str = Form(""),
    confirm_partial: str = Form(""),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    from bot.jalali import parse_month_year_token

    if range_kind == "custom":
        a = parse_month_year_token(custom_from)
        b = parse_month_year_token(custom_to or custom_from)
        if not a or not b:
            return _page(request, db, user, error="بازه سفارشی: ماه شروع/پایان جلالی لازم است.", status_code=400)
        spec = mgp.range_custom(a, b)
    else:
        try:
            n = int(range_kind)
        except ValueError:
            n = 3
        if n not in (3, 6, 12):
            n = 3
        spec = mgp.range_last_n(n)

    months = mgh.load_history(db)
    selected, warns = mgp.filter_months_by_range(months, spec)
    if not selected:
        return _page(request, db, user, error="در این بازه داده‌ای در سابقهٔ ذخیره‌شده نیست.", status_code=400)
    if warns and confirm_partial not in {"1", "on", "true", "yes"}:
        return _page(
            request, db, user,
            error="بازه ناقص است — برای ادامه تیک تأیید را بزنید:\n" + "\n".join(warns),
            status_code=400,
        )
    model, result, _w = mgp.build_range_report(db, spec, inventory=_inventory(db, user))
    return _deliver(request, db, user, model, result, prefix="web_main_goal_range")


# ---------------------------------------------------------------- legacy 4-file month upload
@router.post("/run")
@router.post("/months/upload")
async def upload_month(
    request: Request,
    production: UploadFile = File(...),
    billet_consumption: UploadFile = File(...),
    bloom_consumption: UploadFile = File(...),
    slab_consumption: UploadFile = File(...),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    batch = tehran_now().strftime("%Y%m%d_%H%M%S")
    dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / f"web_{batch}"
    uploads = {
        "production": production,
        "billet_consumption": billet_consumption,
        "bloom_consumption": bloom_consumption,
        "slab_consumption": slab_consumption,
    }
    files: dict[str, Path] = {}
    filenames: dict[str, str] = {}
    try:
        for kind, up in uploads.items():
            path = dest_dir / f"{kind}.xlsx"
            fname = await _save_upload(up, path)
            if not fname:
                raise ValueError(f"فایل «{mg.FILE_KINDS[kind]}» الزامی است.")
            files[kind] = path
            filenames[kind] = fname
    except ValueError as exc:
        return _page(request, db, user, error=str(exc), status_code=400)
    except Exception as exc:  # noqa: BLE001
        return _page(request, db, user, error=user_errors.error_fa("ذخیره فایل ناموفق", exc), status_code=400)

    out = mgh.store_month_set(
        db, files, filenames=filenames, user=user, source="web", inventory=_inventory(db, user)
    )
    if not out.ok:
        return _page(
            request, db, user,
            error=(out.error_fa or "خطا") + "\nاین ماه ذخیره نشد.",
            status_code=400,
        )
    log_activity(db, user, "web_main_goal_store_month")
    n = len(db.list_main_goal_months())
    return _page(request, db, user, message="ماه ذخیره شد.", summary=out.text_fa(history_count=n))


@router.post("/months/bulk")
async def upload_bulk(
    request: Request,
    files: list[UploadFile] = File(...),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    batch = tehran_now().strftime("%Y%m%d_%H%M%S")
    dest_dir = UPLOAD_DIR / str(user["bale_user_id"]) / "main_goal" / f"web_bulk_{batch}"
    buckets: dict[str, dict] = {}
    lines: list[str] = []
    for i, up in enumerate(files or [], 1):
        try:
            fname = await _save_upload(up, dest_dir / f"in_{i:03d}.xlsx")
        except ValueError as exc:
            lines.append(f"⚠ {exc}")
            continue
        if not fname:
            continue
        path = dest_dir / f"in_{i:03d}.xlsx"
        kind, ksrc = mg.detect_file_kind(path, filename=fname)
        period, psrc = mg.detect_period(path, filename=fname)
        if not kind:
            lines.append(f"⚠ «{fname}»: نوع فایل تشخیص نشد ({ksrc})")
            continue
        if not period:
            lines.append(f"⚠ «{fname}»: ماه تشخیص نشد ({psrc})")
            continue
        b = buckets.setdefault(period.key(), {"label": period.label_fa(), "files": {}, "filenames": {}})
        b["files"][kind] = path
        b["filenames"][kind] = fname
        lines.append(f"• «{fname}» → {mg.FILE_KINDS[kind]} | {period.label_fa()}")

    stored, failed = [], []
    inv = _inventory(db, user)
    for _key, b in sorted(buckets.items()):
        missing = [mg.FILE_KINDS[k] for k in mg.FILE_KIND_ORDER if k not in b["files"]]
        if missing:
            failed.append(f"⏳ {b['label']}: ناقص — مانده: " + "، ".join(missing))
            continue
        out = mgh.store_month_set(db, b["files"], filenames=b["filenames"], user=user, source="web", inventory=inv)
        if out.ok:
            stored.append(b["label"] + (" (جایگزین)" if out.replaced else ""))
        else:
            failed.append(f"❌ {b['label']}: {out.error_fa}")
    if stored:
        log_activity(db, user, "web_main_goal_store_month")
    n = len(db.list_main_goal_months())
    summary = "\n".join(
        lines
        + [""]
        + ([f"✅ ذخیره شد: {'، '.join(stored)}"] if stored else ["هیچ ماه کاملی ذخیره نشد."])
        + failed
        + ["", mgh.history_count_line(n)]
    )
    return _page(
        request, db, user,
        message="آپلود گروهی انجام شد." if stored else None,
        error=None if stored else "هیچ ماه کاملی (۴ فایل) یافت نشد.",
        summary=summary,
        status_code=200 if stored else 400,
    )


@router.post("/months/delete")
async def delete_month_by_key(
    period_key: str = Form(...),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    """Delete by period_key (shared with bot) — ids differ between legacy/normalized rows."""
    if not mgh.can_delete_month(user):
        raise ForbiddenFa("حذف ماه از سابقه فقط برای مالک یا مدیر مجاز است.")
    months = {m.period_key: m for m in mgh.load_history(db)}
    m = months.get(period_key)
    if not m:
        raise ForbiddenFa("ماه یافت نشد.")
    db.delete_main_goal_period(period_key)
    log_activity(db, user, "main_goal_delete_month")
    from urllib.parse import quote

    return RedirectResponse(f"/reports/main-goal?msg={quote(f'ماه «{m.label}» حذف شد.')}", status_code=303)


@router.post("/months/{month_id}/delete")
async def delete_month(
    month_id: int,
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    if not mgh.can_delete_month(user):
        raise ForbiddenFa("حذف ماه از سابقه فقط برای مالک یا مدیر مجاز است.")
    row = db.get_main_goal_month(month_id)
    # also allow delete by production id when only normalized rows exist
    if not row:
        # try as production id
        prod = db.get_main_goal_production(month_id)
        if prod:
            db.delete_main_goal_inputs_by_key(prod["period_key"])
            label = prod.get("period_label") or month_id
            from urllib.parse import quote
            return RedirectResponse(f"/reports/main-goal?msg={quote(f'ماه «{label}» حذف شد.')}", status_code=303)
        raise ForbiddenFa("ماه یافت نشد.")
    db.delete_main_goal_month(month_id)
    log_activity(db, user, "main_goal_delete_month")
    from urllib.parse import quote

    label = (row or {}).get("period_label") or str(month_id)
    return RedirectResponse(f"/reports/main-goal?msg={quote(f'ماه «{label}» حذف شد.')}", status_code=303)


# ---------------------------------------------------------------- scenarios
def _deliver(request: Request, db: Database, user: dict, model, result, *, prefix: str):
    if not result.ok:
        return _page(request, db, user, error=result.error_fa, status_code=400)
    mgh.persist_scenario(db, result, model, user=user, source="web")
    log_activity(db, user, f"report_{prefix.removeprefix('web_')}")
    ensure_dirs()
    stem = mgh.file_stem_fa(result)
    mgh.export_scenario(result, stem=stem, report_dir=REPORT_DIR, letterhead_path=letterhead_path(db))
    return _page(request, db, user, message="گزارش ساخته شد.", summary=result.summary, download_stem=stem)


@router.post("/scenario/target")
async def scenario_target(
    request: Request,
    period_text: str = Form(""),
    billet: str = Form(""),
    bloom: str = Form(""),
    slab: str = Form(""),
    total: str = Form(""),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    targets: dict[str, float] = {}
    for key, raw in (("billet", billet), ("bloom", bloom), ("slab", slab), ("total", total)):
        raw = (raw or "").strip()
        if not raw:
            continue
        v = mgh.parse_number(raw)
        if v is None or v <= 0:
            return _page(request, db, user, error=f"تناژ «{mgh.SECTION_CHOICES_FA[key]}» باید عدد مثبت باشد.", status_code=400)
        targets[key] = v
    if not targets:
        return _page(request, db, user, error="حداقل یک تناژ هدف (بیلت/بلوم/اسلب/کل) وارد کنید.", status_code=400)
    model = mgh.build_history_model(mgh.load_history(db), _inventory(db, user))
    result = mgh.scenario_target(model, targets, period_text=period_text.strip())
    return _deliver(request, db, user, model, result, prefix="web_main_goal_target")


@router.post("/scenario/forecast")
async def scenario_forecast(
    request: Request,
    months_ahead: str = Form("6"),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    h = mgh.parse_horizon_months(months_ahead)
    n = int(round(h)) if h else 0
    model = mgh.build_history_model(mgh.load_history(db), _inventory(db, user))
    result = mgh.scenario_forecast(model, n)
    return _deliver(request, db, user, model, result, prefix="web_main_goal_forecast")


def _report_file(stem: str, suffix: str) -> Path:
    if "/" in stem or "\\" in stem or ".." in stem or not stem.startswith(("هدف_اصلی", "main_goal")):
        raise ForbiddenFa("فایل یافت نشد.")
    path = REPORT_DIR / f"{stem}{suffix}"
    if not path.is_file():
        raise ForbiddenFa("فایل یافت نشد.")
    return path


@router.get("/download/{stem}.pdf")
async def download_pdf(stem: str, user=Depends(require_main_goal)):
    path = _report_file(stem, ".pdf")
    return FileResponse(path, media_type=_PDF, filename=path.name)


@router.get("/download/{stem}.xlsx")
async def download_xlsx(stem: str, user=Depends(require_main_goal)):
    path = _report_file(stem, ".xlsx")
    return FileResponse(path, media_type=_XLSX, filename=path.name)
