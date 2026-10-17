"""اقلام بحرانی — draft → user notes → تأیید → archived final report (bot + web).

One shared workflow for the Bale bot and the web panel (a draft started in one can be
finished in the other):

  1. ``create_draft``: build the report (``services.critical_items_report``), store the
     template sheets (rows + fixed notes) in ``critical_item_reports`` and render the
     DRAFT files (xlsx + company/contractor PDF) under
     ``UPLOAD_DIR/critical_reports/<id>/draft/``.
  2. ``set_notes``: the user's own «توضیحات» text (one line per row of the notes
     block); the draft files are re-rendered from the STORED rows so the preview
     matches what will be approved. Empty text = no user notes.
  3. ``approve``: final files under ``…/<id>/final/`` from the same stored rows + the
     notes; status «approved»; listed by ``list_approved`` for re-download.
  4. ``cancel``: drop a draft (files removed, row kept with status «cancelled»).

Rows are frozen at draft time — approving later never recomputes (stock may change).
UPLOAD_DIR is inside the full backup zip and is not purged by housekeeping; files are
re-rendered from the DB row if they ever go missing. Permission: ``report.critical``
(same as the report itself) — checked by the callers (bot handler / web route).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from analytics.critical_items import RENO_LABEL_FA, normalize_reno_mode
from db.models import Database

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_CANCELLED = "cancelled"
STATUS_FA = {STATUS_DRAFT: "پیش‌نویس", STATUS_APPROVED: "تأییدشده", STATUS_CANCELLED: "لغوشده"}
MAX_NOTES_CHARS = 2000
MAX_NOTE_LINES = 12
KINDS = ("xlsx", "company", "contractor")
KIND_FA = {"xlsx": "اکسل", "company": "PDF شرکت", "contractor": "PDF پیمانکار"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS critical_item_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'draft',
    reno_mode TEXT NOT NULL,
    report_date TEXT NOT NULL,
    title TEXT,
    company_count INTEGER NOT NULL DEFAULT 0,
    contractor_count INTEGER NOT NULL DEFAULT 0,
    sheets_json TEXT NOT NULL,
    user_notes TEXT NOT NULL DEFAULT '',
    source TEXT,
    created_by TEXT,
    created_by_name TEXT,
    created_at TEXT NOT NULL,
    notes_by TEXT,
    notes_at TEXT,
    approved_by TEXT,
    approved_by_name TEXT,
    approved_at TEXT,
    xlsx_path TEXT,
    pdf_company TEXT,
    pdf_contractor TEXT
);
CREATE INDEX IF NOT EXISTS idx_critical_item_reports_status
    ON critical_item_reports(status, id);
"""


def ensure_schema(db: Database) -> None:
    with db.connect() as conn:
        conn.executescript(SCHEMA)


def archive_root() -> Path:
    p = Path(config.UPLOAD_DIR) / "critical_reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _name(user: dict | None) -> str:
    user = user or {}
    uid = str(user.get("bale_user_id") or "")
    name = str(user.get("display_name") or "").strip()
    return name if name and name != uid else ""


def clean_notes(text: str | None) -> str:
    """Trim, drop empty lines, cap lines/characters (the notes block stays readable)."""
    from services.critical_items_report import user_note_lines

    lines = user_note_lines(text)[:MAX_NOTE_LINES]
    return "\n".join(lines)[:MAX_NOTES_CHARS]


def _row(db: Database, report_id: int) -> dict[str, Any] | None:
    ensure_schema(db)
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM critical_item_reports WHERE id = ?", (int(report_id),)).fetchone()
    return dict(r) if r else None


def get_report(db: Database, report_id: int) -> dict[str, Any] | None:
    r = _row(db, report_id)
    if r:
        r["status_fa"] = STATUS_FA.get(r["status"], r["status"])
        r["reno_label"] = RENO_LABEL_FA.get(r["reno_mode"], r["reno_mode"])
    return r


