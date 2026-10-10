"""Descriptive Persian file names for every generated report (bot + web).

Users only ever see the file name, so it must say what the file is and when it was
made — never an internal key or a user id. Uniqueness comes from a private
timestamped sub-folder (``unique_dir``), so the visible name stays clean.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_SEP = re.compile(r"[\s_—–\-]+")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(value: object) -> str:
    return str(value).translate(_FA_DIGITS)


def safe_part(text: object) -> str:
    """One name segment: no path separators/illegal chars, spaces → «_»."""
    s = _BAD.sub(" ", str(text or "")).strip()
    s = s.replace("(", " ").replace(")", " ").replace("،", " ").replace(",", " ")
    return _SEP.sub("_", s).strip("_.")


def jalali_stamp(when: datetime | None = None, *, with_time: bool = False) -> str:
    """«1405-07-19» (or «1405-07-19_0135») in Tehran time."""
    from bot.jalali import format_datetime, tehran_now

    dt = when or tehran_now()
    try:
        label = format_datetime(dt)  # «1405/07/19 01:35»
    except Exception:  # noqa: BLE001
        label = dt.strftime("%Y/%m/%d %H:%M")
    day, _, hm = label.partition(" ")
    out = day.replace("/", "-")
    if with_time and hm:
        out += "_" + hm.replace(":", "")[:4]
    return out


def fa_stem(*parts: object, when: datetime | None = None, with_time: bool = False, dated: bool = True) -> str:
    """``fa_stem("گزارش جامع", "مهر 1405")`` → «گزارش_جامع_مهر_1405_1405-07-19»."""
    segs = [safe_part(p) for p in parts if p not in (None, "")]
    if dated:
        segs.append(jalali_stamp(when, with_time=with_time))
    return "_".join(s for s in segs if s)[:180] or "گزارش"


def unique_dir(base: Path | str, tag: str = "r") -> Path:
    """Private per-report folder so two reports with the same Persian name never clash."""
    d = Path(base) / f"{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    d.mkdir(parents=True, exist_ok=True)
    return d
