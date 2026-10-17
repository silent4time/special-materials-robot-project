"""«➕ افزودن رکورد» به منبع اصلی با تأیید مدیر/مالک (1405/07/25).

کاربر دارای مجوز «ویرایش منبع اصلی» (services.permissions.MAIN_SOURCE_EDIT) رکورد را
ثبت می‌کند؛ اگر خودش مالک/مدیر باشد مستقیم اعمال می‌شود، وگرنه یک درخواست در انتظار
ساخته می‌شود و فقط با «✅ تایید» مالک/مدیر (دارای همان مجوز) اعمال می‌شود.
ربات و وب از همین ماژول استفاده می‌کنند.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services import permissions as perm

CB_PREFIX = "msa|"  # inline callback: msa|<id>|ok / msa|<id>|no
PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure(db: Any) -> None:
    with db.connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS main_source_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id TEXT NOT NULL,
                requester_name TEXT,
                record_json TEXT NOT NULL,
                allow_update INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                decided_by TEXT,
                decided_at TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            )"""
        )


def is_approver(user: dict | None) -> bool:
    return bool(user) and user.get("role") in perm.ADMINS and perm.can(user, perm.MAIN_SOURCE_EDIT)


def needs_approval(user: dict | None) -> bool:
    return not is_approver(user)


def approvers(db: Any) -> list[dict]:
    return [u for u in db.list_users(active_only=True) if is_approver(u)]


def submit(db: Any, user: dict, record: dict, *, allow_update: bool = False) -> int:
    _ensure(db)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO main_source_requests (requester_id, requester_name, record_json, allow_update, status, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?)",
            (str(user["bale_user_id"]), user.get("display_name") or "", json.dumps(record, ensure_ascii=False),
             1 if allow_update else 0, _now()),
        )
        return int(cur.lastrowid)


def _row(r: Any) -> dict:
    d = dict(r)
    d["record"] = json.loads(d.pop("record_json") or "{}")
    return d


def get(db: Any, req_id: int) -> dict | None:
    _ensure(db)
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM main_source_requests WHERE id = ?", (int(req_id),)).fetchone()
    return _row(r) if r else None


def list_pending(db: Any) -> list[dict]:
    _ensure(db)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM main_source_requests WHERE status = 'pending' ORDER BY id"
        ).fetchall()
    return [_row(r) for r in rows]


def _decide(db: Any, req_id: int, status: str, approver: dict, note: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE main_source_requests SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ? AND status = 'pending'",
            (status, str(approver["bale_user_id"]), _now(), note, int(req_id)),
        )


def approve(db: Any, req_id: int, approver: dict) -> tuple[dict | None, str]:
    """Apply the request (add_row). Returns (request, Persian result text)."""
    from services import main_source as ms

    if not is_approver(approver):
        return None, "فقط مالک یا مدیر (با مجوز منبع اصلی) می‌تواند تأیید کند."
    req = get(db, req_id)
    if not req:
        return None, "درخواست یافت نشد."
    if req["status"] != PENDING:
        return req, "این درخواست قبلاً بررسی شده است."
    try:
        result = ms.add_row(db, req["record"], bale_user_id=approver["bale_user_id"],
                            allow_update=bool(req["allow_update"]))
    except (KeyError, ValueError) as exc:
        _decide(db, req_id, REJECTED, approver, f"خطا: {exc}")
        return get(db, req_id), f"اعمال نشد: {exc}"
    _decide(db, req_id, APPROVED, approver)
    action = "به‌روز" if result.get("action") == "updated" else "اضافه"
    return get(db, req_id), f"✅ رکورد «{result.get('id')}» {action} شد."


def reject(db: Any, req_id: int, approver: dict, note: str = "") -> tuple[dict | None, str]:
    if not is_approver(approver):
        return None, "فقط مالک یا مدیر (با مجوز منبع اصلی) می‌تواند رد کند."
    req = get(db, req_id)
    if not req:
        return None, "درخواست یافت نشد."
    if req["status"] != PENDING:
        return req, "این درخواست قبلاً بررسی شده است."
    _decide(db, req_id, REJECTED, approver, note)
    return get(db, req_id), "❌ درخواست رد شد."


def request_text_fa(req: dict) -> str:
    rec = req.get("record") or {}
    head = "جایگزینی رکورد موجود" if req.get("allow_update") else "رکورد جدید"
    lines = [
        f"📝 درخواست افزودن به منبع اصلی #{req['id']} ({head})",
        f"درخواست‌دهنده: {req.get('requester_name') or req.get('requester_id')}",
    ]
    from services.main_source import FIELD_LABELS_FA

    for k, v in rec.items():
        if str(v or "").strip():
            lines.append(f"• {FIELD_LABELS_FA.get(k, k)}: {v}")
    return "\n".join(lines)


def inline_markup(req_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ تایید", "callback_data": f"{CB_PREFIX}{req_id}|ok"},
        {"text": "❌ رد", "callback_data": f"{CB_PREFIX}{req_id}|no"},
    ]]}


def bale_send(chat_ids: list, text: str, markup: dict | None = None) -> int:
    """Best-effort Bale notify from the web process (skips if no token)."""
    try:
        from bot.bale_api import BaleClient
        from config import BALE_BOT_TOKEN
    except Exception:  # noqa: BLE001
        return 0
    if not BALE_BOT_TOKEN.strip() or not chat_ids:
        return 0
    sent = 0
    client = BaleClient()
    try:
        for cid in chat_ids:
            try:
                client.send_message(cid, text, markup)
                sent += 1
            except Exception:  # noqa: BLE001
                pass
    finally:
        client.close()
    return sent
