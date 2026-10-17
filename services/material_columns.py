"""ستون‌های پایهٔ مشترک همهٔ گزارش‌های مواد (قانون ثابت؛ CONTRIBUTING.md).

هر جدول گزارشی که «مواد» را فهرست می‌کند (PDF / اکسل؛ ربات، وب و دستیار) با این ۷ ستون
— به همین ترتیب — شروع می‌شود و ستون‌های ویژهٔ گزارش بعد از آن‌ها می‌آیند:

    کد ۴ رقمی، آیدی کد، شرح، کلیدواژه، موجودی، محل استفاده، تأمین‌کننده

مقادیر فقط با «خواندن» منبع اصلی (cleaned product_inventory + اصلاحات دفتر انبار، همان
``load_primary_inventory``) و بر اساس شناسه (آیدی) پر می‌شوند؛ این ماژول هرگز فایل منبع اصلی
یا مسیرهای کد آن را تغییر نمی‌دهد.

نگاشت به ستون‌های منبع اصلی::

    کد ۴ رقمی   ← category_code («کد دسته بندی»)
    آیدی کد     ← id («شناسه مواد»)
    شرح         ← product_name («شرح کالا»)
    کلیدواژه    ← keyword («کلید واژه»)
    موجودی      ← quantity («موجودی»، با اصلاحات دفتر انبار)
    محل استفاده ← usage_location
    تأمین‌کننده ← شرکت/پیمانکار با قانون آیدی (``classify_contractor_or_company``: رقم ۵ تا ۸
                  آیدی = 0000 → پیمانکار)، و ستون «تأمین‌کننده» منبع فقط وقتی آیدی کوتاه/خالی است.
                  (منبع اصلی ستون جداگانه‌ای برای نام شرکت سازنده ندارد؛ نام سازنده داخل «شرح» است.)

استفاده: هر section گزارش ساده (``generate_simple_report_pdf`` / ``generate_simple_report_xlsx``)
کلید ``"material"`` می‌گیرد (``spec``) و مولد، پیش از رسم، ``apply_sections`` را صدا می‌زند::

    {"id": "شناسه"}                       # ردیف‌ها یک‌به‌یک با آیدی
    {"code": "کد دسته", "segment": "company", "mode": "joined"}   # تجمیعی بر اساس کد ۴ رقمی
    "planning": True                     # فقط آیدی‌هایی که گزارش برنامه‌ریزی می‌شمارد (اولویت ≠ ۰)
    {"name": "material_name"}            # فقط نام/«آیدی - شرح» → تطبیق با منبع اصلی
    "drop": [...]                        # ستون‌های گزارش که ستون پایه جایشان را می‌گیرد
    "rename": {"remaining_qty": "موجودی سایت"}   # جلوگیری از هم‌نامی با «موجودی» پایه
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

B_CODE = "کد ۴ رقمی"
B_ID = "آیدی کد"
B_DESC = "شرح"
B_KW = "کلیدواژه"
B_QTY = "موجودی"
B_LOC = "محل استفاده"
B_SUP = "تأمین‌کننده"
BASE_COLUMNS: list[str] = [B_CODE, B_ID, B_DESC, B_KW, B_QTY, B_LOC, B_SUP]

SOURCE_FIELD = {
    B_CODE: "category_code",
    B_ID: "id",
    B_DESC: "product_name",
    B_KW: "keyword",
    B_QTY: "quantity",
    B_LOC: "usage_location",
    B_SUP: "contractor_or_company",
}

# Report columns that always duplicate a base column (removed when the base set is prepended).
SUPERSEDED: frozenset[str] = frozenset({
    "کد", "کد دسته", "کد دسته بندی", "کد دسته‌بندی", "کد چهاررقمی", "کد ۴ رقمی", "category_code",
    "شناسه", "شناسه مواد", "شناسه کالا", "آیدی", "آیدی کد", "id",
    "شرح", "شرح کالا", "کد و شرح کالا", "product_name", "material_name", "نام",
    "کلید واژه", "کلیدواژه", "keyword",
    "محل استفاده", "usage_location",
    "تأمین‌کننده", "تامین کننده", "پیمانکار/شرکت", "contractor_or_company",
})

JOIN = "، "
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ID_HEAD = re.compile(r"^\s*([0-9]{6,}[A-Za-z]{0,3})\s*(?:[-–—:|]\s*|\s+)(.*)$")
_GROUP_SPLIT = re.compile(r"\s*[/+،,]\s*")


def _blank(v: Any) -> bool:
    if v is None:
        return True
    try:
        import math

        if isinstance(v, float) and math.isnan(v):
            return True
    except Exception:  # noqa: BLE001
        pass
    s = str(v).strip()
    return s == "" or s.lower() in ("nan", "none", "nat")


def _txt(v: Any) -> str:
    return "" if _blank(v) else " ".join(str(v).split())


def norm_id(v: Any) -> str:
    s = _txt(v).upper().replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def norm_code(v: Any) -> str:
    s = _txt(v).translate(_DIGITS)
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _norm_name(v: Any) -> str:
    s = _txt(v)
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return " ".join(s.split()).lower()


def _num(v: Any) -> float | None:
    if _blank(v):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _qty_out(total: float) -> int | float:
    return int(total) if float(total).is_integer() else round(total, 2)


SEG_COMPANY = "company"
SEG_CONTRACTOR = "contractor"
SEG_LABEL = {SEG_COMPANY: "شرکت", SEG_CONTRACTOR: "پیمانکار"}


def _seg_key(v: Any) -> str | None:
    s = _txt(v)
    if not s:
        return None
    if s in SEG_LABEL:
        return s
    if "پیمانکار" in s or "contractor" in s.lower():
        return SEG_CONTRACTOR
    if "شرکت" in s or "company" in s.lower():
        return SEG_COMPANY
    return None


@dataclass
class MaterialIndex:
    """Read-only lookup over the main source (id → base fields)."""

    records: list[dict[str, Any]] = field(default_factory=list)
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_code: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, inv: Any) -> "MaterialIndex":
        idx = cls()
        if inv is None or getattr(inv, "empty", True):
            return idx
        try:
            from analytics.critical_items import classify_contractor_or_company
        except Exception:  # noqa: BLE001
            classify_contractor_or_company = None  # type: ignore
        prio0: list[bool] = [False] * len(inv)
        if "priority" in inv.columns:
            try:
                from analytics.tundish import NO_PRIORITY, _priority_values

                prio0 = list(_priority_values(inv["priority"]) == float(NO_PRIORITY))
            except Exception:  # noqa: BLE001
                pass
        for n, r in enumerate(inv.to_dict(orient="records")):
            iid = norm_id(r.get("id"))
            seg = None
            if classify_contractor_or_company is not None:
                try:
                    seg = classify_contractor_or_company(r.get("contractor_or_company"), r.get("id"))
                except Exception:  # noqa: BLE001
                    seg = None
            seg = seg or _seg_key(r.get("contractor_or_company"))
            rec = {
                B_CODE: norm_code(r.get("category_code")),
                B_ID: iid,
                B_DESC: _txt(r.get("product_name")),
                B_KW: _txt(r.get("keyword")),
                B_QTY: _num(r.get("quantity")),
                B_LOC: _txt(r.get("usage_location")),
                B_SUP: SEG_LABEL.get(seg or "", _txt(r.get("contractor_or_company"))),
                "_seg": seg,
                "_prio0": bool(prio0[n]),
            }
            idx.records.append(rec)
            if iid and iid not in idx.by_id:
                idx.by_id[iid] = rec
            if rec[B_CODE]:
                idx.by_code.setdefault(rec[B_CODE], []).append(rec)
            nm = _norm_name(rec[B_DESC])
            if nm:
                idx.by_name.setdefault(nm, []).append(rec)
        return idx

    def __len__(self) -> int:
        return len(self.records)

    # --- lookups -----------------------------------------------------------
    def by_identifier(self, value: Any) -> dict[str, Any] | None:
        iid = norm_id(value)
        return self.by_id.get(iid) if iid else None

    def by_label(self, value: Any) -> list[dict[str, Any]]:
        """«378121641302R - شرح…», a bare id, or an exact product name (may be several IDs)."""
        s = _txt(value)
        if not s:
            return []
        m = _ID_HEAD.match(s)
        if m:
            rec = self.by_identifier(m.group(1))
            if rec:
                return [rec]
        rec = self.by_identifier(s)
        if rec:
            return [rec]
        recs = self.by_name.get(_norm_name(s)) or []
        if not recs and m:
            recs = self.by_name.get(_norm_name(m.group(2))) or []
        return list(recs)

    def for_code(self, code: Any, segment: str | None = None, *, planning: bool = False) -> list[dict[str, Any]]:
        """IDs of a code; ``planning`` = only the rows planning reports count (اولویت ≠ 0)."""
        recs = self.by_code.get(norm_code(code), [])
        if segment:
            recs = [r for r in recs if r.get("_seg") == segment]
        if planning:
            recs = [r for r in recs if not r.get("_prio0")]
        return recs


def merge_records(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """One aggregated base row for several IDs (per-4-digit-code reports)."""

    def uniq(key: str) -> str:
        seen: list[str] = []
        for r in recs:
            for part in str(r.get(key) or "").split(JOIN):
                part = part.strip()
                if part and part not in seen:
                    seen.append(part)
        return JOIN.join(seen)

    qtys = [r.get(B_QTY) for r in recs if r.get(B_QTY) is not None]
    return {
        B_CODE: uniq(B_CODE),
        B_ID: uniq(B_ID),
        B_DESC: " | ".join(dict.fromkeys(r[B_DESC] for r in recs if r.get(B_DESC))),
        B_KW: uniq(B_KW),
        B_QTY: _qty_out(sum(qtys)) if qtys else None,
        B_LOC: uniq(B_LOC),
        B_SUP: uniq(B_SUP),
    }


# --- default index (bound DB) -------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict[int, tuple[float, MaterialIndex]] = {}
_TTL = 20.0


def load_index(db: Any = None, *, fresh: bool = False) -> MaterialIndex:
    """Main-source index (read-only). ``db`` defaults to the process-bound Database."""
    if db is None:
        try:
            from services import permissions as perm

            db = perm.bound_db()
        except Exception:  # noqa: BLE001
            db = None
    if db is None:
        return MaterialIndex()
    key = id(db)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < _TTL:
            return hit[1]
    try:
        from analytics.frames import load_primary_inventory

        inv = load_primary_inventory(db, {"role": "owner", "bale_user_id": None})
        idx = MaterialIndex.from_frame(inv)
    except Exception:  # noqa: BLE001
        logger.exception("material index load failed")
        idx = MaterialIndex()
    with _cache_lock:
        _cache[key] = (now, idx)
    return idx


def index_from(inventory_df: Any) -> MaterialIndex:
    return MaterialIndex.from_frame(inventory_df)


# --- row / section transforms -----------------------------------------------
def _fallback(row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Base cells from the report row itself when the item is not in the main source."""
    out = {c: None for c in BASE_COLUMNS}
    if spec.get("code"):
        out[B_CODE] = _txt(row.get(spec["code"]))
    if spec.get("id"):
        out[B_ID] = _txt(row.get(spec["id"]))
    name = _txt(row.get(spec["name"])) if spec.get("name") else ""
    if name:
        m = _ID_HEAD.match(name)
        if m and not out[B_ID]:
            out[B_ID], name = m.group(1), m.group(2).strip()
        out[B_DESC] = name
    for base, col in (spec.get("fallback") or {}).items():
        if base in out and not _blank(row.get(col)):
            out[base] = row.get(col)
    if spec.get("segment") in SEG_LABEL:
        out[B_SUP] = SEG_LABEL[spec["segment"]]
    return out


