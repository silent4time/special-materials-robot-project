"""OCR + structured parse for KSC production screenshots (shared by bot + web).

Source rule (user decision, 1405-07): main-goal production photos must come ONLY from
the CASTING tab («ریخته گری») of otsteel.ksc.ir/productionstatistics.

  • ``detect_report_tab`` classifies OCR text as casting / furnace / unknown.
    Furnace tab («کوره») signals: header «شماره کوره», columns «تعداد به اسلب» /
    «تعداد به بلوم بیلت», chart titles «… به تفکیک کوره ها», repeated «وزن مذاب».
    NOTE: the tab strip («کوره | کوره پاتیلی | ریخته گری») is visible on every tab,
    so the bare words «ریخته گری»/«کوره» are NOT used as evidence.
  • Furnace or undeterminable tab → rejected (``tab_rejected=True``) with a Persian
    alarm; callers must not store it.
  • Casting tab → ``parse_casting_ocr_text``: per-CCM rows (or CCM columns) →
    section tonnage (slab=CCM1+2, bloom=CCM3, billet=CCM4+5) + melt counts.
    ⚠ The real casting-tab layout has not been seen yet; the parser is heuristic and
    every result carries ``needs_validation=True`` until validated on real screenshots.
  • Furnace parsing (``parse_furnace_ocr_text`` / ``from_known_furnace``) is kept only
    for audit of legacy/provisional rows — never for storing new production.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.jalali import PERSIAN_MONTH_NAME_TO_NUM, format_month_year, parse_month_year_token
from services import main_goal_report as mg

logger = logging.getLogger(__name__)

CCM_TO_SECTION = mg.CCM_TO_SECTION
TAB_LABEL_FA = {
    "casting": "تب ریخته‌گری",
    "furnace": "تب کوره",
    "manual": "ورود دستی",
    "excel": "اکسل",
    "unknown": "نامشخص",
}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass
class ProductionOCRResult:
    ok: bool
    error_fa: str | None = None
    year: int | None = None
    month: int | None = None
    period_key: str | None = None
    period_label: str = ""
    slab_tons: float = 0.0
    bloom_tons: float = 0.0
    billet_tons: float = 0.0
    melt_count: float | None = None
    melt_weight_kg: float | None = None
    product_weight_kg: float | None = None
    slab_count: float | None = None
    bloom_billet_count: float | None = None
    melts_per_day: float | None = None
    report_tab: str = "unknown"  # casting | furnace | manual | excel | unknown
    tab_rejected: bool = False  # True → wrong/undeterminable tab; never store
    tab_evidence: dict[str, Any] = field(default_factory=dict)
    needs_validation: bool = False  # casting parser not yet validated on real screenshots
    ccm_tons: dict[int, float] = field(default_factory=dict)
    ccm_melts: dict[int, float] = field(default_factory=dict)
    ocr_raw_text: str = ""
    ocr_confidence: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_tons(self) -> float:
        return float(self.slab_tons + self.bloom_tons + self.billet_tons)

    def section_melts(self) -> dict[str, float | None]:
        """slab=CCM1+2, bloom=CCM3, billet=CCM4+5 melt counts (None when unknown)."""
        out: dict[str, float | None] = {"slab": None, "bloom": None, "billet": None}
        for ccm_no, melts in self.ccm_melts.items():
            sec = CCM_TO_SECTION.get(int(ccm_no))
            if sec and melts is not None:
                out[sec] = float(out[sec] or 0) + float(melts)
        return out

    def as_production_stats(self) -> mg.ProductionStats:
        return mg.ProductionStats(
            slab_tons=self.slab_tons,
            bloom_tons=self.bloom_tons,
            billet_tons=self.billet_tons,
            notes=list(self.notes),
            missing=list(self.missing),
        )

    def to_fields_json(self) -> str:
        return json.dumps(
            {
                "year": self.year,
                "month": self.month,
                "report_tab": self.report_tab,
                "tab_rejected": self.tab_rejected,
                "tab_evidence": self.tab_evidence,
                "needs_validation": self.needs_validation,
                "ccm_melts": {str(k): v for k, v in self.ccm_melts.items()},
                "section_melts": self.section_melts(),
                "melt_count": self.melt_count,
                "melt_weight_kg": self.melt_weight_kg,
                "product_weight_kg": self.product_weight_kg,
                "slab_count": self.slab_count,
                "bloom_billet_count": self.bloom_billet_count,
                "melts_per_day": self.melts_per_day,
                "ccm_tons": {str(k): v for k, v in self.ccm_tons.items()},
                "slab_tons": self.slab_tons,
                "bloom_tons": self.bloom_tons,
                "billet_tons": self.billet_tons,
                "fields": self.fields,
                "notes": self.notes,
                "missing": self.missing,
            },
            ensure_ascii=False,
        )


def _norm(s: Any) -> str:
    t = str(s if s is not None else "").translate(_DIGITS)
    t = t.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    t = t.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", " ", t).strip()


def _to_float(token: str) -> float | None:
    s = _norm(token).replace(",", "").replace("٬", "").replace("،", "").replace(" ", "")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in {".", "-", "+"}:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if abs(v) < 1e15 else None


def _run_tesseract(path: Path) -> tuple[str, float | None]:
    try:
        import pytesseract
        from PIL import Image, ImageOps, ImageFilter, ImageEnhance
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("برای OCR به pytesseract و Pillow نیاز است.") from exc

    img = Image.open(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    # Cap ultra-large phone photos for speed
    if max(w, h) > 2200:
        scale = 2200 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    elif max(w, h) < 1400:
        scale = 1400 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))

    gray = ImageOps.autocontrast(img.convert("L"))
    gray = ImageEnhance.Contrast(gray).enhance(1.4)
    gray = gray.filter(ImageFilter.SHARPEN)

    texts: list[str] = []
    confs: list[float] = []
    for lang, cfg in (("fas+eng", "--psm 6"), ("eng", "--psm 6"), ("fas+eng", "--psm 4")):
        try:
            data = pytesseract.image_to_data(
                gray, lang=lang, config=cfg, output_type=pytesseract.Output.DICT
            )
            text = pytesseract.image_to_string(gray, lang=lang, config=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tesseract failed %s %s: %s", lang, cfg, exc)
            continue
        texts.append(text or "")
        vals = []
        for c in data.get("conf") or []:
            try:
                v = float(c)
            except (TypeError, ValueError):
                continue
            if v >= 0:
                vals.append(v)
        if vals:
            confs.append(sum(vals) / len(vals) / 100.0)
    merged = "\n".join(texts)
    conf = (sum(confs) / len(confs)) if confs else None
    return merged, conf


def _detect_month(text: str) -> tuple[int | None, int | None, str]:
    n = _norm(text)
    # Date filter range: 1405/04/01 … 1405/04/31 → month 4. Persian OCR often reads
    # «۱۴۰۵» as 1305/1335 (۴→۳), so 13xx years are lifted to 14xx; months are voted.
    full = re.findall(r"(1[34]\d{2})\s*[/\-]\s*(0?[1-9]|1[0-2])\s*[/\-]\s*(\d{1,2})(?!\d)", n)
    if full:
        votes: dict[tuple[int, int], int] = {}
        for y, mo, _d in full:
            yy = int(y)
            if yy < 1400:
                yy = 1400 + yy % 100 if yy % 100 < 50 else yy
            votes[(yy, int(mo))] = votes.get((yy, int(mo)), 0) + 1
        (yy, mo), _cnt = max(votes.items(), key=lambda kv: kv[1])
        fixed = any(int(y) < 1400 for y, _m, _d in full)
        return yy, mo, "date-filter" + ("+year-ocr-fix" if fixed else "")
    ranges = re.findall(r"(14\d{2})\s*[/\-]\s*(0?[1-9]|1[0-2])(?!\d)", n)
    if ranges:
        # prefer the first start date
        y, mo = ranges[0]
        return int(y), int(mo), "ym-slash"
    # The page header shows TODAY («سه شنبه ۱۴ مهر ۱۴۰۵») — never the report period.
    n = re.sub(r"(?:(?:یک|دو|سه|چهار|پنج)\s*)?(?:شنبه|جمعه)\s*\d{1,2}\s*\S+\s*\d{4}", " ", n)
    tok = parse_month_year_token(n)
    if tok:
        return tok[0], tok[1], "month-name"
    lat = {
        "farvardin": 1, "ordibehesht": 2, "khordad": 3, "tir": 4, "mordad": 5,
        "shahrivar": 6, "mehr": 7, "aban": 8, "azar": 9, "dey": 10, "bahman": 11,
        "esfand": 12,
    }
    low = n.casefold()
    for name, num in lat.items():
        mm = re.search(rf"{name}\s*(14\d{{2}})", low)
        if mm:
            return int(mm.group(1)), num, f"latin:{name}"
    for name, num in PERSIAN_MONTH_NAME_TO_NUM.items():
        # «مهر ۱۴۰۵» as a period label; «۱۴ مهر ۱۴۰۵» (day before the name) is today's header date
        mm = re.search(rf"(?<!\d)(?<!\d\s){name}\s*(14\d{{2}})(?!\d)", n)
        if mm:
            return int(mm.group(1)), num, f"fa:{name}"
    return None, None, "not-found"


def _numbers(text: str) -> list[float]:
    out = []
    for tok in re.findall(r"[\d٬,]{2,}(?:\.\d+)?", _norm(text)):
        v = _to_float(tok)
        if v is not None:
            out.append(v)
    return out


def _allocate_tons_from_furnace(
    product_kg: float,
    slab_count: float | None,
    bloom_billet_count: float | None,
) -> tuple[float, float, float, list[str]]:
    """Split product tons by melt destination counts. Bloom+billet stay combined in bloom
    if we cannot split further; billet stays 0 and a note is added.
    """
    notes = []
    product_tons = product_kg / 1000.0
    sc = float(slab_count or 0)
    bbc = float(bloom_billet_count or 0)
    total_c = sc + bbc
    if total_c <= 0:
        notes.append("شمارش اسلب/بلوم‌بیلت نیست — کل تناژ محصول در اسلب گذاشته شد.")
        return product_tons, 0.0, 0.0, notes
    slab = product_tons * (sc / total_c)
    bloom_billet = product_tons * (bbc / total_c)
    # Keep bloom_billet in bloom_tons; billet_tons=0 until casting report splits them
    notes.append(
        "تب کوره: تناژ محصول به نسبت تعداد ذوب اسلب در برابر بلوم/بیلت تقسیم شد؛ "
        "تفکیک بلوم از بیلت بدون گزارش ریخته‌گری ممکن نیست (بلوم‌بیلت در ستون بلوم)."
    )
    return slab, bloom_billet, 0.0, notes


def parse_furnace_ocr_text(text: str, *, confidence: float | None = None) -> ProductionOCRResult:
    result = ProductionOCRResult(
        ok=False, ocr_raw_text=text, ocr_confidence=confidence, report_tab="furnace"
    )
    year, month, src = _detect_month(text)
    result.fields["month_source"] = src
    if year and month:
        result.year, result.month = year, month
        result.period_key = f"m:{year:04d}-{month:02d}"
        result.period_label = format_month_year(year, month, named=True)
    else:
        result.missing.append("ماه جلالی از فیلتر تاریخ تشخیص داده نشد")

    n = _norm(text)
    nums = _numbers(text)
    # Heuristic: furnace totals — large kg weights (~1e7–1e9) and smaller counts
    big = sorted([v for v in nums if v >= 1_000_000], reverse=True)
    medium = [v for v in nums if 50 <= v <= 5000]
    small_day = [v for v in nums if 1 <= v <= 80]

    # Prefer explicit labeled extraction when Latin labels survived OCR
    low = n.casefold()
    def near(label_res: list[str]) -> float | None:
        for lab in label_res:
            m = re.search(lab + r".{0,40}?([\d٬,]{2,}(?:\.\d+)?)", n, flags=re.I)
            if m:
                return _to_float(m.group(1))
        return None

    melt_w = near([r"وزن\s*مذاب", r"melt\s*weight", r"وزنمذاب"])
    prod_w = near([r"وزن\s*محصول", r"product\s*weight", r"وزنمحصول"])
    melts = near([r"تعداد\s*ذوب(?!\s*در)", r"\bmelts?\b", r"تعدادذوب"])
    slab_c = near([r"به\s*اسلب", r"to\s*slab", r"اسلب"])
    bb_c = near([r"بلوم\s*بیلت", r"bloom\s*/?\s*billet", r"به\s*بلوم"])
    mpd = near([r"ذوب\s*در\s*روز", r"melts?\s*/?\s*day", r"per\s*day"])

    # Fallback: pick two largest as melt/product weights (melt usually >= product)
    if melt_w is None and len(big) >= 1:
        melt_w = big[0]
        result.notes.append("وزن مذاب از بزرگ‌ترین عدد OCR تخمین زده شد")
    if prod_w is None and len(big) >= 2:
        # product usually second-largest close to melt
        cand = [b for b in big if b != melt_w]
        prod_w = cand[0] if cand else None
        if prod_w:
            result.notes.append("وزن محصول از دومین عدد بزرگ OCR تخمین زده شد")
    if melts is None:
        # total melts often ~400–1200; exclude Jalali years 14xx and huge weights
        cand = [v for v in medium if 400 <= v <= 1200 and not (1400 <= v <= 1499)]
        if cand:
            melts = max(cand)
            result.notes.append(f"تعداد ذوب تخمینی از OCR: {melts:g}")
    # Labeled «تعداد 930» (without ذوب) — common on furnace cards
    if melts is None or (melts and 1400 <= melts <= 1499):
        m = re.search(r"تعداد\s+([\d٬,]{2,4})(?!\s*ذوب\s*در)", n)
        if m:
            v = _to_float(m.group(1))
            if v and 50 <= v <= 2000 and not (1400 <= v <= 1499):
                melts = v
    if slab_c is None or bb_c is None:
        # counts often hundreds
        cand = sorted([v for v in medium if 100 <= v <= 900], reverse=True)
        if slab_c is None and cand:
            slab_c = cand[0]
        if bb_c is None and len(cand) >= 2:
            bb_c = cand[1]
    if mpd is None and small_day:
        # melts/day total often 20–40
        cand = [v for v in small_day if 15 <= v <= 40]
        if cand:
            mpd = max(cand)

    result.melt_weight_kg = melt_w
    result.product_weight_kg = prod_w
    result.melt_count = melts
    result.slab_count = slab_c
    result.bloom_billet_count = bb_c
    result.melts_per_day = mpd

    if prod_w and prod_w > 0:
        slab, bloom, billet, notes = _allocate_tons_from_furnace(prod_w, slab_c, bb_c)
        result.slab_tons, result.bloom_tons, result.billet_tons = slab, bloom, billet
        result.notes.extend(notes)
    elif melt_w and melt_w > 0:
        slab, bloom, billet, notes = _allocate_tons_from_furnace(melt_w, slab_c, bb_c)
        result.slab_tons, result.bloom_tons, result.billet_tons = slab, bloom, billet
        result.notes.append("وزن محصول نبود — از وزن مذاب استفاده شد")
        result.notes.extend(notes)
    else:
        result.missing.append("وزن محصول/مذاب از OCR خوانده نشد")

    if result.total_tons <= 0:
        result.error_fa = "استخراج تناژ از عکس کوره ناموفق بود. مقادیر را دستی وارد کنید."
        return result
    if not result.period_key:
        result.error_fa = "تناژ خوانده شد ولی ماه تشخیص نشد. ماه را دستی بفرستید."
        return result
    result.ok = True
    return result


# ---------------------------------------------------------------- tab detection
ALARM_FURNACE_FA = (
    "❌ عکس باید از تب «ریخته گری» صفحه آمار تولید باشد؛ این تصویر از تب کوره است. "
    "لطفاً تب ریخته گری را باز کنید و دوباره عکس بفرستید."
)
ALARM_UNKNOWN_FA = (
    "❌ عکس باید از تب «ریخته گری» صفحه آمار تولید باشد؛ نوع تب این تصویر قابل تشخیص نبود "
    "(جدول ماشین‌های ریخته‌گری CCM دیده نشد). لطفاً تب ریخته گری را باز کنید و از کل جدول "
    "همراه با فیلتر تاریخ، واضح و مستقیم دوباره عکس بفرستید."
)
CASTING_VALIDATION_NOTE_FA = (
    "⚠ پارسر تب ریخته‌گری هنوز با عکس واقعی اعتبارسنجی نشده — اعداد CCM را بررسی کنید."
)

# Strong furnace-tab markers (fuzzy for observed OCR noise: «تفکپک», «بپلت», …)
_FURNACE_STRONG = {
    "furnace_no_header": r"شماره\s*کوره",
    "by_furnaces_chart": r"تف\S{0,3}ی?\S{0,2}\s*کوره\s*ها|کوره\s*ها\s*\(",
    "count_to_slab": r"(?:تعداد\s*)?(?<!\S)به\s*اسلب",
    # combined «بلوم بیلت» (furnace destination) — not «بلوم» + «بیلت ۱/۲» machines
    # NOT the casting subtotal row «مجموع بلوم بیلت ها» (real casting screenshot, 1405-07)
    "count_to_bloom_billet": r"(?<!مجموع )(?<!مجموع)بلوم\s*/?\s*ب[^\s\d]{0,2}لت(?!\s*[-_]?\s*[12](?!\d))(?!\s*ها)",
}
# Strong casting-tab markers seen on real «ریخته گری» screenshots (otsteel productionstatistics):
# chart titles «وزن محصول به تفکیک ریخته گری (ماهیانه/سالیانه)», subtotal rows and the CCM header.
_CASTING_STRONG = {
    "by_casting_chart": r"تف\S{0,3}ی?\S{0,2}\s*ری?خ\S{0,2}ه\s*گری",
    "slab_subtotal": r"مجموع\s*اسلب",
    "bloom_billet_subtotal": r"مجموع\s*بلوم",
    "ccm_no_header": r"شماره\s*ccm|ccm\s*شماره",
}
_FURNACE_WEAK = {
    "melt_weight": r"وزن\s*م[ذد]اب",
    "furnace_en": r"\bfurnace\b|\beaf\b",
}
_CASTING_WEAK = {
    "by_machines": r"تفکیک\s*ماشین|ماشین\s*های\s*ریخته",
    "strand": r"استرند|\bstrand\b",
    "casting_en": r"\bcaster\b|\bcasting\b|\bccm\b",
    "ingot": r"شمش|تختال|\bslab\s*/\s*bloom\b",
}

_CONF_DIGIT = {"l": "1", "i": "1", "|": "1", "!": "1", "o": "0"}


def _ccm_tokens(line: str) -> list[tuple[int, int]]:
    """Return [(char_pos, ccm_no)] machine tokens found in an (already normalized) line."""
    low = line.casefold()
    out: list[tuple[int, int]] = []

    def add(pos: int, n: int | None) -> None:
        if n and 1 <= n <= 5 and not any(abs(p - pos) <= 2 for p, _ in out):
            out.append((pos, n))

    def dig(tok: str) -> int | None:
        tok = _CONF_DIGIT.get(tok, tok)
        return int(tok) if tok.isdigit() else None

    for m in re.finditer(r"ccm\s*[-_#]?\s*([1-5])(?!\d)", low):
        add(m.start(), int(m.group(1)))
    for m in re.finditer(r"(?:ماشین|ماشين|خط)\s*(?:ریخته\s*گری\s*)?(?:شماره\s*)?([1-5])(?!\d)", low):
        add(m.start(), int(m.group(1)))
    for m in re.finditer(r"\bslab\s*[-_]?\s*([12li|!])(?![\d,])", low):
        n = dig(m.group(1))
        add(m.start(), n)
    for m in re.finditer(r"\bbillet\s*[-_]?\s*([12li|!])(?![\d,])", low):
        n = dig(m.group(1))
        add(m.start(), {1: 4, 2: 5}.get(n or 0))
    for m in re.finditer(r"\bbloom\b(?!\s*/\s*billet)(?!\s+billet(?!\s*[12li|!]))(?!\s*:)", low):
        add(m.start(), 3)
    # Persian names: «اسلب ۱»، «۱ اسلب»، «بیلت ۲»، «بلوم»
    for m in re.finditer(r"(?<!به )اسلب\s*[-_]?\s*([12])(?![\d,])|(?<![\d,])([12])\s*اسلب", line):
        add(m.start(), int(m.group(1) or m.group(2)))
    for m in re.finditer(r"(?<!بلوم )(?<!بلوم/)ب[یي]لت\s*[-_]?\s*([12])(?![\d,])|(?<![\d,])([12])\s*ب[یي]لت", line):
        add(m.start(), {1: 4, 2: 5}[int(m.group(1) or m.group(2))])
    for m in re.finditer(r"(?<!به )بلوم(?!\s*/?\s*ب[^\s\d]{0,2}لت(?!\s*[12]))", line):
        add(m.start(), 3)
    out.sort()
    return out


def detect_report_tab(text: str) -> tuple[str, dict[str, Any]]:
    """Classify OCR text → ("casting" | "furnace" | "unknown", evidence)."""
    n = _norm(text)
    ev: dict[str, Any] = {"furnace": [], "furnace_weak": [], "casting_weak": [], "ccms": []}
    for key, pat in _FURNACE_STRONG.items():
        if re.search(pat, n, flags=re.I):
            ev["furnace"].append(key)
    for key, pat in _FURNACE_WEAK.items():
        hits = len(re.findall(pat, n, flags=re.I))
        if key == "melt_weight" and hits >= 3:
            ev["furnace_weak"].append(f"{key}x{hits}")
        elif key != "melt_weight" and hits:
            ev["furnace_weak"].append(key)
    for key, pat in _CASTING_WEAK.items():
        if re.search(pat, n, flags=re.I):
            ev["casting_weak"].append(key)
    ev["casting_strong"] = [k for k, pat in _CASTING_STRONG.items() if re.search(pat, n, flags=re.I)]
    ccms: set[int] = set()
    for line in n.splitlines():
        for _pos, no in _ccm_tokens(line):
            ccms.add(no)
    ev["ccms"] = sorted(ccms)
    f_strong = len(ev["furnace"])
    f_score = f_strong + (0.5 if ev["furnace_weak"] else 0)
    c_score = (len(ccms) if len(ccms) >= 2 else 0) + 0.5 * len(ev["casting_weak"]) + len(ev["casting_strong"])
    ev["furnace_score"], ev["casting_score"] = f_score, c_score
    c_strong = len(ev["casting_strong"])
    if c_strong >= 1 and f_strong == 0:
        tab = "casting"
    elif f_strong >= 2 and len(ccms) < 3:
        tab = "furnace"
    elif len(ccms) >= 3 and f_strong == 0:
        tab = "casting"
    elif len(ccms) >= 2 and f_strong == 0 and ev["casting_weak"]:
        tab = "casting"
    elif f_strong >= 1 and len(ccms) < 2:
        tab = "furnace"
    else:
        tab = "unknown"
    ev["decision"] = tab
    return tab, ev


def _rejected(text: str, confidence: float | None, tab: str, ev: dict[str, Any]) -> ProductionOCRResult:
    r = ProductionOCRResult(
        ok=False,
        ocr_raw_text=text,
        ocr_confidence=confidence,
        report_tab=tab,
        tab_rejected=True,
        tab_evidence=ev,
        error_fa=ALARM_FURNACE_FA if tab == "furnace" else ALARM_UNKNOWN_FA,
    )
    year, month, _src = _detect_month(text)
    if year and month and 1390 <= year <= 1499:
        r.year, r.month = year, month
        r.period_label = format_month_year(year, month, named=True)
    r.notes.append(f"تب تشخیص‌داده‌شده: {tab} — ذخیره نشد")
    return r


# ---------------------------------------------------------------- casting parser
_TON_ROW = re.compile(r"product|\bton|tonnage|weight|تولید|وزن|تناژ|\bتن\b", re.I)
_MELT_ROW = re.compile(r"heat|melt|ذوب|تعداد", re.I)
_TOTAL_ROW = re.compile(r"جمع|مجموع|\btotal\b", re.I)


def _line_numbers(line: str) -> list[float]:
    out = []
    for tok in re.findall(r"\d[\d٬,]*(?:\.\d+)?", line):
        if len(re.sub(r"\D", "", tok)) < 2:
            continue  # single digits are machine numbers / noise
        v = _to_float(tok)
        if v is not None:
            out.append(v)
    return out


def _kg_to_tons(v: float) -> float:
    return v / 1000.0 if v >= 1_000_000 else v


def _pick_tons_melts(nums: list[float]) -> tuple[float | None, float | None, str]:
    """From one machine row, choose (monthly tons, melts). Uses tons/melt ∈ [50, 300]."""
    nums = [v for v in nums if not (1390 <= v <= 1499 and float(v).is_integer())]
    tons_c = sorted({_kg_to_tons(v) for v in nums if v >= 1000})
    melt_c = sorted({v for v in nums if 1 <= v <= 1500 and float(v).is_integer()}, reverse=True)
    # KSC EAF heats weigh ~165–175 t; avg-weight columns (t or kg) can look like melt
    # counts, so first try the typical heat-weight window, then a wide sanity window.
    for lo, hi in ((140.0, 210.0), (50.0, 300.0)):
        for m in melt_c:
            ok_t = [t for t in tons_c if lo <= t / m <= hi]
            if ok_t:
                ok_t.sort(reverse=True)
                best = ok_t[0]
                close = [t for t in ok_t if t >= best * 0.9]
                return min(close), m, "ratio"  # melt vs product weight → product (smaller)
    if tons_c:
        return max(tons_c), (melt_c[0] if melt_c else None), "max"
    return None, (melt_c[0] if melt_c else None), "none"


def _parse_casting_columns(lines: list[str]) -> tuple[dict[int, float], dict[int, float], list[str]]:
    """Header line with ≥3 machine tokens followed by numeric rows (CCM columns)."""
    tons: dict[int, float] = {}
    melts: dict[int, float] = {}
    notes: list[str] = []
    for i, line in enumerate(lines):
        toks = _ccm_tokens(line)
        order = [no for _p, no in toks]
        if len(set(order)) < 3 or len(order) != len(set(order)) or _line_numbers(line):
            continue
        k = len(order)
        for nxt in lines[i + 1 : i + 8]:
            nums = _line_numbers(nxt)
            if len(_ccm_tokens(nxt)) >= 3 and not nums:
                continue  # another header (e.g. CCM row then Section row)
            if len(nums) not in (k, k + 1):
                continue
            if len(nums) == k + 1:
                head, tail = nums[0], nums[-1]
                if abs(sum(nums[:-1]) - tail) <= 0.02 * max(tail, 1):
                    nums = nums[:-1]
                elif abs(sum(nums[1:]) - head) <= 0.02 * max(head, 1):
                    nums = nums[1:]
                else:
                    nums = nums[:-1]
            is_melt = bool(_MELT_ROW.search(nxt)) and not _TON_ROW.search(nxt)
            if not is_melt and not _TON_ROW.search(nxt):
                is_melt = all(v <= 1500 for v in nums)
            target = melts if is_melt else tons
            if target:
                continue
            row_kg = max(nums) >= 1_000_000
            for no, v in zip(order, nums):
                target[no] = (v / 1000.0 if row_kg else v) if not is_melt else v
            if row_kg and not is_melt:
                notes.append("ستون‌های CCM به کیلوگرم بودند → تن")
        if tons:
            notes.append("چیدمان ستونی CCM")
            break
    return tons, melts, notes


def _parse_casting_rows(lines: list[str]) -> tuple[dict[int, float], dict[int, float], float | None, list[str]]:
    tons: dict[int, float] = {}
    melts: dict[int, float] = {}
    total: float | None = None
    notes: list[str] = []
    for line in lines:
        toks = _ccm_tokens(line)
        nums = _line_numbers(line)
        if not nums:
            continue
        if not toks:
            if _TOTAL_ROW.search(line) and total is None:
                t, _m, _how = _pick_tons_melts(nums)
                total = t
            continue
        if len({no for _p, no in toks}) != 1:
            continue
        words = line.split()
        pos = toks[0][0]
        prefix_words = len(line[:pos].split())
        if not (prefix_words <= 2 or prefix_words >= len(words) - 3):
            continue  # machine name must be the first/last cell of the row
        no = toks[0][1]
        if no in tons:
            continue
        t, m, how = _pick_tons_melts(nums)
        if t is None:
            continue
        tons[no] = t
        if m is not None:
            melts[no] = m
        if how != "ratio":
            notes.append(f"CCM{no}: تناژ بدون کنترل نسبت تن/ذوب انتخاب شد")
    if tons:
        notes.append("چیدمان سطری CCM")
    return tons, melts, total, notes


def parse_casting_ocr_text(
    text: str, *, confidence: float | None = None, evidence: dict[str, Any] | None = None
) -> ProductionOCRResult:
    result = ProductionOCRResult(
        ok=False,
        ocr_raw_text=text,
        ocr_confidence=confidence,
        report_tab="casting",
        tab_evidence=evidence or {},
        needs_validation=True,
    )
    year, month, src = _detect_month(text)
    result.fields["month_source"] = src
    if year and month and 1390 <= year <= 1499:
        result.year, result.month = year, month
        result.period_key = f"m:{year:04d}-{month:02d}"
        result.period_label = format_month_year(year, month, named=True)
    else:
        result.missing.append("ماه جلالی از فیلتر تاریخ تشخیص داده نشد")

    lines = [ln for ln in _norm_lines(text) if ln]
    tons, melts, notes = _parse_casting_columns(lines)
    total_row = None
    if len(tons) < 3:
        r_tons, r_melts, total_row, r_notes = _parse_casting_rows(lines)
        if len(r_tons) > len(tons):
            tons, melts, notes = r_tons, r_melts, r_notes
    result.ccm_tons = {int(k): float(v) for k, v in sorted(tons.items())}
    result.ccm_melts = {int(k): float(v) for k, v in sorted(melts.items())}
    for no, t in result.ccm_tons.items():
        sec = CCM_TO_SECTION[no]
        setattr(result, f"{sec}_tons", getattr(result, f"{sec}_tons") + t)
    if result.ccm_melts:
        result.melt_count = float(sum(result.ccm_melts.values()))
    result.notes.extend(notes)
    result.notes.append(CASTING_VALIDATION_NOTE_FA)
    missing_ccm = [n for n in range(1, 6) if n not in result.ccm_tons]
    if missing_ccm:
        result.missing.append("CCM خوانده‌نشده: " + "، ".join(f"CCM{n}" for n in missing_ccm))
    if total_row and result.total_tons > 0:
        diff = abs(total_row - result.total_tons) / max(total_row, 1)
        result.fields["total_row_tons"] = total_row
        if diff > 0.03:
            result.notes.append(
                f"جمع سطر «جمع» ({total_row:,.0f}) با جمع CCMها ({result.total_tons:,.0f}) {diff*100:.0f}٪ اختلاف دارد"
            )
    result.fields["section_melts"] = result.section_melts()

    empty = [mg.SECTION_LABEL_FA[s] for s in ("slab", "bloom", "billet") if getattr(result, f"{s}_tons") <= 0]
    if result.total_tons <= 0:
        result.error_fa = "تب ریخته‌گری تشخیص داده شد ولی تناژ CCMها از عکس خوانده نشد. مقادیر را دستی وارد کنید."
        return result
    if empty:
        result.error_fa = (
            "تناژ همه بخش‌ها از تب ریخته‌گری خوانده نشد (خالی: " + "، ".join(empty)
            + "). عکس واضح‌تر بفرستید یا دستی اصلاح کنید."
        )
        return result
    if not result.period_key:
        result.error_fa = "تناژ خوانده شد ولی ماه تشخیص نشد. ماه را دستی بفرستید."
        return result
    result.ok = True
    return result


def _norm_lines(text: str) -> list[str]:
    return [_norm(ln) for ln in (text or "").splitlines()]


def parse_production_ocr_text(text: str, *, confidence: float | None = None) -> ProductionOCRResult:
    """Detect tab; only the casting tab is parsed — furnace/unknown are rejected."""
    tab, ev = detect_report_tab(text)
    if tab != "casting":
        return _rejected(text, confidence, tab, ev)
    return parse_casting_ocr_text(text, confidence=confidence, evidence=ev)


def ocr_production_image(path: Path | str) -> ProductionOCRResult:
    path = Path(path)
    if not path.is_file():
        return ProductionOCRResult(ok=False, error_fa=f"فایل تصویر یافت نشد: {path}")
    try:
        text, conf = _run_tesseract(path)
    except RuntimeError as exc:
        return ProductionOCRResult(ok=False, error_fa=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR failed")
        return ProductionOCRResult(ok=False, error_fa=f"خطای OCR: {exc}")
    if not (text or "").strip():
        return ProductionOCRResult(
            ok=False,
            error_fa="OCR متنی برنگرداند. عکس واضح‌تر یا اصلاح دستی.",
            ocr_confidence=conf,
        )
    return parse_production_ocr_text(text, confidence=conf)


def apply_manual_corrections(
    base: ProductionOCRResult | None,
    *,
    year: int | None = None,
    month: int | None = None,
    slab_tons: float | None = None,
    bloom_tons: float | None = None,
    billet_tons: float | None = None,
    melt_count: float | None = None,
    melt_weight_kg: float | None = None,
    product_weight_kg: float | None = None,
    slab_count: float | None = None,
    bloom_billet_count: float | None = None,
    melts_per_day: float | None = None,
    ccm_tons: dict[int, float] | None = None,
) -> ProductionOCRResult:
    r = base or ProductionOCRResult(ok=False)
    # Manual values are user-confirmed; a rejected furnace draft never becomes "furnace" data.
    if r.tab_rejected or r.report_tab not in ("casting", "excel"):
        r.report_tab = "manual"
        r.tab_rejected = False
    if year is not None:
        r.year = int(year)
    if month is not None:
        r.month = int(month)
    if r.year and r.month:
        r.period_key = f"m:{r.year:04d}-{r.month:02d}"
        r.period_label = format_month_year(r.year, r.month, named=True)
    if melt_weight_kg is not None:
        r.melt_weight_kg = float(melt_weight_kg)
    if product_weight_kg is not None:
        r.product_weight_kg = float(product_weight_kg)
    if slab_count is not None:
        r.slab_count = float(slab_count)
    if bloom_billet_count is not None:
        r.bloom_billet_count = float(bloom_billet_count)
    if melts_per_day is not None:
        r.melts_per_day = float(melts_per_day)
    if melt_count is not None:
        r.melt_count = float(melt_count)
    if ccm_tons:
        r.ccm_tons = {int(k): float(v) for k, v in ccm_tons.items()}
        r.slab_tons = r.bloom_tons = r.billet_tons = 0.0
        for ccm_no, tons in r.ccm_tons.items():
            sec = CCM_TO_SECTION.get(int(ccm_no))
            if sec:
                setattr(r, f"{sec}_tons", getattr(r, f"{sec}_tons") + float(tons))
    if product_weight_kg is not None and slab_tons is None and bloom_tons is None:
        slab, bloom, billet, notes = _allocate_tons_from_furnace(
            float(product_weight_kg), r.slab_count, r.bloom_billet_count
        )
        r.slab_tons, r.bloom_tons, r.billet_tons = slab, bloom, billet
        r.notes.extend(notes)
    if slab_tons is not None:
        r.slab_tons = float(slab_tons)
    if bloom_tons is not None:
        r.bloom_tons = float(bloom_tons)
    if billet_tons is not None:
        r.billet_tons = float(billet_tons)
    r.notes.append("اصلاح دستی اعمال شد")
    if r.period_key and r.total_tons > 0:
        r.ok = True
        r.error_fa = None
    else:
        r.ok = False
        r.error_fa = r.error_fa or "پس از اصلاح دستی هنوز ماه یا تناژ ناقص است."
    return r


def result_summary_fa(r: ProductionOCRResult) -> str:
    lines = []
    if r.period_label:
        lines.append(f"ماه: {r.period_label}")
    if r.report_tab:
        lines.append(f"منبع گزارش: {TAB_LABEL_FA.get(r.report_tab, r.report_tab)}")
    if r.tab_rejected:
        lines.append("⛔ رد شد — فقط عکس تب «ریخته گری» پذیرفته می‌شود.")
        return "\n".join(lines)
    lines.append(
        f"تناژ: اسلب {r.slab_tons:,.1f} | بلوم {r.bloom_tons:,.1f} | "
        f"بیلت {r.billet_tons:,.1f} | جمع {r.total_tons:,.1f}"
    )
    if r.ccm_tons:
        lines.append(
            "CCM (تن): " + " | ".join(f"CCM{k} {v:,.0f}" for k, v in sorted(r.ccm_tons.items()))
        )
    sm = r.section_melts()
    if any(v is not None for v in sm.values()):
        lines.append(
            "ذوب بخش‌ها: "
            + " | ".join(
                f"{mg.SECTION_LABEL_FA[k]} {v:g}" if v is not None else f"{mg.SECTION_LABEL_FA[k]} —"
                for k, v in sm.items()
            )
        )
    if r.needs_validation:
        lines.append(CASTING_VALIDATION_NOTE_FA)
    if r.product_weight_kg is not None:
        lines.append(f"وزن محصول: {r.product_weight_kg:,.0f} kg ({r.product_weight_kg/1000:,.1f} ton)")
    if r.melt_weight_kg is not None:
        lines.append(f"وزن مذاب: {r.melt_weight_kg:,.0f} kg")
    if r.melt_count is not None:
        lines.append(f"ذوب: {r.melt_count:g}")
    if r.slab_count is not None or r.bloom_billet_count is not None:
        lines.append(
            f"تعداد به اسلب: {r.slab_count if r.slab_count is not None else '—'} | "
            f"به بلوم/بیلت: {r.bloom_billet_count if r.bloom_billet_count is not None else '—'}"
        )
    if r.melts_per_day is not None:
        lines.append(f"ذوب/روز: {r.melts_per_day:g}")
    if r.ocr_confidence is not None:
        lines.append(f"اطمینان OCR: {r.ocr_confidence * 100:.0f}٪")
    if r.missing:
        lines.append("⚠ ناقص: " + "؛ ".join(r.missing))
    if r.notes:
        lines.append("یادداشت: " + "؛ ".join(r.notes[:3]))
    return "\n".join(lines)


def from_known_furnace(
    *,
    year: int,
    month: int,
    melts: float,
    melt_weight_kg: float,
    product_weight_kg: float,
    slab_count: float,
    bloom_billet_count: float,
    melts_per_day: float | None = None,
    ocr_raw_text: str = "",
    ocr_confidence: float | None = None,
) -> ProductionOCRResult:
    """Build a result from known furnace KPI totals — audit only.

    Furnace-tab data is NOT accepted as production any more (report_tab="furnace" is
    refused by ``main_goal_persist.store_production_from_ocr``)."""
    slab, bloom, billet, notes = _allocate_tons_from_furnace(
        product_weight_kg, slab_count, bloom_billet_count
    )
    r = ProductionOCRResult(
        ok=True,
        year=year,
        month=month,
        period_key=f"m:{year:04d}-{month:02d}",
        period_label=format_month_year(year, month, named=True),
        slab_tons=slab,
        bloom_tons=bloom,
        billet_tons=billet,
        melt_count=melts,
        melt_weight_kg=melt_weight_kg,
        product_weight_kg=product_weight_kg,
        slab_count=slab_count,
        bloom_billet_count=bloom_billet_count,
        melts_per_day=melts_per_day,
        report_tab="furnace",
        ocr_raw_text=ocr_raw_text,
        ocr_confidence=ocr_confidence,
        notes=notes + ["مقادیر از KPI تب کوره"],
    )
    return r


def from_known_casting(
    *,
    year: int,
    month: int,
    ccm_tons: dict[int, float],
    ccm_melts: dict[int, float] | None = None,
    ocr_raw_text: str = "",
    ocr_confidence: float | None = None,
) -> ProductionOCRResult:
    """Build a casting-tab result from known per-CCM values (seed / smoke / manual)."""
    r = ProductionOCRResult(
        ok=True,
        year=year,
        month=month,
        period_key=f"m:{year:04d}-{month:02d}",
        period_label=format_month_year(year, month, named=True),
        report_tab="casting",
        ccm_tons={int(k): float(v) for k, v in ccm_tons.items()},
        ccm_melts={int(k): float(v) for k, v in (ccm_melts or {}).items()},
        ocr_raw_text=ocr_raw_text,
        ocr_confidence=ocr_confidence,
        notes=["مقادیر CCM از تب ریخته‌گری"],
    )
    for no, t in r.ccm_tons.items():
        sec = CCM_TO_SECTION.get(no)
        if sec:
            setattr(r, f"{sec}_tons", getattr(r, f"{sec}_tons") + t)
    if r.ccm_melts:
        r.melt_count = float(sum(r.ccm_melts.values()))
    return r
