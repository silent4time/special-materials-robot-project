"""Extract material id from «کد و شرح کالا» (item_code_desc).

Default assumption (adjust here if the plant confirms a different format):

  Take the leading token of digits and optional letters before the first
  whitespace, spaced hyphen `` - ``, en-dash ``–``, or em-dash ``—`` in the cell text.

Examples
--------
  ``1201ABC - اسید سولفوریک``  → id=``1201ABC``, name=``اسید سولفوریک``
  ``55 ماده``                  → id=``55``, name=``ماده``
  ``AB12–روغن``                → id=``AB12``, name=``روغن``

The token must contain at least one digit. Empty / no-match → None.
"""
from __future__ import annotations

import re

# Split on spaced hyphen/en-dash first, else first whitespace run.
_SPLIT_RE = re.compile(r"\s+[-–—]\s+|\s+")
# Leading id: letters/digits with at least one digit (easy to tighten later).
_ID_TOKEN_RE = re.compile(r"^[0-9A-Za-z]*\d[0-9A-Za-z]*$")


def extract_item_id(raw: object) -> str | None:
    """Return leading id token from item_code_desc, or None if unusable."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    parts = _SPLIT_RE.split(text, maxsplit=1)
    token = (parts[0] or "").strip()
    if not token or not _ID_TOKEN_RE.match(token):
        return None
    return token


def extract_product_name(raw: object) -> str:
    """Descriptive name after the id separator; falls back to full text."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return ""
    parts = _SPLIT_RE.split(text, maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        return parts[1].strip()
    return text


def split_item_code_desc(raw: object) -> tuple[str | None, str]:
    """Convenience: (id, product_name)."""
    return extract_item_id(raw), extract_product_name(raw)


# ---------------------------------------------------------------------------
# Contractor vs company (پیمانکار / شرکت) from شناسه مواد
# ---------------------------------------------------------------------------
# Plant rule (spec): a contractor material id has «0000» as its second group of
# 4 characters from the left, e.g. ``3787 0000 9002G`` → پیمانکار, while
# ``3781 2164 1302R`` → شرکت. Since 1405-07-14 the rule is AUTHORITATIVE (user rule):
# the importer, every منبع اصلی save and the analytics segment split derive the side from
# the id; the file's «پیمانکار / شرکت» cell is only used when the id is too short, and a
# disagreeing cell is reported in the upload summary (``apply_id_segment_rule``).
CONTRACTOR_ID_MARKER = "0000"
CONTRACTOR_LABEL_FA = "پیمانکار"
COMPANY_LABEL_FA = "شرکت"


def is_contractor_material_id(raw: object) -> bool | None:
    """True = contractor, False = company, None = id too short / blank."""
    if raw is None:
        return None
    text = "".join(str(raw).split())
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text or text.lower() == "nan" or len(text) < 8:
        return None
    return text[4:8] == CONTRACTOR_ID_MARKER


def contractor_or_company_for_id(raw: object) -> str:
    """«پیمانکار» / «شرکت» from the id, or "" when the id cannot tell."""
    flag = is_contractor_material_id(raw)
    if flag is None:
        return ""
    return CONTRACTOR_LABEL_FA if flag else COMPANY_LABEL_FA


def apply_id_segment_rule(df, *, id_col: str = "id", label_col: str = "contractor_or_company"):
    """Overwrite «پیمانکار / شرکت» from شناسه مواد (rule above). Returns (frame, mismatches).

    ``mismatches`` lists rows whose NON-blank cell disagreed with the id rule:
    {"id", "category_code", "file_label", "rule_label"}. Blank cells are filled silently.
    Rows whose id cannot tell keep their cell.
    """
    if df is None or getattr(df, "empty", True) or id_col not in df.columns:
        return df, []
    out = df.copy()
    if label_col not in out.columns:
        out[label_col] = ""
    mismatches: list[dict] = []
    labels = list(out[label_col])
    for pos, (raw_id, cur) in enumerate(zip(out[id_col], labels)):
        rule = contractor_or_company_for_id(raw_id)
        if not rule:
            continue
        cur_text = "" if cur is None else " ".join(str(cur).split())
        if cur_text.lower() in {"nan", "none"}:
            cur_text = ""
        if cur_text and cur_text != rule:
            mismatches.append(
                {
                    "id": str(raw_id).strip(),
                    "category_code": str(out["category_code"].iloc[pos]) if "category_code" in out.columns else "",
                    "file_label": cur_text,
                    "rule_label": rule,
                }
            )
        labels[pos] = rule
    out[label_col] = labels
    return out, mismatches


def segment_mismatch_note_fa(mismatches: list[dict], *, limit: int = 10) -> str:
    """Persian upload-summary warning for file labels that disagreed with the id rule."""
    if not mismatches:
        return ""
    lines = [
        f"\n⚠ «پیمانکار / شرکت» {len(mismatches)} ردیف با قاعده شناسه مواد (رقم ۵ تا ۸ = 0000 → پیمانکار) "
        "مغایر بود و از روی شناسه اصلاح شد:"
    ]
    for m in mismatches[:limit]:
        lines.append(f"• {m['id']} (کد {m.get('category_code') or '—'}): فایل «{m['file_label']}» ← قاعده «{m['rule_label']}»")
    if len(mismatches) > limit:
        lines.append(f"… و {len(mismatches) - limit} ردیف دیگر")
    return "\n".join(lines)

