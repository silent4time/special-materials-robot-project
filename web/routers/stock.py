"""Site stock entry — all active roles (incl. technician), same DB as bot."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request

from bot.activity import log_activity
from config import SITE_STOCK_GROUP_KEYS, SITE_STOCK_GROUPS
from db.models import Database
from services import site_stock_notify
from services.site_stock_lists import items_for_group as site_items_for_group
from services import permissions as perm
from web.deps import current_user, get_db, require_feature
from web.templating import render

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("")
@router.get("/")
async def stock_form(
    request: Request,
    user=Depends(require_feature(perm.SITE_STOCK)),
    db: Database = Depends(get_db),
    group: str = "slab",
    entry_date: str | None = None,
):
    g = (group or "slab").strip().lower()
    if g not in SITE_STOCK_GROUP_KEYS:
        g = "slab"
    day = (entry_date or db.tehran_today()).strip()
    items = site_items_for_group(db, g)
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
    user=Depends(require_feature(perm.SITE_STOCK)),
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
    items = site_items_for_group(db, g)
    form = await request.form()
    saved = 0
    errors: list[str] = []
    values: dict[str, float] = {}
    id_to_item = {str(it["id"]): it for it in items}
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
            values[iid] = qty
            saved += 1
        except (ValueError, KeyError) as exc:
            errors.append(f"{it.get('name_desc') or iid}: {exc}")
    if saved:
        log_activity(db, user, "site_stock_saved", tundish_group=g)
        # Best-effort Bale group notify (same rules as bot; skip if no token/group)
        try:
            from bot.bale_api import BaleClient
            from config import BALE_BOT_TOKEN

            if BALE_BOT_TOKEN.strip() and site_stock_notify.resolve_report_group_id(db):
                client = BaleClient()
                try:
                    site_stock_notify.notify_site_stock_saved(
                        client,
                        db,
                        registrar_name=user.get("display_name") or "کاربر وب",
                        tundish_group=g,
                        entry_date=day,
                        saved=saved,
                        items_total=len(items),
                        values=values,
                        id_to_item=id_to_item,
                    )
                finally:
                    client.close()
        except Exception:  # noqa: BLE001
            logger.exception("web site-stock group notify failed")
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
