"""Material request / warehouse return — can_request_materials (same as bot)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from bot.activity import log_activity
from db.models import Database
from services import permissions as perm
from web.deps import get_db, require_feature, require_materials_user
from web.templating import render

router = APIRouter(prefix="/materials", tags=["materials"])


def _clean(v: object) -> str:
    t = "" if v is None else str(v).strip()
    return "" if t.lower() in {"nan", "none"} else t


def _catalog_items(db: Database) -> list[dict]:
    """Catalog rows enriched from the factory-wide منبع اصلی (display only):
    ``display_name`` = کلید واژه (as the bot's daily lists), ``secondary`` = شرح کالا,
    ``group_fa`` = محل استفاده, ``unit``. Submitted keys stay the material ID."""
    items = db.list_catalog_with_assignments(active_only=True)
    try:
        from services import main_source as ms

        df = ms.load_primary_frame(db)
    except Exception:  # noqa: BLE001
        df = None
    by_id: dict[str, dict] = {}
    if df is not None and not df.empty and "id" in df.columns:
        for rec in df.to_dict("records"):
            iid = _clean(rec.get("id"))
            if iid and iid not in by_id:
                by_id[iid] = rec
    out = []
    for it in items:
        rec = by_id.get(str(it.get("id")), {})
        desc = _clean(rec.get("product_name")) or _clean(it.get("name_desc"))
        kw = _clean(rec.get("keyword"))
        group = _clean(rec.get("usage_location"))
        if not group and it.get("tundish_group"):
            from config import SITE_STOCK_GROUPS

            group = str(SITE_STOCK_GROUPS.get(it["tundish_group"], it["tundish_group"])).replace("موجودی مواد ", "")
        row = dict(it)
        row.update(
            display_name=kw or desc or str(it.get("id")),
            secondary=desc if kw and desc and desc != kw else "",
            group_fa=group,
            unit=_clean(rec.get("unit")),
            category_code=_clean(rec.get("category_code")) or _clean(it.get("category_code")),
        )
        out.append(row)
    return out


@router.get("/request")
async def request_form(
    request: Request,
    user=Depends(require_feature(perm.MATERIAL_REQUEST)),
    db: Database = Depends(get_db),
):
    return render(
        request,
        "materials_request.html",
        {
            "user": user,
            "items": _catalog_items(db),
            "coverage_days": 7,
            "message": None,
            "error": None,
            "history": db.list_material_requests(limit=8),
        },
    )


@router.post("/request")
async def request_submit(
    request: Request,
    user=Depends(require_feature(perm.MATERIAL_REQUEST)),
    db: Database = Depends(get_db),
    coverage_days: float = Form(7),
):
    form = await request.form()
    items = _catalog_items(db)
    by_id = {str(it["id"]): it for it in items}
    lines: list[dict] = []
    for key, val in form.multi_items():
        if not str(key).startswith("qty_"):
            continue
        iid = str(key)[4:]
        raw = str(val or "").strip()
        if not raw:
            continue
        try:
            qty = float(raw.replace(",", "."))
        except ValueError:
            continue
        if qty <= 0:
            continue
        it = by_id.get(iid) or {"id": iid, "name_desc": iid}
        lines.append(
            {
                "item_id": iid,
                "item_name": it.get("name_desc") or iid,
                "unit": None,
                "avg_daily": None,
                "remaining_qty": None,
                "quantity": qty,
            }
        )
    error = None
    message = None
    if not lines:
        error = "حداقل یک قلم با مقدار مثبت وارد کنید."
    else:
        try:
            req = db.create_material_request(
                user["bale_user_id"],
                actor_display_name=user.get("display_name"),
                coverage_days=coverage_days or 7,
                lines=lines,
                status="confirmed",
            )
            log_activity(db, user, "material_request_confirmed")
            message = (
                f"✅ درخواست مواد #{req['id']} با {len(req.get('lines') or [])} قلم ثبت شد "
                f"(پوشش {req.get('coverage_days')} روز)."
            )
        except ValueError as exc:
            error = str(exc)
    return render(
        request,
        "materials_request.html",
        {
            "user": user,
            "items": items,
            "coverage_days": coverage_days,
            "message": message,
            "error": error,
            "history": db.list_material_requests(limit=8),
        },
        status_code=400 if error else 200,
    )


@router.get("/return")
async def return_form(
    request: Request,
    user=Depends(require_feature(perm.WAREHOUSE_RETURN)),
    db: Database = Depends(get_db),
):
    return render(
        request,
        "materials_return.html",
        {
            "user": user,
            "items": _catalog_items(db),
            "message": None,
            "error": None,
        },
    )


@router.post("/return")
async def return_submit(
    request: Request,
    user=Depends(require_feature(perm.WAREHOUSE_RETURN)),
    db: Database = Depends(get_db),
):
    form = await request.form()
    items = _catalog_items(db)
    by_id = {str(it["id"]): it for it in items}
    lines: list[dict] = []
    for key, val in form.multi_items():
        if not str(key).startswith("qty_"):
            continue
        iid = str(key)[4:]
        raw = str(val or "").strip()
        if not raw:
            continue
        try:
            qty = float(raw.replace(",", "."))
        except ValueError:
            continue
        if qty <= 0:
            continue
        it = by_id.get(iid) or {"id": iid, "name_desc": iid}
        lines.append(
            {
                "item_id": iid,
                "item_name": it.get("name_desc") or iid,
                "unit": None,
                "site_qty": None,
                "surplus_qty": None,
                "quantity": qty,
                "surplus_reason": "web_manual",
            }
        )
    error = None
    message = None
    if not lines:
        error = "حداقل یک قلم با مقدار مثبت وارد کنید."
    else:
        try:
            ret = db.create_warehouse_return(
                user["bale_user_id"],
                actor_display_name=user.get("display_name"),
                lines=lines,
                status="confirmed",
            )
            log_activity(db, user, "warehouse_return_confirmed")
            message = f"✅ برگشت به انبار #{ret['id']} با {len(ret.get('lines') or [])} قلم ثبت شد."
        except ValueError as exc:
            error = str(exc)
    return render(
        request,
        "materials_return.html",
        {
            "user": user,
            "items": items,
            "message": message,
            "error": error,
        },
        status_code=400 if error else 200,
    )