def resolve_row(row: dict[str, Any], spec: dict[str, Any], index: MaterialIndex) -> list[dict[str, Any]]:
    """Main-source records behind one report row (may be several for per-code rows)."""
    seg = spec.get("segment")
    if seg and seg not in SEG_LABEL:  # name of a column holding شرکت/پیمانکار
        seg = _seg_key(row.get(seg))
    if spec.get("ids") and row.get(spec["ids"]):
        ids = [norm_id(v) for v in row.get(spec["ids"]) or [] if norm_id(v)]
        recs = [r for r in (index.by_identifier(v) for v in ids) if r]
        known = {r[B_ID] for r in recs}
        for v in ids:  # IDs the main source does not know still show up
            if v not in known:
                recs.append({B_CODE: _txt(row.get(spec.get("code") or "")), B_ID: v, B_DESC: "", B_KW: "",
                             B_QTY: None, B_LOC: "", B_SUP: ""})
        if recs:
            return recs
    if spec.get("id"):
        rec = index.by_identifier(row.get(spec["id"]))
        if rec:
            return [rec]
    if spec.get("name"):
        recs = index.by_label(row.get(spec["name"]))
        if seg:
            recs = [r for r in recs if r.get("_seg") == seg] or recs
        if recs:
            return recs
    has_id = bool(spec.get("id") and not _blank(row.get(spec["id"])) and _txt(row.get(spec["id"])) not in ("—", "-"))
    if has_id or (spec.get("name") and not spec.get("code")):
        return []  # an item the main source does not know → the row's own values (never the whole code)
    if spec.get("code"):
        raw = _txt(row.get(spec["code"]))
        codes = [c for c in _GROUP_SPLIT.split(raw) if c] if raw else []
        recs: list[dict[str, Any]] = []
        for c in codes:
            recs += index.for_code(c, seg, planning=bool(spec.get("planning")))
        if len(codes) > 1 and recs:  # shared-need group subtotal: one line, no per-ID detail
            m = merge_records(recs)
            m[B_ID], m[B_DESC] = "", ""
            m[B_CODE] = raw
            return [m]
        return recs
    return []


