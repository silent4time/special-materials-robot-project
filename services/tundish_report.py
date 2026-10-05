"""«گزارش تاندیش بعد از ریخته‌گری» — shared logic for Bale bot + web panel.

Single source of truth for:
- fixed fields (شماره تاندیش، خط/ماشین، سکوئنس، تعداد ذوب، مارک)
- configurable per-section items (choice | number | text) and their CRUD validation
- casting lines per section (editable list in bot_settings)
- value validation, free-text parsing of operator sentences, summary text, persistence

Both ``bot.tundish_report_flow`` and ``web.routers.tundish_report`` call only
these helpers so the two front-ends never diverge.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from services.tundish_report_defaults import (
    DEFAULT_LINES,
    SECTION_ORDER,
    SECTIONS,
)

TITLE_FA = "گزارش تاندیش بعد از ریخته‌گری"

# Fixed (non-configurable) fields — asked first, in this order.
FIXED_FIELDS: list[dict[str, str]] = [
    {"key": "tundish_no", "label": "شماره تاندیش", "kind": "int"},
    {"key": "line", "label": "خط / ماشین ریخته‌گری", "kind": "line"},
    {"key": "sequence", "label": "سکوئنس", "kind": "int"},
    {"key": "melt_count", "label": "تعداد ذوب", "kind": "int"},
    {"key": "steel_grade", "label": "مارک (گرید فولاد)", "kind": "text"},
]
FIXED_BY_KEY = {f["key"]: f for f in FIXED_FIELDS}

ITEM_TYPES: dict[str, str] = {
    "choice": "انتخابی",
    "number": "عددی",
    "text": "متنی",
}
ITEM_TYPE_BY_LABEL = {v: k for k, v in ITEM_TYPES.items()}

# Roles allowed to submit (every real role; there is no guest role in users.role).
ENTRY_ROLES = frozenset({"owner", "manager", "responsible_officer", "technician"})
# Roles allowed to edit items / lines.
SETTINGS_ROLES = frozenset({"owner", "manager"})

MAX_LABEL_LEN = 60
MAX_OPTION_LEN = 50
MAX_OPTIONS = 20
MAX_LINES = 12
MAX_TEXT_LEN = 300
MAX_GRADE_LEN = 30

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# ---------------------------------------------------------------- helpers
def can_enter(user: dict | None) -> bool:
    return bool(user and user.get("active") and user.get("role") in ENTRY_ROLES)


def can_configure(user: dict | None) -> bool:
    return bool(user and user.get("active") and user.get("role") in SETTINGS_ROLES)


def normalize_digits(text: Any) -> str:
    return str(text if text is not None else "").translate(_DIGITS)


def normalize_text(text: Any) -> str:
    """Latin digits, Persian ی/ک, ZWNJ→space, collapsed whitespace."""
    s = normalize_digits(text)
    s = s.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    s = s.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", " ", s).strip()


def section_label(section: str) -> str:
    return SECTIONS.get(section, section)


def valid_section(section: str | None) -> str:
    s = (section or "").strip().lower()
    if s not in SECTIONS:
        raise ValueError("بخش نامعتبر است (اسلب / بلوم / بیلت).")
    return s


def split_list_text(text: str | None) -> list[str]:
    """Split admin-entered lists on newlines, «،», «,» or «؛» — dedupe, keep order."""
    raw = re.split(r"[\n،,؛;]+", str(text or ""))
    out: list[str] = []
    seen: set[str] = set()
    for part in raw:
        p = re.sub(r"\s+", " ", part).strip()
        if not p:
            continue
        key = normalize_text(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _line_key(section: str) -> str:
    return f"tundish_report_lines:{section}"


# ---------------------------------------------------------------- lines
def get_lines(db: Any, section: str) -> list[str]:
    raw = db.get_setting(_line_key(section))
    if raw:
        try:
            vals = [str(v).strip() for v in json.loads(raw) if str(v).strip()]
            if vals:
                return vals
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return list(DEFAULT_LINES.get(section, []))


def set_lines(db: Any, section: str, lines: Iterable[str] | str, updated_by: Any = None) -> list[str]:
    section = valid_section(section)
    vals = split_list_text(lines) if isinstance(lines, str) else split_list_text("\n".join(lines))
    if not vals:
        raise ValueError("حداقل یک خط / ماشین لازم است.")
    if len(vals) > MAX_LINES:
        raise ValueError(f"حداکثر {MAX_LINES} خط مجاز است.")
    for v in vals:
        if len(v) > MAX_OPTION_LEN:
            raise ValueError(f"نام خط «{v[:20]}…» طولانی است (حداکثر {MAX_OPTION_LEN} نویسه).")
    db.set_setting(_line_key(section), json.dumps(vals, ensure_ascii=False), updated_by=updated_by)
    return vals


# ---------------------------------------------------------------- item CRUD
def validate_item_def(
    label: str | None,
    item_type: str | None,
    options: Iterable[str] | str | None,
) -> tuple[str, str, list[str]]:
    lab = re.sub(r"\s+", " ", str(label or "")).strip()
    if not lab:
        raise ValueError("برچسب قلم خالی است.")
    if len(lab) > MAX_LABEL_LEN:
        raise ValueError(f"برچسب حداکثر {MAX_LABEL_LEN} نویسه است.")
    t = (item_type or "").strip()
    t = ITEM_TYPE_BY_LABEL.get(t, t).lower()
    if t not in ITEM_TYPES:
        raise ValueError("نوع قلم باید انتخابی، عددی یا متنی باشد.")
    if isinstance(options, str):
        opts = split_list_text(options)
    else:
        opts = split_list_text("\n".join(str(o) for o in (options or [])))
    if t == "choice":
        if not opts:
            raise ValueError("برای قلم انتخابی حداقل یک گزینه لازم است.")
        if len(opts) > MAX_OPTIONS:
            raise ValueError(f"حداکثر {MAX_OPTIONS} گزینه مجاز است.")
        for o in opts:
            if len(o) > MAX_OPTION_LEN:
                raise ValueError(f"گزینه «{o[:20]}…» طولانی است (حداکثر {MAX_OPTION_LEN} نویسه).")
    else:
        opts = []
    return lab, t, opts


def add_item(
    db: Any,
    section: str,
    *,
    label: str,
    item_type: str,
    options: Iterable[str] | str | None = None,
    required: bool = True,
    updated_by: Any = None,
) -> dict[str, Any]:
    section = valid_section(section)
    lab, t, opts = validate_item_def(label, item_type, options)
    return db.add_tundish_report_item(
        section=section, label=lab, item_type=t, options=opts, required=bool(required), updated_by=updated_by
    )


def update_item(
    db: Any,
    item_id: int,
    *,
    label: str | None = None,
    item_type: str | None = None,
    options: Iterable[str] | str | None = None,
    required: bool | None = None,
    updated_by: Any = None,
) -> dict[str, Any]:
    cur = db.get_tundish_report_item(int(item_id))
    if not cur:
        raise ValueError("قلم یافت نشد.")
    new_label = cur["label"] if label is None else label
    new_type = cur["item_type"] if item_type is None else item_type
    new_opts: Iterable[str] | str = cur["options"] if options is None else options
    lab, t, opts = validate_item_def(new_label, new_type, new_opts)
    return db.update_tundish_report_item(
        int(item_id), label=lab, item_type=t, options=opts, required=required, updated_by=updated_by
    ) or {}


def delete_item(db: Any, item_id: int) -> bool:
    if not db.delete_tundish_report_item(int(item_id)):
        raise ValueError("قلم یافت نشد.")
    return True


def move_item(db: Any, item_id: int, position: int) -> dict[str, Any]:
    pos = int(normalize_digits(position))
    if pos < 1:
        raise ValueError("جایگاه باید عدد ≥ ۱ باشد.")
    item = db.move_tundish_report_item(int(item_id), pos)
    if not item:
        raise ValueError("قلم یافت نشد.")
    return item


def describe_item(item: dict, idx: int | None = None) -> str:
    head = f"{idx}) " if idx is not None else ""
    req = "اجباری" if item.get("required") else "اختیاری"
    line = f"{head}{item.get('label')} — {ITEM_TYPES.get(item.get('item_type'), item.get('item_type'))} ({req})"
    if item.get("item_type") == "choice" and item.get("options"):
        line += "\n    گزینه‌ها: " + "، ".join(item["options"])
    return line


def describe_section_settings(db: Any, section: str) -> str:
    items = db.list_tundish_report_items(section)
    lines = get_lines(db, section)
    out = [
        f"⚙️ تنظیمات {TITLE_FA} — {section_label(section)}",
        "خطوط / ماشین‌ها: " + "، ".join(lines),
        "",
        "اقلام:" if items else "هنوز قلمی تعریف نشده است.",
    ]
    for i, it in enumerate(items, start=1):
        out.append(describe_item(it, i))
    return "\n".join(out)


# ---------------------------------------------------------------- value validation
def _parse_int(raw: Any, label: str, *, lo: int = 0, hi: int = 99999) -> int:
    s = normalize_digits(raw).strip()
    if not re.fullmatch(r"\d{1,6}", s):
        raise ValueError(f"«{label}» باید عدد صحیح باشد.")
    n = int(s)
    if n < lo or n > hi:
        raise ValueError(f"«{label}» باید بین {lo} و {hi} باشد.")
    return n


def match_line(raw: Any, lines: list[str]) -> str | None:
    """Exact (normalized) or prefix/CCM match of a line label."""
    s = normalize_text(raw)
    if not s:
        return None
    norm = {normalize_text(l): l for l in lines}
    if s in norm:
        return norm[s]
    m_ccm = re.search(r"ccm\s*(\d+)", s, re.I)
    if m_ccm:
        tag = f"ccm{m_ccm.group(1)}"
        for k, v in norm.items():
            if tag in k.lower().replace(" ", ""):
                return v
    m = re.search(r"(اسلب|بلوم|بیلت)\s*(\d*)", s)
    if m:
        cand = f"{m.group(1)} {m.group(2)}".strip()
        hits = [v for k, v in norm.items() if k == cand or k.startswith(cand + " ") or k.startswith(cand + "(")]
        if len(hits) == 1:
            return hits[0]
        if not m.group(2):
            same = [v for k, v in norm.items() if k.startswith(m.group(1))]
            if len(same) == 1:
                return same[0]
    for k, v in norm.items():
        if k.startswith(s):
            return v
    return None


def clean_fixed(key: str, raw: Any, *, lines: list[str]) -> Any:
    spec = FIXED_BY_KEY.get(key)
    if not spec:
        raise ValueError(f"فیلد ناشناخته: {key}")
    label = spec["label"]
    if raw is None or str(raw).strip() == "":
        raise ValueError(f"«{label}» الزامی است.")
    kind = spec["kind"]
    if kind == "int":
        lo = 0 if key == "melt_count" else 1
        return _parse_int(raw, label, lo=lo)
    if kind == "line":
        hit = match_line(raw, lines)
        if not hit:
            raise ValueError(f"«{label}» باید یکی از این‌ها باشد: " + "، ".join(lines))
        return hit
    s = re.sub(r"\s+", " ", normalize_digits(raw)).strip().strip(".،")
    if not s:
        raise ValueError(f"«{label}» الزامی است.")
    if len(s) > MAX_GRADE_LEN:
        raise ValueError(f"«{label}» حداکثر {MAX_GRADE_LEN} نویسه است.")
    return s


def match_option(raw: Any, options: list[str]) -> str | None:
    s = normalize_text(raw)
    for o in options:
        if normalize_text(o) == s:
            return o
    return None


def clean_item_value(item: dict, raw: Any) -> Any:
    """Return cleaned value or ``None`` when an optional item is left empty."""
    label = item.get("label") or "قلم"
    empty = raw is None or str(raw).strip() == ""
    if empty:
        if item.get("required"):
            raise ValueError(f"«{label}» الزامی است.")
        return None
    t = item.get("item_type")
    if t == "number":
        s = normalize_digits(raw).strip().replace("٫", ".").replace(",", ".")
        try:
            f = float(s)
        except ValueError as exc:
            raise ValueError(f"«{label}» باید عدد باشد.") from exc
        if f != f or abs(f) > 1e9:
            raise ValueError(f"«{label}» نامعتبر است.")
        return int(f) if float(f).is_integer() else round(f, 4)
    text = re.sub(r"\s+", " ", str(raw)).strip()
    if t == "choice":
        hit = match_option(text, item.get("options") or [])
        if hit:
            return hit
        # free value outside the configured options («سایر») — short only
        if len(text) > MAX_OPTION_LEN:
            raise ValueError(
                f"«{label}»: یکی از گزینه‌ها را انتخاب کنید یا متن کوتاه (حداکثر {MAX_OPTION_LEN} نویسه) بفرستید."
            )
        return text
    if len(text) > MAX_TEXT_LEN:
        raise ValueError(f"«{label}» حداکثر {MAX_TEXT_LEN} نویسه است.")
    return text


def validate_submission(
    db: Any,
    section: str,
    fixed_raw: dict[str, Any],
    items_raw: dict[Any, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Validate a full submission (web form or bot pending state).

    ``items_raw`` is keyed by item id (int or str). Returns
    ``(fields, item_values, errors)``; ``item_values`` keeps label/type snapshots.
    """
    section = valid_section(section)
    lines = get_lines(db, section)
    errors: list[str] = []
    fields: dict[str, Any] = {}
    for f in FIXED_FIELDS:
        try:
            fields[f["key"]] = clean_fixed(f["key"], fixed_raw.get(f["key"]), lines=lines)
        except ValueError as exc:
            errors.append(str(exc))
    values: list[dict[str, Any]] = []
    raw_by_id = {str(k): v for k, v in (items_raw or {}).items()}
    for it in db.list_tundish_report_items(section):
        try:
            val = clean_item_value(it, raw_by_id.get(str(it["id"])))
        except ValueError as exc:
            errors.append(str(exc))
            val = None
        values.append(
            {
                "item_id": int(it["id"]),
                "label": it["label"],
                "item_type": it["item_type"],
                "value": val,
            }
        )
    return fields, values, errors


