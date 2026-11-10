"""v1.7.2 — free-text «تناژ هدف» question (bot + web assistant), e.g.
«برای ده تن تولید شمش بیلت چقدر مواد لازم است».

Recognises a tonnage (Persian/Arabic/Latin digits, thousands separators, Persian number
words «ده»، «بیست و پنج هزار»، «صد»، «۱۰ هزار») with the unit تن/تُن/ton plus a product
section (بیلت / بلوم / اسلب, also «شمش بیلت»). Runs exactly the same calculation as
«🎯 سناریو ۱ — تناژ هدف» (``services.main_goal_history.scenario_target``) for that
section only, and builds a readable Persian answer + the same PDF/Excel report.

«تختال» is NOT treated as اسلب: the codebase/data never use that word (v1.7.2 check);
the user gets the section buttons instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services import main_goal_history as mgh
from services import main_goal_report as mg
from services import permissions as perm

FEATURE = perm.MAIN_GOAL  # same access as «🎯 هدف اصلی» (owner / manager / responsible officer)
SECTION_FA = {"billet": "بیلت", "bloom": "بلوم", "slab": "اسلب"}
SECTION_ORDER = ("billet", "bloom", "slab")

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_UNITS = {"یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6,
          "هفت": 7, "هشت": 8, "نه": 9}
_TEENS = {"ده": 10, "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14, "پانزده": 15,
          "پونزده": 15, "شانزده": 16, "شونزده": 16, "هفده": 17, "هیفده": 17, "هجده": 18,
          "هیجده": 18, "نوزده": 19}
_TENS = {"بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60, "هفتاد": 70, "هشتاد": 80, "نود": 90}
_HUNDREDS = {"صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300, "چهارصد": 400, "پانصد": 500,
             "پونصد": 500, "ششصد": 600, "هفتصد": 700, "هشتصد": 800, "نهصد": 900}
_SCALES = {"هزار": 1000, "میلیون": 1_000_000}
_WORDS = {**_UNITS, **_TEENS, **_TENS, **_HUNDREDS}
_UNIT_TOKENS = {"تن", "تنی", "ton", "tons", "tonne", "tonnes"}
_SECTION_TOKENS = {
    "بیلت": "billet", "billet": "billet",
    "بلوم": "bloom", "bloom": "bloom",
    "اسلب": "slab", "slab": "slab",
}
_PRODUCT_RE = re.compile(r"(?<!\w)(شمش|تولید|تناژ|ریخته ?گری|محصول)(?!\w)")
_NEED_RE = re.compile(r"(مواد|ماده|لازم|نیاز|احتیاج|می ?خواد|می ?خواهد|چقدر|چه قدر|چه مقدار|چی|چه)")
MAX_TONS = 10_000_000


def normalize(text: Any) -> str:
    from services.assistant_brain import normalize as ab_norm

    s = str(text or "").translate(_DIGITS)
    s = re.sub(r"(?<=\d)[,٬،'](?=\d{3}(?!\d))", "", s)  # 10,000 / ۱۰٬۰۰۰ → 10000
    s = s.replace("٫", ".")
    s = re.sub(r"(?<=\d)(?=[^\d\s.,])", " ", s)  # «۱۰تن» / «10tons» → «10 تن»
    return ab_norm(s)


def _num_token(tok: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", tok)) or tok in _WORDS or tok in _SCALES


def _parse_tokens(tokens: list[str]) -> float | None:
    total = 0.0
    cur = 0.0
    seen = False
    for tok in tokens:
        if tok == "و":
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            cur += float(tok)
            seen = True
        elif tok in _WORDS:
            cur += _WORDS[tok]
            seen = True
        elif tok in _SCALES:
            total += (cur or 1) * _SCALES[tok]
            cur = 0.0
            seen = True
        else:
            return None
    return (total + cur) if seen else None


def parse_tons(t: str) -> list[float]:
    """All tonnages in normalized text (number words / digits immediately before تن)."""
    toks = t.split()
    out: list[float] = []
    for i, tok in enumerate(toks):
        if tok not in _UNIT_TOKENS or i == 0:
            continue
        j = i
        while j > 0 and (_num_token(toks[j - 1]) or (toks[j - 1] == "و" and j > 1 and _num_token(toks[j - 2]))):
            j -= 1
        while j < i and toks[j] == "و":
            j += 1
        if j == i:
            continue
        v = _parse_tokens(toks[j:i])
        if v is not None and 0 < v <= MAX_TONS:
            out.append(float(v))
    return out


def parse_sections(t: str) -> list[str]:
    out: list[str] = []
    for tok in t.split():
        sec = _SECTION_TOKENS.get(tok)
        if sec is None:
            for word, s in _SECTION_TOKENS.items():
                if tok.startswith(word) and len(tok) <= len(word) + 2:  # «بیلتی»، «اسلبها»
                    sec = s
                    break
        if sec and sec not in out:
            out.append(sec)
    return out


@dataclass
class TonnageQuery:
    targets: dict[str, float] = field(default_factory=dict)  # complete: section → tons
    tons: float | None = None  # a tonnage without section
    section: str | None = None  # a section without tonnage

    @property
    def complete(self) -> bool:
        return bool(self.targets)

    @property
    def missing(self) -> str | None:
        if self.targets:
            return None
        if self.tons and not self.section:
            return "section"
        if self.section and not self.tons:
            return "tons"
        return "both"


def parse(text: Any) -> TonnageQuery | None:
    """None = not a target-tonnage question. Otherwise the parsed (maybe partial) query."""
    t = normalize(text)
    if not t or len(t) > 300:
        return None
    if re.search(r"(?<!\w)تاندیش(?!\w)", t) and not re.search(r"(?<!\w)(تن|ton|tons)(?!\w)", t):
        return None  # «نیاز مواد برای ۱۰ تاندیش» = N-tundish report
    tons = parse_tons(t)
    secs = parse_sections(t)
    product = bool(_PRODUCT_RE.search(t))
    need = bool(_NEED_RE.search(t))
    if tons:
        if not (secs or product):
            return None  # «۱۰ تن آجر داریم؟» is not a production question
    elif not (secs and product and need) and "تناژ هدف" not in t:
        return None
    q = TonnageQuery()
    if tons and secs:
        if len(secs) == len(tons):
            q.targets = dict(zip(secs, tons))
        elif len(secs) == 1:
            q.targets = {secs[0]: tons[0]}
        else:
            q.tons = tons[0]  # several sections, one tonnage → ask which section
    elif tons:
        q.tons = tons[0]
    elif len(secs) == 1:
        q.section = secs[0]
    return q


# ---------------------------------------------------------------- display helpers
def fa_num(v: float | None, digits: int = 1) -> str:
    """Persian digits, «٬» thousands, «٫» decimals; no trailing zeros."""
    if v is None:
        return "—"
    txt = f"{v:,.{digits}f}" if digits else f"{v:,.0f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    if txt in {"-0", ""}:
        txt = "0"
    return txt.replace(",", "٬").replace(".", "٫").translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def tons_fa(v: float) -> str:
    return fa_num(v, 0 if float(v).is_integer() else 2)


def question_label(targets: dict[str, float]) -> str:
    return " و ".join(f"{tons_fa(v)} تن شمش {SECTION_FA[s]}" for s, v in targets.items())


# ---------------------------------------------------------------- answer
COL_ITEM = "ماده"
COL_UNIT = "واحد"
COL_NEED = "مقدار لازم"
COL_STOCK = "موجودی فعلی"
COL_SHORT = "کسری"
COL_SECTION = "بخش"
TABLE_COLS = [COL_ITEM, COL_UNIT, COL_NEED, COL_STOCK, COL_SHORT]


@dataclass
class TonnageAnswer:
    ok: bool
    text: str
    targets: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)  # one per material line
    result: Any = None  # mgh.ScenarioResult (PDF/Excel)
    model: Any = None
    table: dict[str, Any] | None = None  # assistant_tables format
    denied: bool = False


def denied_text() -> str:
    return f"⛔ برای این پرسش دسترسی ندارید (همان دسترسی منوی «{perm.FEATURE_LABEL_FA.get(FEATURE, 'هدف اصلی')}»)."


def clarify_text(q: TonnageQuery) -> str:
    miss = q.missing
    if miss == "section":
        return (f"برای {tons_fa(q.tons or 0)} تن تولیدِ کدام شمش؟\n"
                "یکی از دکمه‌های بیلت / بلوم / اسلب را بزنید.")
    if miss == "tons":
        return (f"چند تن شمش {SECTION_FA[q.section or 'billet']}؟\n"
                "تناژ را به تن بفرستید (مثلاً «۱۰ تن» یا «ده تن»).")
    return ("برای محاسبهٔ مواد لازم، تناژ و نوع شمش را بنویسید؛ مثلاً "
            "«برای ده تن تولید شمش بیلت چقدر مواد لازم است».\nیا اول بخش را انتخاب کنید:")


def clarify_buttons(q: TonnageQuery) -> list[str]:
    """Complete follow-up questions (pressing = typing them)."""
    miss = q.missing
    if miss == "section":
        t = tons_fa(q.tons or 0)
        return [f"{t} تن شمش {fa}" for fa in ("بیلت", "بلوم", "اسلب")]
    if miss == "tons":
        fa = SECTION_FA[q.section or "billet"]
        return [f"{t} تن شمش {fa}" for t in ("۱۰", "۱۰۰", "۱٬۰۰۰", "۱۰٬۰۰۰")]
    return [f"۱۰ تن شمش {fa}" for fa in ("بیلت", "بلوم", "اسلب")]


def parse_amount(text: Any) -> float | None:
    """Reply to «چند تن؟»: «۱۰»، «ده»، «۱۰ تن»، «بیست هزار»."""
    t = normalize(text)
    tons = parse_tons(t)
    if tons:
        return tons[0]
    toks = [x for x in t.split() if x not in _UNIT_TOKENS]
    if toks and all(_num_token(x) or x == "و" for x in toks):
        v = _parse_tokens(toks)
        if v is not None and 0 < v <= MAX_TONS:
            return float(v)
    return None


def _rate_card_rows(inventory: Any, sec: str, tundish_n: float | None) -> list[dict[str, Any]]:
    """Materials one or more tundishes of ``sec`` consume, from منبع اصلی rates.

    Used when the stored monthly consumption has no positive quantity for the
    section. Same pipeline as «نیاز مواد برای N تاندیش» (patching × N, renovation
    within the annual cap, casting floor only for the sections named in محل استفاده).
    شرود «سطح ریخته گری اسلب» therefore never appears for بیلت.
    """
    from services.n_tundish_report import (
        COL_CODE,
        COL_KEYWORD,
        COL_NEED,
        COL_SHORT,
        COL_STOCK,
        COL_UNIT,
        build_rows,
    )

    try:
        n = int(round(float(tundish_n or 0)))
    except (TypeError, ValueError):
        n = 0
    if n < 1:
        n = 1
    res = build_rows(inventory, sec, n)
    if getattr(res, "error", None) or not res.rows:
        return []
    out = []
    for row in res.rows:
        need = row.get(COL_NEED)
        if not isinstance(need, (int, float)):
            continue
        code = str(row.get(COL_CODE) or "").strip()
        keyword = str(row.get(COL_KEYWORD) or "").strip()
        name = keyword or code
        if code and keyword and not keyword.startswith("جمع"):
            name = f"{code} — {keyword}"
        stock = row.get(COL_STOCK)
        short = row.get(COL_SHORT)
        out.append({
            COL_SECTION: SECTION_FA[sec],
            COL_ITEM: name,
            COL_UNIT: _unit_fa(row.get(COL_UNIT)),
            COL_NEED: float(need),
            COL_STOCK: float(stock) if isinstance(stock, (int, float)) else None,
            COL_SHORT: float(short) if isinstance(short, (int, float)) and float(short) > 0 else (
                0.0 if isinstance(short, (int, float)) else None
            ),
            "_from_rates": True,
        })
    return out


def export_files(ans: "TonnageAnswer", *, report_dir: Any, letterhead_path: Any = None) -> tuple[Any, Any]:
    """PDF + Excel: the answer table first, then the full «سناریو ۱ — تناژ هدف» report."""
    import copy

    from services.file_names import fa_stem

    res = copy.copy(ans.result)
    res.sections = list((ans.table or {}).get("sections") or []) + list(ans.result.sections)
    res.title = ans.table["title"] if ans.table else res.title
    res.subtitle = (ans.table or {}).get("subtitle") or res.subtitle
    stem = fa_stem("مواد لازم", *[f"{tons_fa(v)} تن {SECTION_FA[s]}" for s, v in ans.targets.items()],
                   with_time=True)
    return mgh.export_scenario(res, stem=stem, report_dir=report_dir, letterhead_path=letterhead_path)


def build_answer(db: Any, user: dict | None, targets: dict[str, float], inventory: Any = None) -> TonnageAnswer:
    """Same calculation as «🎯 سناریو ۱ — تناژ هدف» for the asked section(s) only."""
    if not perm.can(user, FEATURE):
        return TonnageAnswer(ok=False, text=denied_text(), targets=targets, denied=True)
    targets = {s: float(v) for s, v in targets.items() if s in SECTION_FA and v and v > 0}
    if not targets:
        return TonnageAnswer(ok=False, text=clarify_text(TonnageQuery()), targets=targets)
    model = mgh.build_history_model(mgh.load_history(db), inventory)
    result = mgh.scenario_target(model, dict(targets), period_text="")
    if not result.ok:
        return TonnageAnswer(ok=False, text=result.error_fa or "محاسبه انجام نشد.", targets=targets, model=model)
    plan = {r["بخش"]: r for r in result.sections[0]["rows"]}
    tundish_by_sec = {}
    for sec in targets:
        tun = _num((plan.get(SECTION_FA[sec]) or {}).get("تاندیش_لازم"))
        tundish_by_sec[sec] = tun
    rows: list[dict[str, Any]] = []
    for v in mgh.material_need_values(model, result.params["resolved"], tundish_by_sec):
        mm = v["material"]
        short = v["shortage"]
        rows.append({
            COL_SECTION: SECTION_FA[v["section"]],
            COL_ITEM: mm.name,
            COL_UNIT: _unit_fa(mm.unit),
            COL_NEED: v["need"],
            COL_STOCK: v["stock"],
            COL_SHORT: short if (short is not None and short > 0) else (0.0 if short is not None else None),
        })
    lines = [f"🎯 مواد لازم برای تولید {question_label(targets)}"]
    lines.append(f"مبنا: نرخ مصرف هر قلم بر تن تولید همان بخش در سابقهٔ ذخیره‌شده ({_fa_hist(model)}).")
    for sec in SECTION_ORDER:
        if sec not in targets:
            continue
        p = plan.get(SECTION_FA[sec]) or {}
        tun = p.get("تاندیش_لازم")
        tun_txt = fa_num(float(tun), 0) if _num(tun) is not None else "—"
        sec_rows = [x for x in rows if x[COL_SECTION] == SECTION_FA[sec]]
        from_rates = False
        if not sec_rows:
            sec_rows = _rate_card_rows(inventory, sec, tundish_by_sec.get(sec))
            if sec_rows:
                from_rates = True
                rows.extend(sec_rows)
        n_short = sum(1 for x in sec_rows if (x[COL_SHORT] or 0) > 0)
        lines.append("")
        lines.append(f"🔹 بخش {SECTION_FA[sec]} — {tons_fa(targets[sec])} تن | تاندیش لازم ≈ {tun_txt}")
        if not sec_rows:
            lines.append("برای این بخش نه مصرف ثبت‌شده‌ای هست و نه نرخ مواد در منبع اصلی.")
            continue
        if from_rates:
            lines.append(
                f"در سابقهٔ مصرف این بخش مقدار ماده‌ای ثبت نشده؛ "
                f"فهرست زیر مصرف همان {tun_txt} تاندیش از نرخ منبع اصلی است "
                f"(پچینگ و نوسازی؛ سطح ریخته‌گری فقط اگر محل استفاده همین بخش باشد)."
            )
        lines.append(f"{fa_num(len(sec_rows), 0)} قلم فقط از بخش {SECTION_FA[sec]}"
                     + (f"؛ {fa_num(n_short, 0)} قلم کسری دارد." if n_short else "؛ کسری ندارد."))
        from services import assistant_tables as at

        disp = []
        for x in sec_rows:
            disp.append({
                COL_ITEM: x[COL_ITEM],
                COL_UNIT: x[COL_UNIT] or "—",
                COL_NEED: _qty_fa(x[COL_NEED], x[COL_UNIT]),
                COL_STOCK: "نامشخص" if x[COL_STOCK] is None else fa_num(x[COL_STOCK], 2),
                COL_SHORT: _qty_fa(x[COL_SHORT], x[COL_UNIT]) if (x[COL_SHORT] or 0) > 0 else "ندارد",
            })
        lines += at.text_table(TABLE_COLS, disp, max_rows=15, widths={COL_ITEM: 22})
        from services.units import is_countable

        if any(is_countable(x[COL_UNIT]) for x in sec_rows):
            lines.append("مقدار اقلام شمارشی (عدد) به عدد صحیح، رو به بالا گرد شده است.")
        if any(x[COL_STOCK] is None for x in sec_rows):
            lines.append("«نامشخص» = قلم به منبع اصلی وصل نشده است.")
        ex = model.excluded_materials(sec)
        if ex:
            lines.append("کنار گذاشته (متعلق به بخش دیگر طبق «محل استفاده»): "
                         + "، ".join(f"{mm.name}" for mm in ex) + ".")
    for w in result.warnings:
        lines.append(f"⚠ {w}")
    rate_rows = [x for x in rows if x.get("_from_rates")]
    if rate_rows:
        for block in result.sections:
            if block.get("title") == "مواد مورد نیاز برای تناژ هدف" and not block.get("rows"):
                block["rows"] = [
                    {
                        "بخش": x[COL_SECTION],
                        "ماده": x[COL_ITEM],
                        "واحد": x[COL_UNIT] or "—",
                        "نیاز_بر_اساس_تن": "—",
                        "نیاز_بر_اساس_تاندیش": "—",
                        "نیاز_پیشنهادی": x[COL_NEED],
                        "موجودی_منبع_اصلی": "—" if x[COL_STOCK] is None else x[COL_STOCK],
                        "کسری": x[COL_SHORT] if (x[COL_SHORT] or 0) > 0 else ("0" if x[COL_SHORT] is not None else "—"),
                    }
                    for x in rate_rows
                ]
        notes = result.sections[-1] if result.sections else None
        if notes and notes.get("title") == "قواعد و محدودیت‌ها":
            notes.setdefault("rows", []).append({
                "یادداشت": "چون در سابقهٔ مصرف این بخش مقدار مثبتی نبود، فهرست مواد از نرخ منبع اصلی و به تعداد تاندیش لازم ساخته شد. شرود سطح ریخته‌گری اسلب جز مواد بیلت نیست.",
            })
    table = {
        "title": f"مواد لازم برای تولید {question_label(targets)}",
        "subtitle": f"محاسبهٔ «سناریو ۱ — تناژ هدف» | {model.history_label()}",
        "sections": [{
            "title": f"بخش {SECTION_FA[sec]} — {tons_fa(targets[sec])} تن",
            "columns": TABLE_COLS,
            "rows": [_table_row(x) for x in rows if x[COL_SECTION] == SECTION_FA[sec]],
        } for sec in SECTION_ORDER if sec in targets],
    }
    return TonnageAnswer(ok=True, text="\n".join(lines), targets=targets, rows=rows, result=result,
                         model=model, table=table)


def _table_row(x: dict[str, Any]) -> dict[str, Any]:
    from services.units import round_qty

    def q(v: float | None) -> Any:
        return "—" if v is None else round_qty(v, x[COL_UNIT])

    return {
        COL_ITEM: x[COL_ITEM],
        COL_UNIT: x[COL_UNIT] or "—",
        COL_NEED: q(x[COL_NEED]),
        COL_STOCK: "—" if x[COL_STOCK] is None else round(float(x[COL_STOCK]), 2),
        COL_SHORT: q(x[COL_SHORT]) if (x[COL_SHORT] or 0) > 0 else ("ندارد" if x[COL_SHORT] is not None else "—"),
    }


def _qty_fa(v: float | None, unit: str) -> str:
    """Countable units (عدد، ست …) round UP to whole pieces; weights keep 2 decimals."""
    if v is None:
        return "—"
    from services.units import is_countable

    if is_countable(unit):
        import math

        return fa_num(float(math.ceil(v - 1e-9)), 0)
    return fa_num(v, 2 if abs(v) < 100 else 1)


def _unit_fa(u: Any) -> str:
    from services.units import unit_fa

    s = unit_fa(u)
    return {"kg": "کیلوگرم"}.get(s.lower(), s) if s else ""


def _fa_hist(model: Any) -> str:
    n, t = model.n_months, len(model.tonnage_months)
    s = f"{fa_num(n, 0)} ماه"
    if t != n:
        s += f"، {fa_num(t, 0)} ماه با تناژ"
    return s
