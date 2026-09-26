"""Warehouse inbound report: delta between two inventory snapshots.

Inbound = brand-new items OR quantity increases (positive delta).
Decreases and unchanged quantities are omitted.
Only rows whose category_code is in the bot allowlist are included.
"""
from __future__ import annotations

from pathlib import Path
from typing import Collection, Iterable

import pandas as pd

from excel.id_parse import extract_item_id, extract_product_name
from excel.processor import _normalize_category_code, _normalize_key_part


STATUS_NEW = "قلم جدید"
STATUS_INCREASE = "افزایش موجودی"

INBOUND_COLUMNS = [
    "کد کالا",
    "شرح",
    "کد دسته",
    "مقدار قبلی",
    "مقدار جدید",
    "مقدار ورودی",
    "وضعیت",
]


def inventory_match_key(row: pd.Series | dict) -> str:
    """Stable match key: item id from کد و شرح کالا / id column, else normalized name."""
    get = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
    raw_id = get("id")
    if raw_id is not None and not (isinstance(raw_id, float) and pd.isna(raw_id)):
        text = str(raw_id).strip()
        if text and text.lower() != "nan":
            return f"id:{_normalize_key_part(text)}"

    desc = get("item_code_desc")
    if desc is None or (isinstance(desc, float) and pd.isna(desc)):
        desc = get("product_name")
    parsed = extract_item_id(desc)
    if parsed:
        return f"id:{_normalize_key_part(parsed)}"

    name = extract_product_name(desc) if desc is not None else ""
    if not name:
        name = str(desc or "").strip()
    return f"name:{_normalize_key_part(name)}"


def _qty(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        num = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    if pd.isna(num):
        return None
    return float(num)


def _display_id(row: pd.Series | dict) -> str:
    get = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
    raw_id = get("id")
    if raw_id is not None and not (isinstance(raw_id, float) and pd.isna(raw_id)):
        text = str(raw_id).strip()
        if text and text.lower() != "nan":
            return text
    desc = get("item_code_desc") or get("product_name")
    parsed = extract_item_id(desc)
    return parsed or "—"


def _display_desc(row: pd.Series | dict) -> str:
    get = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
    desc = get("item_code_desc")
    if desc is not None and not (isinstance(desc, float) and pd.isna(desc)):
        text = str(desc).strip()
        if text and text.lower() != "nan":
            return text
    name = get("product_name")
    if name is not None and not (isinstance(name, float) and pd.isna(name)):
        text = str(name).strip()
        if text and text.lower() != "nan":
            return text
    return "—"


def _build_snapshot(df: pd.DataFrame | None) -> dict[str, dict]:
    """Map match-key → {qty, category_code, id, desc, row} (last wins on duplicate keys)."""
    out: dict[str, dict] = {}
    if df is None or df.empty:
        return out
    for _, row in df.iterrows():
        key = inventory_match_key(row)
        if not key or key in {"id:", "name:"}:
            continue
        qty = _qty(row.get("quantity"))
        if qty is None:
            continue
        cat = _normalize_category_code(row.get("category_code"))
        out[key] = {
            "qty": qty,
            "category_code": cat,
            "item_id": _display_id(row),
            "desc": _display_desc(row),
        }
    return out


def compute_inbound_delta(
    previous: pd.DataFrame | None,
    current: pd.DataFrame | None,
    *,
    category_allowlist: Collection[str] | None = None,
) -> pd.DataFrame:
    """Return inbound rows (new or increased qty) filtered by category allowlist.

    ``category_allowlist`` should be the bot's active category_codes set.
    Rows with missing/unknown category are excluded when an allowlist is provided.
    If allowlist is None or empty, every inbound candidate is excluded (safe default
    matching inventory upload behaviour that refuses empty allowlists).
    """
    allowed = {
        str(c).zfill(4) if str(c).isdigit() else str(c)
        for c in (category_allowlist or [])
        if str(c).strip()
    }
    old_map = _build_snapshot(previous)
    new_map = _build_snapshot(current)
    rows: list[dict] = []
    for key, new_info in new_map.items():
        cat = new_info.get("category_code")
        if not allowed or not cat or cat not in allowed:
            continue
        new_qty = float(new_info["qty"])
        old_info = old_map.get(key)
        if old_info is None:
            rows.append(
                {
                    "کد کالا": new_info["item_id"],
                    "شرح": new_info["desc"],
                    "کد دسته": cat,
                    "مقدار قبلی": 0.0,
                    "مقدار جدید": new_qty,
                    "مقدار ورودی": new_qty,
                    "وضعیت": STATUS_NEW,
                }
            )
            continue
        old_qty = float(old_info["qty"])
        delta = new_qty - old_qty
        if delta > 0:
            rows.append(
                {
                    "کد کالا": new_info["item_id"],
                    "شرح": new_info["desc"],
                    "کد دسته": cat,
                    "مقدار قبلی": old_qty,
                    "مقدار جدید": new_qty,
                    "مقدار ورودی": delta,
                    "وضعیت": STATUS_INCREASE,
                }
            )
    out = pd.DataFrame(rows, columns=INBOUND_COLUMNS)
    if out.empty:
        return out
    return out.sort_values(
        ["وضعیت", "کد دسته", "کد کالا"], kind="stable"
    ).reset_index(drop=True)


def format_inbound_list_fa(
    inbound_df: pd.DataFrame | None,
    *,
    limit: int = 40,
    max_chars: int = 3500,
) -> list[str]:
    """Persian text chunks for Bale messages."""
    if inbound_df is None or inbound_df.empty:
        return ["هیچ قلم ورودی (جدید یا افزایش موجودی) در دسته‌های مجاز شناسایی نشد."]
    header = (
        "کد کالا | شرح | کد دسته | قبلی → جدید | ورودی | وضعیت\n"
        "──────── | ──── | ────── | ────────── | ───── | ──────"
    )
    lines: list[str] = []
    for _, row in inbound_df.head(int(limit)).iterrows():
        code = str(row.get("کد کالا") or "—")
        desc = str(row.get("شرح") or "—")
        if len(desc) > 48:
            desc = desc[:45] + "…"
        cat = str(row.get("کد دسته") or "—")
        prev_q = row.get("مقدار قبلی")
        new_q = row.get("مقدار جدید")
        inbound_q = row.get("مقدار ورودی")
        status = str(row.get("وضعیت") or "")
        lines.append(
            f"{code} | {desc} | {cat} | "
            f"{float(prev_q):g} → {float(new_q):g} | {float(inbound_q):g} | {status}"
        )
    omitted = max(0, len(inbound_df) - int(limit))
    if omitted:
        lines.append(f"… و {omitted} قلم دیگر (در فایل اکسل کامل ببینید).")

    chunks: list[str] = []
    current = header
    for line in lines:
        candidate = f"{current}\n{line}"
        if current != header and len(candidate) > max_chars:
            chunks.append(current)
            current = f"{header}\n{line}"
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def write_inbound_excel(
    inbound_df: pd.DataFrame,
    path: Path | str,
) -> Path:
    """Write inbound rows to an .xlsx file; returns the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame = inbound_df if inbound_df is not None else pd.DataFrame(columns=INBOUND_COLUMNS)
    frame.to_excel(out, index=False, engine="openpyxl")
    return out