def columns_for(columns: Iterable[str], spec: dict[str, Any]) -> list[str]:
    drop = set(SUPERSEDED) | set(spec.get("drop") or ()) | {spec.get(k) for k in ("id", "code", "name") if spec.get(k)}
    ren = spec.get("rename") or {}
    rest: list[str] = []
    for c in columns:
        if c in ren:
            rest.append(ren[c])
        elif c in drop or c in BASE_COLUMNS:
            continue
        else:
            rest.append(c)
    return list(BASE_COLUMNS) + [c for c in rest if c not in BASE_COLUMNS]


def apply_rows(
    columns: list[str], rows: list[dict[str, Any]], spec: dict[str, Any], index: MaterialIndex | None = None
) -> tuple[list[str], list[dict[str, Any]], list[int]]:
    """(columns, rows, src_index) — base columns first; src_index[i] = original row of output row i."""
    index = index if index is not None else load_index()
    mode = spec.get("mode") or "joined"
    ren = spec.get("rename") or {}
    out_cols = columns_for(columns, spec)
    out_rows: list[dict[str, Any]] = []
    src: list[int] = []
    for i, row in enumerate(rows or []):
        recs = resolve_row(row, spec, index) if len(index) else []
        extra = {ren.get(k, k): v for k, v in row.items()}
        if not recs:
            bases = [_fallback(row, spec)]
        elif mode == "per_id" and len(recs) > 1:
            bases = [{c: r.get(c) for c in BASE_COLUMNS} for r in recs]
        elif len(recs) == 1:
            bases = [{c: recs[0].get(c) for c in BASE_COLUMNS}]
        else:
            bases = [merge_records(recs)]
        for b in bases:
            if b.get(B_QTY) is not None:
                b[B_QTY] = _qty_out(float(b[B_QTY]))
            else:
                qf = spec.get("qty_fallback") or (
                    B_QTY if B_QTY in row and B_QTY not in (spec.get("rename") or {}) else None)
                if qf and not _blank(row.get(qf)):
                    b[B_QTY] = row.get(qf)
            merged = {k: v for k, v in extra.items() if k not in BASE_COLUMNS}
            merged.update(b)
            out_rows.append({c: merged.get(c) for c in out_cols} | {k: v for k, v in merged.items() if k.startswith("_")})
            src.append(i)
    return out_cols, out_rows, src


