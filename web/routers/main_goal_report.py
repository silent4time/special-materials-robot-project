"""گزارش هدف اصلی — web upload of 4 Excels + PDF/xlsx download.

Thin wrapper over ``services.main_goal_report`` (same as Bale flow).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from analytics.frames import load_primary_inventory
from bot.activity import log_activity
from bot.jalali import format_date, tehran_now
from config import REPORT_DIR, UPLOAD_DIR, ensure_dirs
from db.models import Database
from excel.simple_report import generate_simple_report_xlsx
from pdf.generator import generate_simple_report_pdf
from services import main_goal_report as mg
from web.deps import ForbiddenFa, current_user, get_db
from web.services.data import letterhead_path
from web.templating import render

router = APIRouter(prefix="/reports/main-goal", tags=["main-goal"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF = "application/pdf"


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
) -> dict:
    recent = []
    for r in db.list_main_goal_reports(limit=8):
        recent.append(r)
    return {
        "user": user,
        "title_fa": mg.TITLE_FA,
        "subtitle_fa": mg.SUBTITLE_FA,
        "file_kinds": [(k, mg.FILE_KINDS[k]) for k in mg.FILE_KIND_ORDER],
        "message": message,
        "error": error,
        "summary": summary,
        "recent": recent,
        "ccm_note": "CCM1/2=اسلب، CCM3=بلوم، CCM4/5=بیلت",
    }


@router.get("")
@router.get("/")
async def page(
    request: Request,
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    return render(request, "main_goal_report.html", _ctx(db, user, message=msg, error=err))


async def _save_upload(upload: UploadFile | None, dest: Path) -> str | None:
    if upload is None or not upload.filename:
        return None
    name = upload.filename
    if not name.lower().endswith(".xlsx"):
        raise ValueError(f"فقط .xlsx: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return name


@router.post("/run")
async def run_report(
    request: Request,
    production: UploadFile = File(...),
    billet_consumption: UploadFile = File(...),
    bloom_consumption: UploadFile = File(...),
    slab_consumption: UploadFile = File(...),
    target_tons: str = Form(""),
    user=Depends(require_main_goal),
    db: Database = Depends(get_db),
):
    tgt: float | None = None
    raw = (target_tons or "").strip()
    if raw:
        try:
            tgt = float(mg.normalize_digits(raw).replace(",", ""))
            if tgt <= 0:
                raise ValueError
        except ValueError:
            return render(
                request,
                "main_goal_report.html",
                _ctx(db, user, error="تناژ هدف باید عدد مثبت باشد."),
                status_code=400,
            )

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
        return render(
            request,
            "main_goal_report.html",
            _ctx(db, user, error=str(exc)),
            status_code=400,
        )
    except Exception as exc:  # noqa: BLE001
        return render(
            request,
            "main_goal_report.html",
            _ctx(db, user, error=f"ذخیره فایل ناموفق: {exc}"),
            status_code=400,
        )

    inv = None
    try:
        inv = load_primary_inventory(db, user)
    except Exception:
        pass

    result = mg.compute_main_goal(
        files, filenames=filenames, inventory=inv, target_tons=tgt
    )
    if not result.ok:
        return render(
            request,
            "main_goal_report.html",
            _ctx(db, user, error=result.error_fa),
            status_code=400,
        )

    file_meta = {
        k: {
            "path": str(files[k]),
            "filename": filenames.get(k),
            "period_source": result.period_sources.get(k),
        }
        for k in mg.FILE_KIND_ORDER
    }
    now = tehran_now()
    db.insert_main_goal_report(
        period_key=result.period.key() if result.period else None,
        period_label=result.period.label_fa() if result.period else None,
        period_json=json.dumps(
            {"period": result.period.__dict__ if result.period else None, "sources": result.period_sources},
            ensure_ascii=False,
        ),
        files_json=json.dumps(file_meta, ensure_ascii=False),
        results_json=mg.persist_payload(result, file_meta),
        summary_text=result.summary_text(),
        target_tons=result.target_tons,
        source="web",
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        created_at=mg.utc_now_iso(),
        created_at_tehran=now.isoformat(),
        jalali_date=format_date(now),
    )
    log_activity(db, user, "report_main_goal")

    if not result.rate_rows and (not result.production or result.production.total_tons <= 0):
        return render(
            request,
            "main_goal_report.html",
            _ctx(
                db,
                user,
                message="دادهٔ جدولی کافی نبود — فقط خلاصه متنی.",
                summary=result.summary_text(),
            ),
        )

    ensure_dirs()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    stem = f"web_main_goal_{stamp}"
    title = f"{mg.TITLE_FA} — {result.period.label_fa() if result.period else ''}"
    subtitle = mg.SUBTITLE_FA
    pdf_path = REPORT_DIR / f"{stem}.pdf"
    xlsx_path = REPORT_DIR / f"{stem}.xlsx"
    lh = letterhead_path(db)
    generate_simple_report_pdf(
        title,
        subtitle=subtitle,
        sections=result.sections,
        output_path=pdf_path,
        filename_stem=stem,
        letterhead_path=lh,
    )
    generate_simple_report_xlsx(
        title,
        subtitle=subtitle,
        sections=result.sections,
        output_path=xlsx_path,
        filename_stem=stem,
    )
    # Stash paths in a short-lived marker file keyed by stamp for download links
    marker = REPORT_DIR / f"{stem}.meta.json"
    marker.write_text(
        json.dumps({"pdf": str(pdf_path), "xlsx": str(xlsx_path), "stem": stem}, ensure_ascii=False),
        encoding="utf-8",
    )
    return render(
        request,
        "main_goal_report.html",
        {
            **_ctx(db, user, message="گزارش ساخته شد.", summary=result.summary_text()),
            "download_stem": stem,
        },
    )


@router.get("/download/{stem}.pdf")
async def download_pdf(stem: str, user=Depends(require_main_goal)):
    path = REPORT_DIR / f"{stem}.pdf"
    if not path.is_file() or "main_goal" not in stem:
        raise ForbiddenFa("فایل یافت نشد.")
    return FileResponse(path, media_type=_PDF, filename=path.name)


@router.get("/download/{stem}.xlsx")
async def download_xlsx(stem: str, user=Depends(require_main_goal)):
    path = REPORT_DIR / f"{stem}.xlsx"
    if not path.is_file() or "main_goal" not in stem:
        raise ForbiddenFa("فایل یافت نشد.")
    return FileResponse(path, media_type=_XLSX, filename=path.name)
