"""گزارش تاندیش بعد از ریخته‌گری — web form + settings CRUD.

Thin wrapper over ``services.tundish_report`` (same validation / parsing /
persistence as the Bale flow in ``bot.tundish_report_flow``).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from bot.activity import log_activity
from db.models import Database
from services import tundish_report as tr
from web.deps import ForbiddenFa, current_user, get_db
from web.templating import render

router = APIRouter(prefix="/tundish-report", tags=["tundish-report"])


def require_entry(user=Depends(current_user)) -> dict:
    if not tr.can_enter(user):
        raise ForbiddenFa("دسترسی ثبت گزارش تاندیش ندارید.")
    return user


def require_settings(user=Depends(current_user)) -> dict:
    if not tr.can_configure(user):
        raise ForbiddenFa("فقط مالک یا مدیر می‌تواند اقلام گزارش تاندیش را تنظیم کند.")
    return user


def _section(raw: Any) -> str:
    s = str(raw or "slab").strip().lower()
    return s if s in tr.SECTIONS else "slab"


def _form_context(
    db: Database,
    user: dict,
    section: str,
    *,
    values: dict[str, Any] | None = None,
    raw_text: str = "",
    message: str | None = None,
    errors: list[str] | None = None,
    parsed_note: str | None = None,
) -> dict:
    return {
        "user": user,
        "title_fa": tr.TITLE_FA,
        "sections": [(k, tr.section_label(k)) for k in tr.SECTION_ORDER],
        "section": section,
        "section_fa": tr.section_label(section),
        "fixed_fields": tr.FIXED_FIELDS,
        "lines": tr.get_lines(db, section),
        "items": db.list_tundish_report_items(section),
        "item_types": tr.ITEM_TYPES,
        "recent_grades": db.recent_tundish_steel_grades(section, limit=8),
        "values": values or {},
        "raw_text": raw_text,
        "message": message,
        "errors": errors or [],
        "parsed_note": parsed_note,
        "recent": db.list_tundish_reports(section=section, limit=20),
        "can_configure": tr.can_configure(user),
    }


def _collect(form: Any, items: list[dict]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixed = {f["key"]: str(form.get(f["key"]) or "").strip() for f in tr.FIXED_FIELDS}
    item_raw: dict[str, Any] = {}
    values: dict[str, Any] = dict(fixed)
    for it in items:
        key = f"item_{it['id']}"
        other = str(form.get(f"{key}_other") or "").strip()
        val = other or str(form.get(key) or "").strip()
        item_raw[str(it["id"])] = val
        values[key] = val
    return fixed, item_raw, values


@router.get("")
@router.get("/")
async def form_page(
    request: Request,
    section: str = "slab",
    user=Depends(require_entry),
    db: Database = Depends(get_db),
):
    return render(request, "tundish_report.html", _form_context(db, user, _section(section)))


@router.post("/parse")
async def parse_text(
    request: Request,
    user=Depends(require_entry),
    db: Database = Depends(get_db),
):
    form = await request.form()
    section = _section(form.get("section"))
    raw = str(form.get("raw_text") or "").strip()
    items = db.list_tundish_report_items(section)
    lines = tr.get_lines(db, section)
    parsed = tr.parse_report_text(raw, section, lines, items)
    values: dict[str, Any] = {}
    found = 0
    for key, val in parsed["fields"].items():
        try:
            values[key] = tr.clean_fixed(key, val, lines=lines)
            found += 1
        except ValueError:
            pass
    by_id = {int(it["id"]): it for it in items}
    for iid, val in parsed["items"].items():
        it = by_id.get(int(iid))
        if not it:
            continue
        try:
            values[f"item_{iid}"] = tr.clean_item_value(it, val)
            found += 1
        except ValueError:
            pass
    total = len(tr.FIXED_FIELDS) + len(items)
    note = f"🔎 {found} از {total} مورد از متن شناسایی شد؛ بقیه را تکمیل و سپس ثبت کنید."
    return render(
        request,
        "tundish_report.html",
        _form_context(db, user, section, values=values, raw_text=raw, parsed_note=note),
    )


@router.post("/submit")
async def submit(
    request: Request,
    user=Depends(require_entry),
    db: Database = Depends(get_db),
):
    form = await request.form()
    section = _section(form.get("section"))
    items = db.list_tundish_report_items(section)
    fixed, item_raw, values = _collect(form, items)
    raw_text = str(form.get("raw_text") or "").strip() or None
    rep, errors = tr.submit(db, user, section, fixed, item_raw, source="web", raw_text=raw_text)
    if errors:
        return render(
            request,
            "tundish_report.html",
            _form_context(db, user, section, values=values, raw_text=raw_text or "", errors=errors),
            status_code=400,
        )
    log_activity(db, user, "tundish_report_saved", tundish_group=section)
    return render(
        request,
        "tundish_report.html",
        _form_context(
            db,
            user,
            section,
            message=f"✅ گزارش #{rep['id']} در {rep['created_at_tehran']} (تهران) ثبت شد.",
        ),
    )


# ------------------------------------------------------------------ settings
def _settings_context(
    db: Database, user: dict, section: str, *, message: str | None = None, error: str | None = None
) -> dict:
    return {
        "user": user,
        "title_fa": tr.TITLE_FA,
        "sections": [(k, tr.section_label(k)) for k in tr.SECTION_ORDER],
        "section": section,
        "section_fa": tr.section_label(section),
        "items": db.list_tundish_report_items(section),
        "item_types": tr.ITEM_TYPES,
        "lines": tr.get_lines(db, section),
        "message": message,
        "error": error,
    }


def _settings_render(request, db, user, section, message=None, error=None):
    return render(
        request,
        "tundish_report_settings.html",
        _settings_context(db, user, section, message=message, error=error),
        status_code=400 if error else 200,
    )


@router.get("/settings")
async def settings_page(
    request: Request,
    section: str = "slab",
    user=Depends(require_settings),
    db: Database = Depends(get_db),
):
    return _settings_render(request, db, user, _section(section))


@router.post("/settings/item/add")
async def settings_add(
    request: Request,
    user=Depends(require_settings),
    db: Database = Depends(get_db),
):
    form = await request.form()
    section = _section(form.get("section"))
    try:
        it = tr.add_item(
            db,
            section,
            label=str(form.get("label") or ""),
            item_type=str(form.get("item_type") or ""),
            options=str(form.get("options") or ""),
            required=bool(form.get("required")),
            updated_by=user["bale_user_id"],
        )
    except ValueError as exc:
        return _settings_render(request, db, user, section, error=str(exc))
    log_activity(db, user, "tundish_report_settings")
    return _settings_render(request, db, user, section, message=f"✅ قلم «{it['label']}» اضافه شد.")


@router.post("/settings/item/{item_id}/edit")
async def settings_edit(
    item_id: int,
    request: Request,
    user=Depends(require_settings),
    db: Database = Depends(get_db),
):
    form = await request.form()
    section = _section(form.get("section"))
    try:
        it = tr.update_item(
            db,
            item_id,
            label=str(form.get("label") or ""),
            item_type=str(form.get("item_type") or ""),
            options=str(form.get("options") or ""),
            required=bool(form.get("required")),
            updated_by=user["bale_user_id"],
        )
        pos = str(form.get("position") or "").strip()
        if pos:
            tr.move_item(db, item_id, int(tr.normalize_digits(pos)))
    except ValueError as exc:
        return _settings_render(request, db, user, section, error=str(exc))
    log_activity(db, user, "tundish_report_settings")
    return _settings_render(request, db, user, section, message=f"✅ قلم «{it.get('label')}» ذخیره شد.")


@router.post("/settings/item/{item_id}/delete")
async def settings_delete(
    item_id: int,
    request: Request,
    user=Depends(require_settings),
    db: Database = Depends(get_db),
):
    form = await request.form()
    section = _section(form.get("section"))
    try:
        tr.delete_item(db, item_id)
    except ValueError as exc:
        return _settings_render(request, db, user, section, error=str(exc))
    log_activity(db, user, "tundish_report_settings")
    return _settings_render(request, db, user, section, message="🗑 قلم حذف شد (گزارش‌های قبلی دست نمی‌خورند).")


@router.post("/settings/lines")
async def settings_lines(
    request: Request,
    user=Depends(require_settings),
    db: Database = Depends(get_db),
):
    form = await request.form()
    section = _section(form.get("section"))
    try:
        vals = tr.set_lines(db, section, str(form.get("lines") or ""), updated_by=user["bale_user_id"])
    except ValueError as exc:
        return _settings_render(request, db, user, section, error=str(exc))
    log_activity(db, user, "tundish_report_settings")
    return _settings_render(request, db, user, section, message="✅ خطوط ذخیره شد: " + "، ".join(vals))
