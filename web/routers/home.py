"""Home dashboard — links filtered by the same RBAC as the Bale bot."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from auth.rbac import can_request_materials, require_manager, role_label
from web.deps import current_user, deny_technician, user_can_see_reports
from web.templating import render

router = APIRouter(tags=["home"])


@router.get("/home")
async def home(request: Request, user=Depends(current_user)):
    return render(
        request,
        "home.html",
        {
            "user": user,
            "role_fa": role_label(user.get("role") or ""),
            "show_stock": True,
            "show_materials": can_request_materials(user),
            "show_reports": user_can_see_reports(user),
            "show_web_settings": require_manager(user),
            "is_technician": deny_technician(user),
            "error": None,
        },
    )
