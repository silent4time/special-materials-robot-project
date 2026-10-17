"""Web parity for «🤖 از دستیار بپرس»: same services.assistant_brain, same per-intent
permissions (services.permissions) and the same owner switch (assistant_enabled)."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import UploadFile  # request.form() yields starlette (not fastapi) UploadFile

from db.models import Database
from web.deps import current_user, get_db
from web.templating import render

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])

MAX_AUDIO_BYTES = 15 * 1024 * 1024

# bot button label → web page (answer follow-ups); unknown labels re-ask the assistant
def _web_links() -> dict[str, str]:
    from bot import keyboards as kb

    return {
        kb.BTN_CRITICAL_ITEMS: "/reports/critical-items",
        kb.BTN_N_TUNDISH: "/reports/n-tundish",
        kb.BTN_PERIOD: "/reports/period",
        kb.BTN_DAILY: "/reports/daily",
        kb.BTN_COMPREHENSIVE: "/reports/comprehensive",
        kb.BTN_REMAINING: "/reports/remaining-critical",
        kb.BTN_SURPLUS: "/reports/surplus",
        kb.BTN_INBOUND: "/reports",
        kb.BTN_MONTHLY_SUMMARY: "/reports/monthly-summary",
        kb.BTN_ANALYTICS: "/reports",
        kb.BTN_MAIN_GOAL: "/reports/main-goal",
        kb.BTN_SITE_STOCK: "/stock",
        kb.BTN_TR_MENU: "/tundish-report",
        kb.BTN_MATERIAL_REQUEST: "/materials/request",
        kb.BTN_WAREHOUSE_RETURN: "/materials/return",
        kb.BTN_MAIN_SOURCE_FILE: "/settings/main-source",
        kb.BTN_MS_PENDING: "/settings/main-source",
        kb.BTN_UPLOAD_MENU: "/files",
    }


def _ctx(db: Database, user: dict, **kw):
    from services import assistant_brain as ab
    from services import voice_assistant as va

    enabled = db.get_setting(va.SETTING_ENABLED) == "1"
    return {
        "user": user,
        "enabled": enabled,
        "help_text": ab.help_text_fa(user),
        "whisper": va.availability()["whisper"] if enabled else False,
        "question": "",
        "answer": None,
        "links": [],
        "followups": [],
        "transcript": "",
        "error": None,
        **kw,
    }


@router.get("/assistant")
async def assistant_page(request: Request, user=Depends(current_user), db: Database = Depends(get_db)):
    return render(request, "assistant.html", _ctx(db, user))


@router.post("/assistant/ask")
async def assistant_ask(request: Request, user=Depends(current_user), db: Database = Depends(get_db)):
    from starlette.concurrency import run_in_threadpool

    from services import assistant_brain as ab
    from services import voice_assistant as va

    ctx = _ctx(db, user)
    if not ctx["enabled"]:
        ctx["error"] = "دستیار هوشمند غیرفعال است (فعال‌سازی: مالک، ⚙️ تنظیمات ربات)."
        return render(request, "assistant.html", ctx, status_code=400)
    form = await request.form()
    q = str(form.get("q") or "").strip()[:500]
    audio = form.get("audio")
    transcript = ""
    if not q and isinstance(audio, UploadFile) and audio.filename:
        if not ctx["whisper"]:
            ctx["error"] = "تبدیل گفتار به متن (Whisper) روی سرور نصب نیست؛ سؤال را بنویسید."
            return render(request, "assistant.html", ctx, status_code=400)
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            ctx["error"] = "فایل صوتی بزرگ‌تر از ۱۵ مگابایت است."
            return render(request, "assistant.html", ctx, status_code=400)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ("in" + (Path(audio.filename).suffix or ".ogg")[:6])
            p.write_bytes(data)
            transcript = await run_in_threadpool(va.transcribe, p)
        if not transcript:
            ctx["error"] = "متن صوت تشخیص داده نشد."
            return render(request, "assistant.html", ctx, status_code=400)
        q = transcript
    if not q:
        ctx["error"] = "سؤال را بنویسید."
        return render(request, "assistant.html", ctx, status_code=400)
    ans = await run_in_threadpool(lambda: ab.answer(db, user, q, channel="web"))
    links_map = _web_links()
    links, followups = [], []
    for b in ans.buttons:
        if b in links_map:
            links.append((b, links_map[b]))
        else:
            followups.append(b)
    ctx.update(question=q, answer=ans, links=links, followups=followups, transcript=transcript)
    return render(request, "assistant.html", ctx)
