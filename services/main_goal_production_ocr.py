"""OCR + structured parse for KSC production screenshots.

Supports:
  • Furnace tab (کوره) otsteel.ksc.ir/productionstatistics — totals row:
      melts, melt_weight_kg, product_weight_kg, slab_count, bloom_billet_count, melts/day
      Month from Jalali date filter (1405/04/01–1405/04/31 → Tir).
      Weights are kg → tons = kg/1000. Section tonnage split by melt counts when
      casting CCM tonnage is absent (bloom vs billet not separated).
  • Casting/CCM tables (legacy Excel-like screenshots) when CCM1..5 present.
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
    report_tab: str = "furnace"  # furnace | casting | unknown
    ccm_tons: dict[int, float] = field(default_factory=dict)
    ocr_raw_text: str = ""
    ocr_confidence: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_tons(self) -> float:
        return float(self.slab_tons + self.bloom_tons + self.billet_tons)

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
    # Date filter range: 1405/04/01 … 1405/04/31 → month 4
    m = re.search(r"(14\d{2})\s*[/\-]\s*(0?[1-9]|1[0-2])\s*[/\-]\s*\d{1,2}", n)
    if m:
        return int(m.group(1)), int(m.group(2)), "date-filter"
    ranges = re.findall(r"(14\d{2})\s*[/\-]\s*(0?[1-9]|1[0-2])", n)
    if ranges:
        # prefer the first start date
        y, mo = ranges[0]
        return int(y), int(mo), "ym-slash"
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
        if name in n:
            ym = re.search(r"(14\d{2})", n)
            if ym:
                return int(ym.group(1)), num, f"fa:{name}"
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


def parse_production_ocr_text(text: str, *, confidence: float | None = None) -> ProductionOCRResult:
    """Auto-detect furnace vs CCM casting layout."""
    n = _norm(text).casefold()
    if "ccm" in n and ("product" in n or "ton" in n):
        # reuse simpler CCM path via furnace parser extras
        from services import main_goal_production_ocr as selfmod  # noqa — fall through
    # Prefer furnace if date filter / کوره / furnace KPIs
    if any(k in n for k in ("کوره", "furnace", "productionstatistics", "وزن مذاب", "وزن محصول", "به اسلب")):
        return parse_furnace_ocr_text(text, confidence=confidence)
    # Try furnace anyway (most current uploads), then mark casting if CCM found
    r = parse_furnace_ocr_text(text, confidence=confidence)
    if "ccm1" in n or "ccm 1" in n:
        r.report_tab = "casting"
    return r


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
        lines.append(f"منبع گزارش: {r.report_tab}")
    lines.append(
        f"تناژ (تقریبی): اسلب {r.slab_tons:,.1f} | بلوم‌بیلت {r.bloom_tons:,.1f} | "
        f"بیلت {r.billet_tons:,.1f} | جمع {r.total_tons:,.1f}"
    )
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
    """Build a result from known furnace KPI totals (seed / manual)."""
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