def _list(db: Database, where: str, args: tuple, limit: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT id, status, reno_mode, report_date, title, company_count, contractor_count, "
            f"user_notes, source, created_by, created_by_name, created_at, approved_by, "
            f"approved_by_name, approved_at FROM critical_item_reports WHERE {where} "
            f"ORDER BY id DESC LIMIT ?",
            (*args, int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["status_fa"] = STATUS_FA.get(d["status"], d["status"])
        d["reno_label"] = RENO_LABEL_FA.get(d["reno_mode"], d["reno_mode"])
        out.append(d)
    return out


def list_approved(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return _list(db, "status = ?", (STATUS_APPROVED,), limit)


def list_drafts(db: Database, limit: int = 10) -> list[dict[str, Any]]:
    return _list(db, "status = ?", (STATUS_DRAFT,), limit)


def sheets_of(report: dict[str, Any]) -> list[dict[str, Any]]:
    return json.loads(report.get("sheets_json") or "[]")


def _file_prefix(report: dict[str, Any], final: bool) -> str:
    date_tag = str(report["report_date"]).replace("/", "-")
    mode = "با_نوسازی" if report["reno_mode"] == "with" else "بدون_نوسازی"
    tag = "تأییدشده" if final else "پیش‌نویس"
    return f"لیست_اقلام_بحرانی_{date_tag}_{mode}_{tag}_{int(report['id'])}"


def _render(db: Database, report: dict[str, Any], *, final: bool,
            letterhead_path: Path | str | None = None) -> dict[str, Path]:
    from services.critical_items_report import render_critical_files

    out_dir = archive_root() / str(int(report["id"])) / ("final" if final else "draft")
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    xlsx, pdfs = render_critical_files(
        sheets_of(report), out_dir=out_dir, prefix=_file_prefix(report, final),
        user_notes=report.get("user_notes") or "", letterhead_path=letterhead_path,
    )
    files = {"xlsx": xlsx, **pdfs}
    with db.connect() as conn:
        conn.execute(
            "UPDATE critical_item_reports SET xlsx_path = ?, pdf_company = ?, pdf_contractor = ? "
            "WHERE id = ?",
            (str(xlsx), str(pdfs.get("company") or "") or None,
             str(pdfs.get("contractor") or "") or None, int(report["id"])),
        )
    return files


def report_files(db: Database, report: dict[str, Any], *,
                 letterhead_path: Path | str | None = None) -> dict[str, Path]:
    """{kind: path} of the current files (draft or final); re-rendered if missing."""
    files = {
        k: Path(v) for k, v in (("xlsx", report.get("xlsx_path")),
                                ("company", report.get("pdf_company")),
                                ("contractor", report.get("pdf_contractor"))) if v
    }
    if not files.get("xlsx") or not all(p.is_file() for p in files.values()):
        files = _render(db, report, final=report["status"] == STATUS_APPROVED,
                        letterhead_path=letterhead_path)
    return files


def create_draft(
    db: Database,
    user: dict[str, Any],
    *,
    reno_mode: str = "with",
    source: str = "bot",
    letterhead_path: Path | str | None = None,
    report_date: Any = None,
    inventory_df: Any = None,
):
    """Build the report → stored draft. Returns (report | None, CriticalItemsResult)."""
    from services.critical_items_report import (
        SEGMENT_COMPANY, SEGMENT_CONTRACTOR, generate_critical_items_files, report_title,
        sheets_payload,
    )

    ensure_schema(db)
    reno_mode = normalize_reno_mode(reno_mode)
    uid = str(user.get("bale_user_id") or "")
    tmp_dir = Path(config.REPORT_DIR) / "critical_items" / (uid or "web") / "_build"
    res = generate_critical_items_files(
        db, user, report_date=report_date, output_dir=tmp_dir, file_prefix="build",
        letterhead_path=letterhead_path, reno_mode=reno_mode, inventory_df=inventory_df,
    )
    if res.error or res.counts is None:
        return None, res
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO critical_item_reports (status, reno_mode, report_date, title, "
            "company_count, contractor_count, sheets_json, user_notes, source, created_by, "
            "created_by_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)",
            (STATUS_DRAFT, reno_mode, res.counts.report_date or "",
             report_title(res.counts, None, reno_mode),
             res.row_count(SEGMENT_COMPANY), res.row_count(SEGMENT_CONTRACTOR),
             json.dumps(sheets_payload(res.sheets), ensure_ascii=False),
             source, uid, _name(user), _now()),
        )
        report_id = int(cur.lastrowid)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    report = get_report(db, report_id)
    _render(db, report, final=False, letterhead_path=letterhead_path)
    return get_report(db, report_id), res


def _editable(report: dict[str, Any] | None) -> str | None:
    if not report:
        return "گزارش اقلام بحرانی یافت نشد."
    if report["status"] == STATUS_APPROVED:
        return f"گزارش #{report['id']} قبلاً تأیید شده و قابل ویرایش نیست."
    if report["status"] == STATUS_CANCELLED:
        return f"پیش‌نویس #{report['id']} لغو شده است."
    return None


def set_notes(db: Database, report_id: int, text: str | None, user: dict[str, Any], *,
              letterhead_path: Path | str | None = None):
    """Save the user's notes on a draft and re-render the draft files.
    Returns (report | None, error | None)."""
    report = get_report(db, report_id)
    err = _editable(report)
    if err:
        return None, err
    notes = clean_notes(text)
    with db.connect() as conn:
        conn.execute(
            "UPDATE critical_item_reports SET user_notes = ?, notes_by = ?, notes_at = ? WHERE id = ?",
            (notes, str(user.get("bale_user_id") or ""), _now(), int(report_id)),
        )
    report = get_report(db, report_id)
    _render(db, report, final=False, letterhead_path=letterhead_path)
    return get_report(db, report_id), None


def approve(db: Database, report_id: int, user: dict[str, Any], *,
            letterhead_path: Path | str | None = None):
    """Draft (+ its notes) → final archived files. Returns (report | None, error | None)."""
    report = get_report(db, report_id)
    err = _editable(report)
    if err:
        return None, err
    with db.connect() as conn:
        conn.execute(
            "UPDATE critical_item_reports SET status = ?, approved_by = ?, approved_by_name = ?, "
            "approved_at = ? WHERE id = ? AND status = ?",
            (STATUS_APPROVED, str(user.get("bale_user_id") or ""), _name(user), _now(),
             int(report_id), STATUS_DRAFT),
        )
    report = get_report(db, report_id)
    _render(db, report, final=True, letterhead_path=letterhead_path)
    shutil.rmtree(archive_root() / str(int(report_id)) / "draft", ignore_errors=True)
    return get_report(db, report_id), None


def cancel(db: Database, report_id: int, user: dict[str, Any]):
    report = get_report(db, report_id)
    err = _editable(report)
    if err:
        return None, err
    with db.connect() as conn:
        conn.execute(
            "UPDATE critical_item_reports SET status = ?, notes_by = ?, notes_at = ?, xlsx_path = NULL, "
            "pdf_company = NULL, pdf_contractor = NULL WHERE id = ?",
            (STATUS_CANCELLED, str(user.get("bale_user_id") or ""), _now(), int(report_id)),
        )
    shutil.rmtree(archive_root() / str(int(report_id)), ignore_errors=True)
    return get_report(db, report_id), None


def summary_fa(report: dict[str, Any]) -> str:
    """One-line label: «#12 — 1405/07/25 — با نوسازی — شرکت ۱۲ / پیمانکار ۹ قلم»."""
    when = report.get("approved_at") if report.get("status") == STATUS_APPROVED else report.get("created_at")
    who = report.get("approved_by_name") if report.get("status") == STATUS_APPROVED else report.get("created_by_name")
    extra = f" — {who}" if who else ""
    _ = when
    return (
        f"#{report['id']} — {report['report_date']} — {report.get('reno_label') or RENO_LABEL_FA.get(report['reno_mode'], '')}"
        f" — شرکت {report['company_count']} / پیمانکار {report['contractor_count']} قلم{extra}"
    )


def preview_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """[{segment, name, title, columns, rows, notes}] for the web draft page."""
    from services.critical_items_report import compose_notes

    out = []
    for sh in sheets_of(report):
        out.append({**sh, "notes": compose_notes(sh.get("fixed_notes") or [],
                                                 report.get("user_notes") or "")})
    return out