def apply_section(section: dict[str, Any], index: MaterialIndex | None = None) -> dict[str, Any]:
    spec = section.get("material")
    if not spec or not section.get("columns"):
        return section
    cols, rows, src = apply_rows(list(section["columns"]), list(section.get("rows") or []), spec, index)
    out = {k: v for k, v in section.items() if k != "material"}
    out["columns"], out["rows"] = cols, rows
    for key in ("bold_rows", "highlight_rows"):
        if section.get(key):
            marks = set(section[key])
            out[key] = [j for j, i in enumerate(src) if i in marks]
    out["material_base"] = True
    return out


def apply_sections(sections: list[dict[str, Any]] | None, index: MaterialIndex | None = None) -> list[dict[str, Any]]:
    if not sections:
        return list(sections or [])
    if not any(s.get("material") for s in sections):
        return list(sections)
    idx = index if index is not None else load_index()
    return [apply_section(s, idx) for s in sections]


# Common specs for frames keyed by ``material_name`` (analytics rates/remaining/surplus):
# matched by «آیدی - شرح» or exact product name → one row per material (per ID).
SPEC_RATES = {"name": "material_name", "mode": "per_id"}
SPEC_SITE_REMAINING = {"name": "material_name", "mode": "per_id", "rename": {"remaining_qty": "موجودی سایت"}}
SPEC_WAREHOUSE = {"name": "material_name", "mode": "per_id", "drop": ["remaining_qty"],
                  "qty_fallback": "remaining_qty"}


def remaining_spec(source_label: str | None) -> dict[str, Any]:
    """Site stock stays a report column («موجودی سایت»); warehouse remaining *is* base «موجودی»."""
    return SPEC_SITE_REMAINING if source_label and "سایت" in str(source_label) else SPEC_WAREHOUSE


def needs_landscape(sections: list[dict[str, Any]] | None, threshold: int = 9) -> bool:
    return any(len(s.get("columns") or []) >= threshold and s.get("rows") for s in sections or [])


def augment_frame(df: Any, spec: dict[str, Any], index: MaterialIndex | None = None) -> Any:
    """DataFrame variant (comprehensive report): returns a frame whose ``attrs['material_cols']``
    lists the display columns (base first)."""
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return df
    cols = list(spec.get("columns") or df.columns)
    cols = [c for c in cols if c in df.columns]
    rows = df.to_dict(orient="records")
    out_cols, out_rows, _src = apply_rows(cols, rows, spec, index)
    frame = pd.DataFrame(out_rows, columns=out_cols)
    frame.attrs["material_cols"] = out_cols
    return frame


def header_starts_with_base(header: list[Any]) -> bool:
    return [str(h).strip() for h in list(header)[: len(BASE_COLUMNS)]] == BASE_COLUMNS
