"""Parse post-cast tundish sequence Excel logs → DB rows + monthly aggregates.

Expected columns (Persian header row, sometimes 2-row header):
  ماشین | تاندیش | تاندیشکار | تعداد ذوب | مدت سکوئنس | ISG |
  ذوب اول (شماره/شروع) | ذوب آخر (شماره/پایان) | تعویض شرود | تعویض نازل بیرونی | تیوب چنجر؟
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from bot.jalali import format_month_year, parse_month_year_token
from services import main_goal_report as mg

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm(s: Any) -> str:
    t = str(s if s is not None else "").translate(_DIGITS)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return re.sub(r"\s+", " ", t).strip()


def _fold(s: Any) -> str:
    return _norm(s).casefold().replace(" ", "")


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = _norm(v).replace(",", "").replace("٬", "")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _yes(v: Any) -> int:
    s = _norm(v)
    if not s:
        return 0
    if s in {"دارد", "بله", "yes", "y", "1", "true"}:
        return 1
    if s in {"ندارد", "خیر", "no", "n", "0", "false"}:
        return 0
    return 0


def _section_from_machine(machine: str) -> str | None:
    f = _fold(machine)
    if "اسلب" in machine or "slab" in f:
        return "slab"
    if "بلوم" in machine or "bloom" in f:
        return "bloom"
    if "بیلت" in machine or "billet" in f:
        return "billet"
    return None


def detect_section_from_file(path: Path | str, filename: str | None = None) -> str | None:
    name = _norm(filename or Path(path).name)
    sec = _section_from_machine(name)
    if sec:
        return sec
    # peek first data machines
    try:
        parsed = parse_sequence_excel(path)
        secs = {r.section for r in parsed.rows if r.section}
        if len(secs) == 1:
            return next(iter(secs))
        # majority
        if secs:
            from collections import Counter
            c = Counter(r.section for r in parsed.rows if r.section)
            return c.most_common(1)[0][0]
    except Exception:  # noqa: BLE001
        pass
    return None


@dataclass
class SequenceRow:
    section: str
    machine: str = ""
    tundish_no: str = ""
    operator: str = ""
    melt_count: float | None = None
    sequence_minutes: float | None = None
    isg: str = ""
    first_melt_no: str = ""
    first_cast_start: str = ""
    last_melt_no: str = ""
    last_cast_end: str = ""
    shroud_replaced: int = 0
    outer_nozzle_replaced: int = 0
    tube_changer: int = 0


@dataclass
class SequenceParseResult:
    ok: bool
    error_fa: str | None = None
    year: int | None = None
    month: int | None = None
    period_key: str | None = None
    period_label: str = ""
    section: str | None = None
    rows: list[SequenceRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def tundish_count(self) -> int:
        return len(self.rows)

    @property
    def melt_sum(self) -> float:
        return float(sum(r.melt_count or 0 for r in self.rows))


def _detect_month_from_rows(rows: list[SequenceRow]) -> tuple[int | None, int | None]:
    for r in rows:
        for raw in (r.first_cast_start, r.last_cast_end):
            s = _norm(raw)
            m = re.search(r"(14\d{2})\s*[/\-]\s*(0?[1-9]|1[0-2])", s)
            if m:
                return int(m.group(1)), int(m.group(2))
            tok = parse_month_year_token(s)
            if tok:
                return tok
    return None, None


def parse_sequence_excel(path: Path | str) -> SequenceParseResult:
    path = Path(path)
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        return SequenceParseResult(ok=False, error_fa=f"خواندن Excel ناموفق: {exc}")
    ws = wb[wb.sheetnames[0]]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    if not grid:
        return SequenceParseResult(ok=False, error_fa="شیت خالی است")

    # Find header row containing ماشین / تاندیش
    header_i = None
    for i, row in enumerate(grid[:5]):
        joined = " ".join(_norm(c) for c in row if c is not None)
        if "ماشین" in joined and "تاندیش" in joined:
            header_i = i
            break
    if header_i is None:
        header_i = 0

    header = [_fold(c) for c in grid[header_i]]

    def col(*needles: str) -> int | None:
        for i, h in enumerate(header):
            if any(n in h for n in needles):
                return i
        return None

    # With 2-row headers, subheaders may be on next row — merge
    if header_i + 1 < len(grid):
        sub = grid[header_i + 1]
        for i, c in enumerate(sub):
            if i < len(header) and _norm(c):
                header[i] = header[i] + _fold(c)

    i_machine = col("ماشین", "machine")
    i_tundish = col("تاندیش") if True else None
    # first column named exactly tundish (not tundishkar)
    i_tundish = None
    for i, h in enumerate(header):
        if h == "تاندیش" or h == "tundish" or (h.startswith("تاندیش") and "کار" not in h and "ذوب" not in h):
            i_tundish = i
            break
    if i_tundish is None:
        i_tundish = col("tundish")
    i_op = col("تاندیشکار", "operator")
    i_melts = col("تعدادذوب", "تعداد ذوب".replace(" ", ""), "melt")
    # fix melts: search original
    for i, h in enumerate(header):
        if "تعدادذوب" in h or "تعدادذوب" == h or ("ذوب" in h and "تعداد" in h and "اول" not in h and "آخر" not in h):
            i_melts = i
            break
    i_dur = col("مدت", "سکوئنس", "duration")
    i_isg = col("isg")
    i_shroud = col("شرود", "shroud")
    i_nozzle = col("نازل", "nozzle")
    i_tube = col("تیوب", "tube")

    # first/last melt columns: by position after ISG often pairs
    data_start = header_i + 1
    # skip subheader row if it has no machine-like values
    if data_start < len(grid):
        probe = _norm(grid[data_start][i_machine] if i_machine is not None and i_machine < len(grid[data_start]) else "")
        if not probe or probe in {"شماره ذوب", "شروع ریخته گری"}:
            data_start += 1

    rows: list[SequenceRow] = []
    for raw in grid[data_start:]:
        if not any(c is not None and str(c).strip() for c in raw):
            continue
        machine = _norm(raw[i_machine]) if i_machine is not None and i_machine < len(raw) else ""
        if not machine or machine in {"ماشین"}:
            continue
        sec = _section_from_machine(machine)
        if not sec:
            continue

        def cell(idx: int | None) -> Any:
            if idx is None or idx >= len(raw):
                return None
            return raw[idx]

        # Heuristic for first/last cast timestamps: look for jalali datetime strings in row
        cast_times = []
        melt_nos = []
        for c in raw:
            s = _norm(c)
            if re.search(r"14\d{2}/\d{1,2}/\d{1,2}", s):
                cast_times.append(s)
            elif isinstance(c, (int, float)) and c > 1_000_000:
                melt_nos.append(str(int(c)))
            elif re.fullmatch(r"\d{6,}", s or ""):
                melt_nos.append(s)

        sr = SequenceRow(
            section=sec,
            machine=machine,
            tundish_no=_norm(cell(i_tundish)),
            operator=_norm(cell(i_op)),
            melt_count=_to_float(cell(i_melts)),
            sequence_minutes=_to_float(cell(i_dur)),
            isg=_norm(cell(i_isg)),
            first_melt_no=melt_nos[0] if melt_nos else "",
            first_cast_start=cast_times[0] if cast_times else "",
            last_melt_no=melt_nos[1] if len(melt_nos) > 1 else "",
            last_cast_end=cast_times[1] if len(cast_times) > 1 else (cast_times[0] if cast_times else ""),
            shroud_replaced=_yes(cell(i_shroud)),
            outer_nozzle_replaced=_yes(cell(i_nozzle)),
            tube_changer=_yes(cell(i_tube)),
        )
        rows.append(sr)

    if not rows:
        return SequenceParseResult(ok=False, error_fa="هیچ ردیف سکوئنس معتبری یافت نشد")

    year, month = _detect_month_from_rows(rows)
    # majority section
    from collections import Counter
    sec = Counter(r.section for r in rows).most_common(1)[0][0]
    if not year or not month:
        return SequenceParseResult(
            ok=False,
            error_fa="ماه جلالی از تاریخ سکوئنس‌ها تشخیص داده نشد",
            section=sec,
            rows=rows,
        )
    return SequenceParseResult(
        ok=True,
        year=year,
        month=month,
        period_key=f"m:{year:04d}-{month:02d}",
        period_label=format_month_year(year, month, named=True),
        section=sec,
        rows=rows,
        notes=[f"{len(rows)} سکوئنس، جمع ذوب {sum(r.melt_count or 0 for r in rows):g}"],
    )


# ---------------------------------------------------------------- split by month × section
def _ym(raw: str) -> tuple[int, int] | None:
    m = re.search(r"(14\d{2})\s*[/\-]\s*(0?[1-9]|1[0-2])(?!\d)", _norm(raw))
    return (int(m.group(1)), int(m.group(2))) if m else None


def row_period(r: SequenceRow) -> tuple[int, int] | None:
    """Month a sequence belongs to = month of its FIRST cast start (fallback: last cast end)."""
    return _ym(r.first_cast_start) or _ym(r.last_cast_end)


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def split_rows_by_period_section(
    parsed: SequenceParseResult, section: str | None = None
) -> tuple[dict[tuple[int, int, str], list[SequenceRow]], list[str]]:
    """Group sequence rows → {(year, month, section): rows}.

    * month: start month of each sequence (``row_period``); undated rows use the file month.
    * section: each row's own machine (اسلب ۱/۲ → slab, بلوم → bloom, بیلت ۱/۲ → billet). A
      caller-chosen ``section`` only overrides when the file holds a single section.
    * export-edge straddle: in the EARLIEST month of the file, a section whose rows all
      started there but ended in the next month (e.g. one slab sequence 03/31 18:18 → 04/01)
      is folded into the next month instead of creating a one-row month.
    """
    notes: list[str] = []
    secs = {r.section for r in parsed.rows if r.section}
    override = section if (section and len(secs) <= 1) else None
    if section and len(secs) > 1:
        notes.append(
            "فایل چند بخشی است؛ هر ردیف بر اساس ماشین خودش (اسلب/بلوم/بیلت) ذخیره شد "
            f"(بخش انتخابی «{mg.SECTION_LABEL_FA.get(section, section)}» فقط برای فایل تک‌بخشی اعمال می‌شود)."
        )
    fallback = (parsed.year, parsed.month) if parsed.year and parsed.month else None
    groups: dict[tuple[int, int, str], list[SequenceRow]] = {}
    for r in parsed.rows:
        ym = row_period(r) or fallback
        sec = override or r.section
        if not ym or not sec:
            continue
        groups.setdefault((ym[0], ym[1], sec), []).append(r)
    if len({(y, m) for y, m, _ in groups}) > 1:
        first = min((y, m) for y, m, _ in groups)
        nxt = _next_month(*first)
        for key in [k for k in groups if (k[0], k[1]) == first]:
            rows = groups[key]
            if rows and all(_ym(r.last_cast_end) == nxt for r in rows):
                groups.pop(key)
                groups.setdefault((nxt[0], nxt[1], key[2]), []).extend(rows)
                notes.append(
                    f"{len(rows)} سکوئنس {mg.SECTION_LABEL_FA.get(key[2], key[2])} که در "
                    f"{format_month_year(first[0], first[1], named=True)} شروع و در "
                    f"{format_month_year(nxt[0], nxt[1], named=True)} تمام شده (لبه فایل) "
                    f"به {format_month_year(nxt[0], nxt[1], named=True)} منظور شد."
                )
    return dict(sorted(groups.items())), notes

