"""«📝 آمار تولید» — web mirror of the bot's text-only production-stats input.

Form (year/month + ccm1..ccm5 kg + total melts) → confirm-replace when the month
exists → list with edit / delete (confirmation). Shared: ``services.production_manual``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from bot.activity import log_activity
from db.models import Database
from services import main_goal_report as mg
from services import permissions as perm
from services import production_manual as pm
from web.deps import ForbiddenFa, current_user, get_db
from web.templating import render

router = APIRouter(prefix="/reports/main-goal/production-stats", tags=["production-stats"])


def require_ps(user=Depends(current_user)) -> dict:
    if not (mg.can_run(user) or perm.can(user, perm.FILE_INPUTS)):
        raise ForbiddenFa("دسترسی «ورود فایل‌ها» یا «هدف اصلی» ندارید.")
    return user


def _rows(db: Database) -> list[dict]:
    out = []
    for r in pm.list_months(db):
        d = pm.from_row(r)
        if d:
            out.append({"ym": pm.ym_text(d.year, d.month), "label": d.label, "data": d,
                        "tons": d.section_tons(), "jalali_date": r.get("jalali_date") or "",
                        "actor": r.get("actor_display_name") or ""})
    return out


def _page(request: Request, db: Database, user: dict, *, form: dict | None = None,
          status_code: int = 200, **kw):
    ctx = {
        "user": user,
        "fields": pm.FIELDS,
        "rows": _rows(db),
        "form": form or {},
        "fmt": pm.fmt_kg,
        "message": kw.get("message") or request.query_params.get("msg"),
        "error": kw.get("error"),
        "summary": kw.get("summary"),
        "confirm_replace": kw.get("confirm_replace", False),
        "confirm_delete": kw.get("confirm_delete"),
        "editing": kw.get("editing", False),
    }
    return render(request, "production_stats.html", ctx, status_code=status_code)


@router.get("")
@router.get("/")
async def page(request: Request, user=Depends(require_ps), db: Database = Depends(get_db)):
    form: dict = {}
    editing = False
    ym = pm.parse_year_month(request.query_params.get("edit") or "")
    if ym:
        row = pm.get_month(db, *ym)
        d = pm.from_row(row) if row else None
        if d:
            form = {"ym": pm.ym_text(*ym), **{k: pm.fmt_kg(d.kg.get(k)).replace(",", "") for k in pm.FIELD_KEYS[:5]},
                    "melts": pm.fmt_kg(d.melts).replace(",", "")}
            editing = True
    dym = pm.parse_year_month(request.query_params.get("delete") or "")
    confirm_delete = pm.ym_text(*dym) if dym and pm.get_month(db, *dym) else None
    return _page(request, db, user, form=form, editing=editing, confirm_delete=confirm_delete)


@router.post("")
@router.post("/")
async def save(request: Request, user=Depends(require_ps), db: Database = Depends(get_db)):
    f = await request.form()
    form = {k: str(f.get(k) or "").strip() for k in ["ym", *pm.FIELD_KEYS]}
    ym = pm.parse_year_month(form["ym"])
    if not ym:
        return _page(request, db, user, form=form, error="سال/ماه نامعتبر است (مثلاً 1405/07).", status_code=400)
    kg: dict[str, float] = {}
    for k, lbl, _s in pm.FIELDS:
        v = pm.parse_number(form[k])
        if v is None:
            return _page(request, db, user, form=form,
                         error=f"وزن محصول {lbl} نامعتبر است (عدد به کیلوگرم).", status_code=400)
        kg[k] = v
    melts = pm.parse_number(form["melts"], integer=True)
    if melts is None:
        return _page(request, db, user, form=form, error="تعداد کل ذوب‌ها باید عدد صحیح باشد.", status_code=400)
    data = pm.ManualProduction(ym[0], ym[1], kg, melts)
    if pm.get_month(db, *ym) and str(f.get("replace") or "") != "1":
        return _page(request, db, user, form=form, confirm_replace=True, summary=data.summary_fa(),
                     error=f"برای {pm.ym_text(*ym)} قبلاً آمار تولید ثبت شده است؛ برای جایگزینی تأیید کنید.")
    _row, replaced = pm.store(db, data, user=user, source="web")
    log_activity(db, user, "web_production_stats_store")
    return _page(request, db, user, message=pm.confirmation_fa(data, replaced).split("\n")[0],
                 summary=data.summary_fa())


@router.post("/delete")
async def delete(request: Request, user=Depends(require_ps), db: Database = Depends(get_db)):
    f = await request.form()
    ym = pm.parse_year_month(str(f.get("ym") or ""))
    if not ym or str(f.get("confirm") or "") != "1":
        return _page(request, db, user, error="حذف تأیید نشد.", status_code=400)
    ok = pm.delete(db, *ym)
    if ok:
        log_activity(db, user, "web_production_stats_delete")
    msg = f"آمار تولید {pm.ym_text(*ym)} حذف شد." if ok else "ماه یافت نشد."
    return RedirectResponse(f"/reports/main-goal/production-stats?msg={msg}", status_code=303)
