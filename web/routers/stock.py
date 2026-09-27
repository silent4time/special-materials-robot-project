"""Site stock entry — all active roles (incl. technician), same DB as bot."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from bot.activity import log_activity
from config import SITE_STOCK_GROUP_KEYS, SITE_STOCK_GROUPS
from db.models import Database
from web.deps import current_user, get_db
from web.templating import render

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("")
@router.get("/")
async def stock_form(
    request: Request,
    user=Depends(current_user),
    db: Database = Depends(get_db),
    group: str = "slab",
    entry_date: str | None = None,
):
    g = (group or "slab").strip().lower()
    if g not in SITE_STOCK_GROUP_KEYS:
        g = "slab"
    day = (entry_date or db.tehran_today()).strip()
    items = db.list_items_for_group(g)
    existing = {
        e["item_id"]: e
        for e in db.list_site_stock_entries(entry_date=day, tundish_group=g)
    }
    return render(
        request,
        "stock.html",
        {
            "user": user,
            "group": g,
            "groups": SITE_STOCK_GROUPS,
            "entry_date": day,
            "items": items,
            "existing": existing,
            "message": None,
            "error": None,
        },
    )


@router.post("/save")
async def stock_save(
    request: Request,
    user=Depends(current_user),
    db: Database = Depends(get_db),
    group: str = Form(...),
    entry_date: str = Form(...),
):
    g = (group or "").strip().lower()
    day = (entry_date or db.tehran_today()).strip()
    if g not in SITE_STOCK_GROUP_KEYS:
        return render(
            request,
            "stock.html",
            {
                "user": user,
                "group": "slab",
                "groups": SITE_STOCK_GROUPS,
                "entry_date": day,
                "items": [],
                "existing": {},
                "message": None,
                "error": "گروه نامعتبر.",
            },
            status_code=400,
        )
    items = db.list_items_for_group(g)
    form = await request.form()
    saved = 0
    errors: list[str] = []
    for it in items:
        iid = str(it["id"])
        raw = form.get(f"qty_{iid}")
        if raw is None or str(raw).strip() == "":
            continue
        try:
            qty = float(str(raw).strip().replace(",", "."))
        except ValueError:
            errors.append(f"{it.get('name_desc') or iid}: مقدار نامعتبر")
            continue
        try:
            db.upsert_site_stock_entry(
                bale_user_id=user["bale_user_id"],
                tundish_group=g,
                item_id=iid,
                quantity=qty,
                item_name_snapshot=it.get("name_desc"),
                actor_display_name=user.get("display_name"),
                entry_date=day,
            )
            saved += 1
        except (ValueError, KeyError) as exc:
            errors.append(f"{it.get('name_desc') or iid}: {exc}")
    if saved:
        log_activity(db, user, "site_stock_saved", tundish_group=g)
    existing = {
        e["item_id"]: e
        for e in db.list_site_stock_entries(entry_date=day, tundish_group=g)
    }
    msg = f"✅ {saved} قلم برای «{SITE_STOCK_GROUPS.get(g, g)}» در تاریخ {day} ذخیره شد."
    if errors:
        msg += " | خطاها: " + "؛ ".join(errors[:5])
    return render(
        request,
        "stock.html",
        {
            "user": user,
            "group": g,
            "groups": SITE_STOCK_GROUPS,
            "entry_date": day,
            "items": items,
            "existing": existing,
            "message": msg if saved or errors else "هیچ مقداری وارد نشد.",
            "error": None,
        },
    )
