"""v1.6.0 — «ℹ️ درباره و به‌روزرسانی» (owner/manager; same services.updater as the bot)."""
from __future__ import annotations

import re
from html import escape

from fastapi import APIRouter, Depends, Request
from markupsafe import Markup

from bot.activity import log_activity
from db.models import Database
from services import updater as up
from web.deps import get_db, require_admin_web
from web.templating import render

router = APIRouter(tags=["about"])


def changelog_html(md: str) -> Markup:
    """Tiny markdown → RTL HTML (headings, bullets, bold, code), like Updater::changelog_html."""
    out, in_list = [], False
    for raw in (md or "").splitlines():
        line = escape(raw.rstrip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
        m = re.match(r"^\s*[-*]\s+(.*)", line)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{m.group(1)}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        h = re.match(r"^(#{1,6})\s+(.*)", line)
        if h:
            out.append(f"<h{min(len(h.group(1)) + 2, 6)}>{h.group(2)}</h{min(len(h.group(1)) + 2, 6)}>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return Markup('<div class="changelog" dir="rtl">' + "".join(out) + "</div>")


def update_banner(user: dict | None) -> dict | None:
    """base.html banner — cached data only (never blocks a page on GitHub)."""
    from auth.rbac import require_manager

    if not user or not require_manager(user):
        return None
    try:
        info = up.update_info(get_db(), network=False)
    except Exception:  # noqa: BLE001
        return None
    st = info.get("status") or {}
    if st.get("state") == "running":
        return {"kind": "running", "text": up.status_fa(st)}
    if info["available"]:
        return {"kind": "new", "current": info["current"], "latest": info["latest"]}
    return None


def _ctx(db: Database, user: dict, *, force: bool = False, message: str = "", error: str = "") -> dict:
    info = up.update_info(db, force=force)
    return {
        "user": user,
        "info": info,
        "changelog": changelog_html(up.changelog_text(info)) if info["available"] else None,
        "current_changelog": changelog_html(up.local_changelog(info["current"])),
        "status_text": up.status_fa(info["status"]),
        "message": message,
        "error": error,
    }


@router.get("/about")
async def about_page(request: Request, user=Depends(require_admin_web), db: Database = Depends(get_db)):
    return render(request, "about.html", _ctx(db, user))


@router.post("/about/check")
async def about_check(request: Request, user=Depends(require_admin_web), db: Database = Depends(get_db)):
    ctx = _ctx(db, user, force=True)
    if ctx["info"]["error"]:
        ctx["error"] = "بررسی نسخهٔ جدید ممکن نشد (اتصال به GitHub)."
    return render(request, "about.html", ctx)


@router.post("/about/update")
async def about_update(request: Request, user=Depends(require_admin_web), db: Database = Depends(get_db)):
    info = up.update_info(db)
    if not info["available"]:
        return render(request, "about.html", _ctx(db, user, message="ربات به‌روز است."))
    if (info["status"] or {}).get("state") == "running":
        return render(request, "about.html", _ctx(db, user, error="یک به‌روزرسانی در حال اجراست."))
    how = up.launch_detached(str(user.get("bale_user_id") or ""))
    try:
        log_activity(db, user, "web_update", from_version=info["current"], to_version=info["latest"])
    except Exception:  # noqa: BLE001
        pass
    return render(request, "about.html", _ctx(
        db, user,
        message=f"⏳ به‌روزرسانی به نسخهٔ {info['latest']} شروع شد ({how}). پس از اجرای تست‌ها، ربات و پنل وب "
                "ری‌استارت می‌شوند؛ چند دقیقه بعد صفحه را تازه کنید.",
    ))
