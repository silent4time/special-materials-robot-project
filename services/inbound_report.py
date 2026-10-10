"""«📥 گزارش اقلام ورودی به انبار» — shared bot + web service (t221u).

Baseline = the PREVIOUS warehouse stock upload («📥 موجودی انبار» path), frozen as an
immutable snapshot (one row per material ID). Full منبع اصلی uploads, add/edit/delete
record, usage sync and web edits never move the baseline.

After each stock upload an inbound report is computed and stored:

(a) increase — ID present in the baseline snapshot and its stock rose → the increase;
(b) new ID   — ID absent from the baseline, its 4-digit code exists in منبع اصلی
               (live, before the upload) and stock > 0 → the full stock.

Excluded: code 1800 (surplus), new IDs with stock 0. Rows the upload rule rejected
(``services.main_source.filter_inventory_upload``: new 4-digit code / new 1800 row)
are listed separately as ⛔ and are never inbound. Priority-0 rows are included.
Duplicate rows of one ID (one row per work order in the warehouse export, same stock
repeated) collapse to ONE value per ID — never summed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

import config
from bot.jalali import format_datetime
from db.models import Database
from services.main_source import (
    SURPLUS_CATEGORY_CODE,
    _cell_str,
    _norm_code4,
    _norm_id,
)

logger = logging.getLogger(__name__)

STOCK_UPLOAD_KIND = "stock_update"

TYPE_INCREASE = "افزایش"
TYPE_NEW_ID = "شناسه جدید"

COL_ROW = "ردیف"
COL_CODE = "کد دسته"
COL_ID = "شناسه مواد"
COL_KEYWORD = "کلید واژه"
COL_UNIT = "واحد"
COL_PREV = "موجودی قبلی"
COL_NEW = "موجودی جدید"
COL_INBOUND = "مقدار ورودی"
COL_TYPE = "نوع"
COL_SUPPLIER = "تأمین‌کننده"
COL_LOCATION = "محل استفاده"

INBOUND_COLUMNS = [
    COL_ROW, COL_CODE, COL_ID, COL_KEYWORD, COL_UNIT, COL_PREV, COL_NEW,
    COL_INBOUND, COL_TYPE, COL_SUPPLIER, COL_LOCATION,
]
SUMMARY_COLUMNS = [
    COL_ROW, COL_CODE, "شرح دسته", "تعداد افزایش", "تعداد شناسه جدید",
    "تعداد کل اقلام", "جمع مقدار ورودی",
]
REJECTED_COLUMNS = [COL_ROW, COL_CODE, COL_ID, "شرح کالا", "موجودی", "دلیل"]

REPORT_TITLE = "گزارش اقلام ورودی به انبار"
SHEET_INBOUND = "ورودی"
SHEET_SUMMARY = "خلاصه بر اساس کد"
SHEET_REJECTED = "رد شده"


# ── helpers ─────────────────────────────────────────────────────────────────
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _qty(value: Any) -> float | None:
    if value is None:
        return None
    try:
        num = pd.to_numeric(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if num is None or pd.isna(num):
        return None
    return float(num)


def _num(value: float | None) -> int | float | str:
    """Report-friendly number: int when integral (avoids 1.2e+06 in PDF)."""
    if value is None:
        return ""
    if float(value).is_integer():
        return int(value)
    return round(float(value), 3)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = _cell_str(value)
    return "" if text.lower() == "nan" else text


def stock_frame_by_id(df: pd.DataFrame | None) -> pd.DataFrame:
    """One row per material ID (warehouse duplicates per work order → ONE value).

    Quantity = max over duplicate rows (they repeat the same stock), never the sum.
    Other fields: first non-blank value. Adds ``_key`` (normalized id) and
    ``category_code`` normalized to 4 digits.
    """
    cols = ["_key", "id", "category_code", "quantity", "product_name", "keyword",
            "unit", "contractor_or_company", "usage_location", "priority"]
    if df is None or df.empty or "id" not in df.columns:
        return pd.DataFrame(columns=cols)
    out: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        key = _norm_id(row.get("id"))
        if not key or key.lower() == "nan":
            continue
        qty = _qty(row.get("quantity"))
        rec = out.get(key)
        if rec is None:
            rec = {"_key": key, "id": _text(row.get("id")), "quantity": qty}
            for c in cols[2:]:
                if c == "quantity":
                    continue
                rec[c] = _norm_code4(row.get(c)) if c == "category_code" else _text(row.get(c))
            out[key] = rec
            continue
        if qty is not None and (rec["quantity"] is None or qty > rec["quantity"]):
            rec["quantity"] = qty
        for c in cols[2:]:
            if c == "quantity" or rec.get(c):
                continue
            val = _norm_code4(row.get(c)) if c == "category_code" else _text(row.get(c))
            if val:
                rec[c] = val
    return pd.DataFrame(list(out.values()), columns=cols)


# ── core computation (pure) ─────────────────────────────────────────────────
@dataclass
class InboundResult:
    lines: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    summary: list[dict[str, Any]] = field(default_factory=list)
    n_increase: int = 0
    n_new_id: int = 0
    n_zero_new: int = 0
    n_excluded_1800: int = 0
    total_inbound: float = 0.0
    has_baseline: bool = True


def compute_inbound(
    baseline: pd.DataFrame | None,
    uploaded: pd.DataFrame | None,
    *,
    live_before: pd.DataFrame | None,
    live_after: pd.DataFrame | None = None,
    rejected: Iterable[dict[str, Any]] | None = None,
    category_labels: dict[str, str] | None = None,
) -> InboundResult:
    """Inbound per the spec (see module docstring).

    ``baseline``    previous stock-upload snapshot (None → first baseline, no inbound)
    ``uploaded``    the uploaded stock frame (clean, before merge, any duplicates)
    ``live_before`` منبع اصلی before this upload (source of «existing 4-digit codes»)
    ``live_after``  منبع اصلی after merge (display metadata: keyword, unit, supplier…)
    ``rejected``    rows skipped by ``filter_inventory_upload`` (shown as ⛔)
    """
    res = InboundResult(has_baseline=baseline is not None)
    cur = stock_frame_by_id(uploaded)
    base = stock_frame_by_id(baseline) if baseline is not None else None
    base_qty = (
        {r["_key"]: r["quantity"] for _, r in base.iterrows()} if base is not None else {}
    )
    live_codes = {
        _norm_code4(v) for v in (live_before.get("category_code", []) if live_before is not None else [])
    } - {""}
    meta_df = stock_frame_by_id(live_after if live_after is not None else live_before)
    meta = {r["_key"]: r for _, r in meta_df.iterrows()}

    rej_keys: set[str] = set()
    for r in rejected or []:
        key = _norm_id(r.get("id"))
        if not key or key in rej_keys:
            continue
        rej_keys.add(key)
        res.rejected.append(
            {
                COL_CODE: _norm_code4(r.get("category_code")) or "—",
                COL_ID: _text(r.get("id")),
                "شرح کالا": _text(r.get("product_name")) or "—",
                "موجودی": _num(_qty(r.get("quantity"))),
                "دلیل": _text(r.get("reason")),
            }
        )

    for _, row in cur.iterrows():
        key = row["_key"]
        if key in rej_keys:
            continue
        code = row["category_code"]
        if code == SURPLUS_CATEGORY_CODE:
            res.n_excluded_1800 += 1
            continue
        if base is None:
            continue
        new_q = row["quantity"]
        if new_q is None:
            continue
        if key in base_qty:
            old_q = base_qty[key]
            old_v = 0.0 if old_q is None else float(old_q)
            if new_q > old_v:
                kind, inbound, prev = TYPE_INCREASE, new_q - old_v, old_v
            else:
                continue
        else:
            if code not in live_codes:
                continue  # unreachable in practice (filter rejects) — never inbound
            if new_q <= 0:
                res.n_zero_new += 1
                continue
            kind, inbound, prev = TYPE_NEW_ID, new_q, None
        m = meta.get(key)
        def pick(col: str) -> str:
            val = (m.get(col) if m is not None else "") or row.get(col) or ""
            return str(val)
        keyword = pick("keyword") or pick("product_name") or "—"
        res.lines.append(
            {
                COL_CODE: code or "—",
                COL_ID: row["id"] or (m.get("id") if m is not None else "") or "—",
                COL_KEYWORD: keyword,
                COL_UNIT: pick("unit"),
                COL_PREV: _num(prev) if prev is not None else 0,
                COL_NEW: _num(new_q),
                COL_INBOUND: _num(inbound),
                COL_TYPE: kind,
                COL_SUPPLIER: pick("contractor_or_company"),
                COL_LOCATION: pick("usage_location"),
                "_inbound": float(inbound),
            }
        )
        if kind == TYPE_INCREASE:
            res.n_increase += 1
        else:
            res.n_new_id += 1

    res.lines.sort(key=lambda r: (str(r[COL_CODE]), str(r[COL_ID])))
    for i, r in enumerate(res.lines, 1):
        r[COL_ROW] = i
    res.rejected.sort(key=lambda r: (str(r[COL_CODE]), str(r[COL_ID])))
    for i, r in enumerate(res.rejected, 1):
        r[COL_ROW] = i

    labels = category_labels or {}
    by_code: dict[str, dict[str, Any]] = {}
    for r in res.lines:
        agg = by_code.setdefault(
            str(r[COL_CODE]),
            {"تعداد افزایش": 0, "تعداد شناسه جدید": 0, "_sum": 0.0},
        )
        if r[COL_TYPE] == TYPE_INCREASE:
            agg["تعداد افزایش"] += 1
        else:
            agg["تعداد شناسه جدید"] += 1
        agg["_sum"] += r["_inbound"]
    for i, code in enumerate(sorted(by_code), 1):
        agg = by_code[code]
        res.summary.append(
            {
                COL_ROW: i,
                COL_CODE: code,
                "شرح دسته": labels.get(code, ""),
                "تعداد افزایش": agg["تعداد افزایش"],
                "تعداد شناسه جدید": agg["تعداد شناسه جدید"],
                "تعداد کل اقلام": agg["تعداد افزایش"] + agg["تعداد شناسه جدید"],
                "جمع مقدار ورودی": _num(agg["_sum"]),
            }
        )
    res.total_inbound = float(sum(r["_inbound"] for r in res.lines))
    for r in res.lines:
        r.pop("_inbound", None)
    return res


# ── snapshot + record (DB side effects) ─────────────────────────────────────
def snapshot_dir() -> Path:
    path = Path(config.UPLOAD_DIR) / "inventory_snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_snapshot(upload: dict[str, Any] | None) -> pd.DataFrame | None:
    if not upload or not upload.get("snapshot_path"):
        return None
    path = Path(upload["snapshot_path"])
    if not path.is_file():
        logger.warning("inbound baseline snapshot missing on disk: %s", path)
        return None
    return pd.read_excel(path, engine="openpyxl", dtype={"id": str, "category_code": str})


def freeze_stock_snapshot(
    db: Database,
    uploaded: pd.DataFrame,
    *,
    bale_user_id: str | int | None,
    actor_display_name: str | None,
    extract_id: int | None = None,
    raw_path: str | Path | None = None,
    note: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Write an immutable one-row-per-ID snapshot of a stock upload; insert DB row."""
    frame = stock_frame_by_id(uploaded).drop(columns=["_key"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    snap = snapshot_dir() / f"stock_upload_{stamp}.xlsx"
    frame.to_excel(snap, index=False, engine="openpyxl", sheet_name="stock")
    raw_copy = None
    if raw_path and Path(raw_path).is_file():
        raw_copy = snap.with_name(snap.stem + "_raw" + Path(raw_path).suffix)
        try:
            shutil.copy2(raw_path, raw_copy)
        except OSError as exc:  # noqa: PERF203
            logger.warning("could not keep raw stock upload copy: %s", exc)
            raw_copy = None
    upload_id = db.insert_stock_upload(
        snapshot_path=str(snap),
        kind=STOCK_UPLOAD_KIND,
        extract_id=extract_id,
        raw_path=str(raw_copy or raw_path or "") or None,
        sha256=_sha256(snap),
        row_count=int(len(frame)),
        bale_user_id=bale_user_id,
        actor_display_name=actor_display_name,
        note=note,
        created_at=created_at,
    )
    return db.get_stock_upload(upload_id) or {"id": upload_id, "snapshot_path": str(snap)}


def _category_labels(db: Database) -> dict[str, str]:
    try:
        return {
            _norm_code4(r.get("code")): str(r.get("label") or "")
            for r in db.list_category_codes(active_only=False)
        }
    except Exception:  # noqa: BLE001
        return {}


def record_stock_upload(
    db: Database,
    *,
    uploaded: pd.DataFrame | None,
    live_before: pd.DataFrame | None,
    live_after: pd.DataFrame | None,
    rejected: Iterable[dict[str, Any]] | None,
    bale_user_id: str | int | None,
    actor_display_name: str | None,
    extract_id: int | None = None,
    raw_path: str | Path | None = None,
    created_at: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Stock upload hook: baseline = previous stock upload; compute, freeze, store.

    Returns the stored inbound report row (dict, with parsed lists).
    """
    baseline_row = db.latest_stock_upload(kind=STOCK_UPLOAD_KIND)
    baseline_df = load_snapshot(baseline_row)
    if baseline_row is not None and baseline_df is None:
        baseline_row = None
    result = compute_inbound(
        baseline_df,
        uploaded,
        live_before=live_before,
        live_after=live_after,
        rejected=rejected,
        category_labels=_category_labels(db),
    )
    upload = freeze_stock_snapshot(
        db,
        uploaded if uploaded is not None else pd.DataFrame(),
        bale_user_id=bale_user_id,
        actor_display_name=actor_display_name,
        extract_id=extract_id,
        raw_path=raw_path,
        note=note,
        created_at=created_at,
    )
    report_id = db.insert_inbound_report(
        stock_upload_id=int(upload["id"]),
        baseline_upload_id=int(baseline_row["id"]) if baseline_row else None,
        bale_user_id=None if bale_user_id is None else str(bale_user_id),
        actor_display_name=actor_display_name,
        upload_created_at=upload.get("created_at") or created_at or _utcnow(),
        baseline_created_at=baseline_row.get("created_at") if baseline_row else None,
        n_increase=result.n_increase,
        n_new_id=result.n_new_id,
        n_rejected=len(result.rejected),
        n_zero_new=result.n_zero_new,
        n_excluded_1800=result.n_excluded_1800,
        total_inbound=result.total_inbound,
        lines_json=json.dumps(result.lines, ensure_ascii=False),
        rejected_json=json.dumps(result.rejected, ensure_ascii=False),
        summary_json=json.dumps(result.summary, ensure_ascii=False),
    )
    return get_report(db, report_id) or {}


# ── reading stored reports ──────────────────────────────────────────────────
def _parse(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for key, dest in (("lines_json", "lines"), ("rejected_json", "rejected"), ("summary_json", "summary")):
        try:
            out[dest] = json.loads(row.get(key) or "[]")
        except (TypeError, ValueError):
            out[dest] = []
    out["upload_label"] = format_datetime(row.get("upload_created_at")) or "—"
    out["baseline_label"] = (
        format_datetime(row.get("baseline_created_at")) if row.get("baseline_created_at") else ""
    )
    out["has_baseline"] = bool(row.get("baseline_upload_id"))
    out["n_inbound"] = int(row.get("n_increase") or 0) + int(row.get("n_new_id") or 0)
    return out


def get_report(db: Database, report_id: int) -> dict[str, Any] | None:
    return _parse(db.get_inbound_report(int(report_id)))


def latest_report(db: Database) -> dict[str, Any] | None:
    return _parse(db.latest_inbound_report())


def list_reports(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for r in db.list_inbound_reports(limit=limit):
        item = dict(r)
        item["upload_label"] = format_datetime(r.get("upload_created_at")) or "—"
        item["baseline_label"] = (
            format_datetime(r.get("baseline_created_at")) if r.get("baseline_created_at") else ""
        )
        item["n_inbound"] = int(r.get("n_increase") or 0) + int(r.get("n_new_id") or 0)
        out.append(item)
    return out


# ── presentation (shared by bot + web) ──────────────────────────────────────
def _fa_num(n: int) -> str:
    return str(int(n))  # same Latin digits as the rest of the bot/report text


def rejected_breakdown_fa(report: dict[str, Any]) -> list[str]:
    """Why rows were set aside — one clear line per reason (bot, web, PDF)."""
    rej = report.get("rejected") or []
    out: list[str] = []
    if rej:
        no_code = [r for r in rej if "کد ۴ رقمی" in str(r.get("دلیل") or "")]
        new_1800 = [r for r in rej if "1800" in str(r.get("دلیل") or "")]
        other = len(rej) - len(no_code) - len(new_1800)

        def pos(rows):
            k = 0
            for r in rows:
                try:
                    k += float(r.get("موجودی") or 0) > 0
                except (TypeError, ValueError):
                    pass
            return k

        if no_code:
            codes = "، ".join(sorted({str(r.get("کد دسته")) for r in no_code}))
            out.append(
                f"⛔ {_fa_num(len(no_code))} ردیف با کد ۴ رقمی ناموجود در منبع اصلی (در زمان آپلود) کنار گذاشته شد"
                f" ({_fa_num(pos(no_code))} ردیف با موجودی) — کدها: {codes}"
            )
        if new_1800:
            out.append(
                f"⛔ {_fa_num(len(new_1800))} ردیف جدید کد 1800 (مازاد) کنار گذاشته شد"
                f" ({_fa_num(pos(new_1800))} ردیف با موجودی) — فقط با «افزودن رکورد» یا جایگزینی کامل منبع اصلی"
            )
        if other > 0:
            out.append(f"⛔ {_fa_num(other)} ردیف دیگر کنار گذاشته شد (جزئیات در PDF/اکسل)")
    elif report.get("n_rejected"):
        out.append(f"⛔ {_fa_num(int(report.get('n_rejected') or 0))} ردیف کنار گذاشته شد (جزئیات در PDF/اکسل)")
    if report.get("n_zero_new"):
        out.append(f"ℹ️ {_fa_num(int(report['n_zero_new']))} شناسهٔ جدید با موجودی صفر فهرست نشد.")
    if report.get("n_excluded_1800"):
        out.append(f"ℹ️ {_fa_num(int(report['n_excluded_1800']))} ردیف کد 1800 موجود لحاظ نشد.")
    return out


def header_lines_fa(report: dict[str, Any]) -> list[str]:
    actor = report.get("actor_display_name") or "—"
    lines = [f"تاریخ آپلود فعلی موجودی انبار: {report.get('upload_label') or '—'} (ثبت‌کننده: {actor})"]
    if report.get("has_baseline"):
        lines.append(f"تاریخ پایهٔ مقایسه (آپلود موجودی قبلی): {report.get('baseline_label') or '—'}")
    else:
        lines.append("پایه مقایسه: ندارد — این آپلود اولین پایه است (ورودی محاسبه نمی‌شود).")
    lines.append(
        f"ورودی: {report.get('n_inbound', 0)} قلم "
        f"(افزایش {report.get('n_increase', 0)} + شناسه جدید {report.get('n_new_id', 0)})"
    )
    lines.extend(rejected_breakdown_fa(report))
    lines.append(
        "قاعده: کد 1800 لحاظ نمی‌شود؛ شناسه جدید با موجودی صفر فهرست نمی‌شود؛ "
        "تکرار یک شناسه در چند دستور کار یک مقدار حساب می‌شود."
    )
    return lines


def summary_text_fa(report: dict[str, Any], *, limit: int = 10) -> str:
    """Short Persian upload/bot message."""
    if not report:
        return "گزارش اقلام ورودی ثبت نشد."
    head = "📥 اقلام ورودی به انبار"
    if not report.get("has_baseline"):
        return (
            f"{head}: این آپلود به‌عنوان اولین پایه مقایسه ثبت شد؛ "
            "از آپلود موجودی بعدی ورودی‌ها گزارش می‌شوند."
            + "".join("\n" + x for x in rejected_breakdown_fa(report))
        )
    lines = [
        f"{head}",
        f"• تاریخ آپلود فعلی: {report.get('upload_label') or '—'}",
        f"• تاریخ پایهٔ مقایسه (آپلود قبلی): {report.get('baseline_label') or '—'}",
        "نتیجه:",
        f"{report.get('n_inbound', 0)} قلم (افزایش {report.get('n_increase', 0)}، "
        f"شناسه جدید {report.get('n_new_id', 0)})",
    ]
    for r in (report.get("lines") or [])[:limit]:
        from services.units import unit_fa

        unit = f" {unit_fa(r.get(COL_UNIT))}" if r.get(COL_UNIT) else ""
        lines.append(
            f"• {r.get(COL_CODE)} | {str(r.get(COL_KEYWORD))[:40]} | "
            f"+{r.get(COL_INBOUND)}{unit} ({r.get(COL_TYPE)})"
        )
    extra = int(report.get("n_inbound", 0)) - limit
    if extra > 0:
        lines.append(f"… و {extra} قلم دیگر (جزئیات در PDF/اکسل)")
    if not report.get("n_inbound"):
        lines.append(
            "قلم ورودی شناسایی نشد: موجودی هیچ شناسهٔ موجودی نسبت به پایه افزایش نیافت و "
            "شناسهٔ جدیدِ مجاز با موجودی بیشتر از صفر نبود."
        )
    lines.extend(rejected_breakdown_fa(report))
    return "\n".join(lines)


def report_sections(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "title": "اقلام ورودی به انبار",
            "sheet_title": SHEET_INBOUND,
            "columns": INBOUND_COLUMNS,
            "rows": report.get("lines") or [],
            "empty_message": (
                "قلم ورودی (افزایش موجودی یا شناسه جدید با موجودی) شناسایی نشد."
                if report.get("has_baseline")
                else "اولین پایه مقایسه — ورودی محاسبه نمی‌شود."
            ),
        },
        {
            "title": SHEET_SUMMARY,
            "sheet_title": SHEET_SUMMARY,
            "columns": SUMMARY_COLUMNS,
            "rows": report.get("summary") or [],
            "empty_message": "—",
        },
        {
            "title": "⛔ ردیف‌های رد شده (ورودی حساب نمی‌شوند)",
            "sheet_title": SHEET_REJECTED,
            "columns": REJECTED_COLUMNS,
            "rows": report.get("rejected") or [],
            "empty_message": "ردیفی رد نشد.",
            "header_bg": "#7f1d1d",
        },
    ]


def letterhead_path(db: Database) -> Path | None:
    raw = db.get_setting("letterhead_pdf")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_file() else None


def file_stem_fa(report: dict[str, Any]) -> str:
    """«گزارش_اقلام_ورودی_1405-07-06» (Jalali date of the current upload)."""
    from bot.jalali import format_date, format_datetime

    raw = report.get("upload_created_at")
    try:
        label = format_datetime(raw)[:10] if raw else ""
    except Exception:  # noqa: BLE001
        label = format_date(raw) if raw else ""
    label = (label or "").replace("/", "-").strip() or f"{int(report.get('id') or 0)}"
    return f"گزارش_اقلام_ورودی_{label}"


def caption_fa(report: dict[str, Any], *, excel: bool = False) -> str:
    """File caption with both dates clearly labelled (same wording as the message)."""
    base = f"{'نسخه اکسل — ' if excel else ''}{REPORT_TITLE}\nتاریخ آپلود فعلی: {report.get('upload_label') or '—'}"
    if report.get("has_baseline"):
        base += f"\nتاریخ پایهٔ مقایسه: {report.get('baseline_label') or '—'}"
    return base


def build_report_files(
    db: Database,
    report: dict[str, Any],
    *,
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    """PDF (A4 portrait + letterhead) and XLSX (ورودی / خلاصه بر اساس کد / رد شده)."""
    from excel.simple_report import generate_simple_report_xlsx
    from pdf.generator import generate_simple_report_pdf

    out_dir = Path(out_dir or config.REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = file_stem_fa(report)
    # one sub-folder per stored report so the Persian names never collide
    out_dir = out_dir / f"inbound_{int(report['id'])}"
    out_dir.mkdir(parents=True, exist_ok=True)
    subtitle = "\n".join(header_lines_fa(report))
    sections = report_sections(report)
    pdf_path = generate_simple_report_pdf(
        REPORT_TITLE,
        subtitle=subtitle,
        sections=sections,
        output_path=out_dir / f"{stem}.pdf",
        filename_stem=stem,
        letterhead_path=letterhead_path(db),
    )
    xlsx_path = generate_simple_report_xlsx(
        REPORT_TITLE,
        subtitle=subtitle,
        sections=sections,
        output_path=out_dir / f"{stem}.xlsx",
        filename_stem=stem,
    )
    return Path(pdf_path), Path(xlsx_path)


def history_button_label(item: dict[str, Any]) -> str:
    """Bot keyboard label for an earlier report (parsed back by HISTORY_RE)."""
    return f"📥 ورودی {item.get('upload_label') or '—'} — {item.get('n_inbound', 0)} قلم (#{int(item['id'])})"


HISTORY_PREFIX = "📥 ورودی "
