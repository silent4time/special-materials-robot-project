"""«ℹ️ درباره و به‌روزرسانی» — same services.updater as the bot.

Reading the page: owner and manager. Checking and installing: owner only.
"""
from __future__ import annotations

import re
from html import escape

from fastapi import APIRouter, Depends, Request
from markupsafe import Markup

from auth.rbac import require_owner
from bot.activity import log_activity
from db.models import Database
from services import updater as up
from web.deps import get_db, require_admin_web, require_owner_web
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
    """base.html banner — cached data only, owner only (never blocks a page)."""
    if not user or not require_owner(user):
        return None
    try:
        info = up.update_info(get_db(), network=False)
    except Exception:  # noqa: BLE001
        return None
    st = info.get("status") or {}
    if st.get("state") == "running":
        return {"kind": "running", "text": up.redact_public(up.status_fa(st))}
    if info["available"]:
        return {"kind": "new", "current": info["current"], "latest": info["latest"]}
    return None


def _ctx(db: Database, user: dict, *, force: bool = False, message: str = "", error: str = "") -> dict:
    owner = require_owner(user)
    info = up.update_info(db, force=bool(force and owner), network=bool(force and owner))
    show_new = bool(owner and info["available"])
    return {
        "user": user,
        "info": info,
        "facts": up.about_facts(),
        "can_update": owner,
        "show_install": show_new,
        "changelog": changelog_html(up.changelog_text(info)) if show_new else None,
        "current_changelog": changelog_html(up.redact_public(up.local_changelog(info["current"]))),
        "status_text": up.status_fa(info["status"]) if owner else "",
        "message": message,
        "error": error,
    }


@router.get("/about")
async def about_page(request: Request, user=Depends(require_admin_web), db: Database = Depends(get_db)):
    return render(request, "about.html", _ctx(db, user))


@router.post("/about/check")
async def about_check(request: Request, user=Depends(require_owner_web), db: Database = Depends(get_db)):
    ctx = _ctx(db, user, force=True)
    if ctx["info"]["error"]:
        ctx["error"] = up.CHECK_FAIL_FA
    elif not ctx["info"]["available"]:
        ctx["message"] = up.NO_UPDATE_FA.format(current=ctx["info"]["current"])
    return render(request, "about.html", ctx)


@router.post("/about/update")
async def about_update(request: Request, user=Depends(require_owner_web), db: Database = Depends(get_db)):
    info = up.update_info(db, network=False)
    if not info["available"]:
        info = up.update_info(db, force=True)
    if info.get("error") and not info["available"]:
        return render(request, "about.html", _ctx(db, user, error=up.CHECK_FAIL_FA))
    if not info["available"]:
        return render(request, "about.html", _ctx(
            db, user, message=up.NO_UPDATE_FA.format(current=info["current"]),
        ))
    token = up.try_acquire_update_lock()
    if not token:
        return render(request, "about.html", _ctx(db, user, error=up.BUSY_FA))
    try:
        how = up.launch_detached(str(user.get("bale_user_id") or ""), lock_token=token)
        up.clear_proc_flag()
    except Exception:
        up.release_update_lock(token=token)
        raise
    try:
        log_activity(db, user, "web_update", from_version=info["current"], to_version=info["latest"])
    except Exception:  # noqa: BLE001
        pass
    return render(request, "about.html", _ctx(
        db, user,
        message=f"⏳ به‌روزرسانی به نسخهٔ {info['latest']} روی همین سرور شروع شد ({how}). "
                "پیش از نصب بک‌آپ گرفته می‌شود. پس از تست‌ها ربات و پنل وب ری‌استارت می‌شوند؛ "
                "چند دقیقه بعد صفحه را تازه کنید.",
    ))
