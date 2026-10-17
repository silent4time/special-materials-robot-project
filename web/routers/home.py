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


@router.get("/file-guides/{name}")
async def file_guide_image(name: str, user=Depends(current_user)):
    """Step screenshots of «❓ راهنمای تهیهٔ فایل» (login required; names blurred)."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from services import file_guides

    path = (file_guides.IMG_DIR / name).resolve()
    if path.parent != file_guides.IMG_DIR.resolve() or not path.is_file() or path.suffix != ".jpg":
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")