# ---------------------------------------------------------------- summary / save
def format_value(v: Any) -> str:
    if v is None or v == "":
        return "—"
    return str(v)


def build_summary(section: str, fields: dict[str, Any], item_values: list[dict[str, Any]]) -> str:
    out = [f"🧾 {TITLE_FA} — {section_label(section)}"]
    for f in FIXED_FIELDS:
        out.append(f"{f['label']}: {format_value(fields.get(f['key']))}")
    if item_values:
        out.append("—")
        for it in item_values:
            out.append(f"{it.get('label')}: {format_value(it.get('value'))}")
    return "\n".join(out)


def save_report(
    db: Any,
    user: dict,
    section: str,
    fields: dict[str, Any],
    item_values: list[dict[str, Any]],
    *,
    source: str,
    raw_text: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from bot.jalali import format_date, format_datetime

    section = valid_section(section)
    now = now or datetime.now(timezone.utc)
    summary = build_summary(section, fields, item_values)
    return db.insert_tundish_report(
        section=section,
        fields=fields,
        items=item_values,
        summary_text=summary,
        raw_text=(raw_text or None),
        source=source,
        bale_user_id=user["bale_user_id"],
        actor_display_name=user.get("display_name"),
        created_at=now.isoformat(),
        created_at_tehran=format_datetime(now),
        jalali_date=format_date(now),
    )


def submit(
    db: Any,
    user: dict,
    section: str,
    fixed_raw: dict[str, Any],
    items_raw: dict[Any, Any],
    *,
    source: str,
    raw_text: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate + persist in one call. Returns ``(report|None, errors)``."""
    if not can_enter(user):
        return None, ["دسترسی ثبت گزارش تاندیش ندارید."]
    fields, values, errors = validate_submission(db, section, fixed_raw, items_raw)
    if errors:
        return None, errors
    rep = save_report(db, user, section, fields, values, source=source, raw_text=raw_text)
    return rep, []


def short_report_line(rep: dict) -> str:
    f = rep.get("fields") or {}
    who = rep.get("actor_display_name") or rep.get("bale_user_id")
    return (
        f"#{rep.get('id')} {rep.get('created_at_tehran')} — {section_label(rep.get('section'))} "
        f"{f.get('line') or ''} | تاندیش {f.get('tundish_no')} | سکوئنس {f.get('sequence')} | "
        f"{f.get('melt_count')} ذوب | مارک {f.get('steel_grade')} — {who}"
    )


# ---------------------------------------------------------------- free-text parse
_SEQ_RE = r"س[یي]?ک(?:وئ|و|ئ|ا)?ن[سص]"


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s\.\,،؛:;/\-\(\)]+", s) if t]


def parse_report_text(
    text: str,
    section: str,
    lines: list[str],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Best-effort parse of an operator sentence into fixed fields + item values.

    Returns ``{"fields": {...}, "items": {item_id: value}}`` with only the
    values that were confidently found. Callers validate with
    :func:`clean_fixed` / :func:`clean_item_value` and ask for the rest.
    """
    s = normalize_text(text)
    fields: dict[str, Any] = {}

    m = re.search(r"تاندیش\s*(?:شماره\s*)?:?\s*(\d+)", s)
    if m:
        fields["tundish_no"] = int(m.group(1))
    m = re.search(r"(?:اسلب|بلوم|بیلت)\s*\d*|ccm\s*\d+", s, re.I)
    if m:
        hit = match_line(m.group(0), lines)
        if hit:
            fields["line"] = hit
    m = re.search(_SEQ_RE + r"\s*:?\s*(\d+)", s)
    if m:
        fields["sequence"] = int(m.group(1))
    m = re.search(r"تعداد\s*ذوب\s*:?\s*(\d+)", s) or re.search(r"(\d+)\s*ذوب(?:ه|ی)?(?![\u0600-\u06FF])", s)
    if m:
        fields["melt_count"] = int(m.group(1))
    m = re.search(
        r"(?:مارک|گرید)(?:\s*(?:ذوب|فولاد))?\s*:?\s*([A-Za-z0-9][A-Za-z0-9\-_\.]*)", s
    )
    if m:
        fields["steel_grade"] = m.group(1).rstrip(".")

    # --- configurable items: segment text by item anchors (label before «—»)
    def anchor_of(label: str) -> tuple[str, str]:
        clean = re.sub(r"\([^)]*\)", " ", label)  # «مدت ریخته‌گری (دقیقه)» → anchor without unit
        parts = re.split(r"\s*[—–-]\s*", clean, maxsplit=1)
        return normalize_text(parts[0]), normalize_text(parts[1]) if len(parts) > 1 else ""

    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        a, _ = anchor_of(it.get("label") or "")
        if a:
            groups.setdefault(a, []).append(it)
    positions: list[tuple[int, int, str]] = []
    for a in groups:
        idx = s.find(a)
        if idx >= 0:
            positions.append((idx, idx + len(a), a))
    positions.sort()
    segments: dict[str, str] = {}
    for i, (start, end, a) in enumerate(positions):
        nxt = len(s)
        for st2, _e2, _a2 in positions[i + 1 :]:
            if st2 >= end:
                nxt = st2
                break
        seg = s[end:nxt]
        # «/» separates fields in some operator messages (billet/bloom sample)
        seg = seg.split("/", 1)[0]
        segments[a] = seg.strip(" .،")

    values: dict[int, Any] = {}
    for a, seg in segments.items():
        toks = set(_tokens(seg))
        group = groups[a]
        for it in group:
            t = it.get("item_type")
            _, suffix = anchor_of(it.get("label") or "")
            if t == "choice":
                best: tuple[int, str] | None = None
                for o in it.get("options") or []:
                    otoks = _tokens(normalize_text(o))
                    if otoks and all(ot in toks for ot in otoks):
                        if best is None or len(otoks) > best[0]:
                            best = (len(otoks), o)
                if best:
                    values[int(it["id"])] = best[1]
                elif len(group) == 1 and seg and len(_tokens(seg)) <= 4:
                    values[int(it["id"])] = seg
            elif t == "number":
                kw = _tokens(suffix)[0] if suffix else ""
                mm = re.search(re.escape(kw) + r"\s*(\d+(?:\.\d+)?)", seg) if kw else None
                if mm:
                    values[int(it["id"])] = mm.group(1)
                else:
                    nums = re.findall(r"\d+(?:\.\d+)?", seg)
                    if len(nums) == 1 and (len(group) == 1 or not kw):
                        values[int(it["id"])] = nums[0]
            elif t == "text" and len(group) == 1 and seg:
                values[int(it["id"])] = seg

    # Choice items whose anchor never appears (e.g. «علت پایان سکونس» written as
    # «بعلت محدودیت سکونس …»): match multi-word options against the whole text,
    # or capture the words after «بعلت / به علت» for a «علت…» item.
    all_toks = set(_tokens(s))
    for a, group in groups.items():
        if a in segments:
            continue
        for it in group:
            if it.get("item_type") != "choice" or int(it["id"]) in values:
                continue
            best = None
            for o in it.get("options") or []:
                otoks = _tokens(normalize_text(o))
                if len(otoks) >= 2 and all(ot in all_toks for ot in otoks):
                    if best is None or len(otoks) > best[0]:
                        best = (len(otoks), o)
            if best:
                values[int(it["id"])] = best[1]
                continue
            if "علت" in a:
                mm = re.search(r"ب(?:ه)?\s*علت\s+([^\d/\.،]+?)(?=\s*(?:\d|/|\.|،|$|سک|تعویض|پایان))", s)
                if mm and mm.group(1).strip():
                    cand = mm.group(1).strip()
                    opts = it.get("options") or []
                    hit = match_option(cand, opts)
                    if not hit:
                        pref = [o for o in opts if normalize_text(o).startswith(normalize_text(cand))]
                        hit = pref[0] if len(pref) == 1 else None
                    values[int(it["id"])] = hit or cand
    return {"fields": fields, "items": values}


__all__ = [
    "FIXED_FIELDS",
    "ITEM_TYPES",
    "SECTIONS",
    "SECTION_ORDER",
    "TITLE_FA",
    "add_item",
    "build_summary",
    "can_configure",
    "can_enter",
    "clean_fixed",
    "clean_item_value",
    "delete_item",
    "describe_item",
    "describe_section_settings",
    "get_lines",
    "move_item",
    "parse_report_text",
    "save_report",
    "set_lines",
    "submit",
    "update_item",
    "validate_submission",
]
