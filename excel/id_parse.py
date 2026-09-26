"""Extract material id from «کد و شرح کالا» (item_code_desc).

Default assumption (adjust here if the plant confirms a different format):

  Take the leading token of digits and optional letters before the first
  whitespace, spaced hyphen `` - ``, or en-dash ``–`` in the cell text.

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
_SPLIT_RE = re.compile(r"\s+[-–]\s+|\s+")
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
