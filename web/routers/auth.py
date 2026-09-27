"""Login / logout — session stores bale_user_id only (role lives on users row)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from db.models import Database
from web.auth_web import authenticate
from web.deps import current_user_optional, get_db
from web.templating import render

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login_page(request: Request, user=Depends(current_user_optional)):
    if user:
        return RedirectResponse("/home", status_code=303)
    return render(request, "login.html", {"error": None, "user": None})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Database = Depends(get_db),
):
    user = authenticate(db, username, password)
    if not user:
        return render(
            request,
            "login.html",
            {"error": "نام کاربری یا رمز عبور نادرست است.", "user": None},
            status_code=401,
        )
    request.session.clear()
    request.session["bale_user_id"] = str(user["bale_user_id"])
    request.session["web_username"] = username.strip()
    return RedirectResponse("/home", status_code=303)


@router.post("/logout")
@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
