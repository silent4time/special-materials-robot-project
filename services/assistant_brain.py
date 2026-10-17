"""مغز دستیار هوشمند (متن یا صوت → نیت + پارامتر → پاسخ از دادهٔ واقعی).

اصل طراحی: «مدل زبانی هیچ عددی نمی‌سازد».

1. ``route(text)``: اول قواعد سریع فارسی (regex/کلیدواژه پس از نرمال‌سازی ی/ي، ک/ك،
   ارقام ۰-۹/٠-٩ و نیم‌فاصله). فقط اگر قواعد چیزی نیافتند و Ollama در دسترس بود،
   ``llm_route`` با حالت JSON سخت‌گیر (format=json، temperature 0، چند مثال) نیت را
   حدس می‌زند؛ خروجی مدل فقط نیت/پارامتر است، نه عدد پاسخ.
2. ``answer(db, user, text)``: نیت را با همان توابع سرویس/DB که گزارش‌ها و منوها
   استفاده می‌کنند اجرا می‌کند (بدون منطق تکراری، بدون تغییر منبع اصلی) و همان
   مجوز نقش منوی متناظر (services.permissions) را اعمال می‌کند.
3. بازنویسی اختیاری با مدل (``rephrase=True``): فقط از روی متن قالبی، و فقط اگر همهٔ
   اعداد خروجی دقیقاً همان اعداد متن قالبی باشند؛ وگرنه همان متن قالبی برمی‌گردد.

اگر Ollama خاموش باشد مسیر قواعد همچنان پاسخ می‌دهد.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from services import permissions as perm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- intents
I_STOCK = "stock"
I_CRITICAL = "critical"
I_N_TUNDISH = "n_tundish"
I_COVERAGE = "coverage"
I_CONSUMPTION = "consumption"
I_LAST_UPLOAD = "last_upload"
I_PENDING = "pending_approvals"
I_TUNDISH_STATS = "tundish_stats"
I_OPEN_REPORT = "open_report"
I_HELP = "help"
I_UNKNOWN = "unknown"

INTENTS = (
    I_STOCK, I_CRITICAL, I_N_TUNDISH, I_COVERAGE, I_CONSUMPTION, I_LAST_UPLOAD,
    I_PENDING, I_TUNDISH_STATS, I_OPEN_REPORT, I_HELP,
)

INTENT_LABEL_FA = {
    I_STOCK: "موجودی قلم",
    I_CRITICAL: "اقلام بحرانی / کم",
    I_N_TUNDISH: "نیاز مواد برای N تاندیش",
    I_COVERAGE: "حد تحمل (روز پوشش) قلم",
    I_CONSUMPTION: "مصرف بازه‌ای",
    I_LAST_UPLOAD: "تاریخ آخرین بارگذاری موجودی",
    I_PENDING: "درخواست‌های در انتظار تأیید",
    I_TUNDISH_STATS: "آمار توالی تاندیش / ریخته‌گری",
    I_OPEN_REPORT: "باز کردن گزارش / منو",
    I_HELP: "راهنما",
    I_UNKNOWN: "نامشخص",
}

# permission(s) of the menu each intent mirrors (any-of); None = always allowed
INTENT_FEATURES: dict[str, tuple[str, ...] | None] = {
    # موجودی منبع اصلی: همان دسترسی «📦 منبع اصلی» (دانلود/فهرست) یا گزارش اقلام بحرانی
    I_STOCK: (perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT, perm.REPORT_CRITICAL),
    I_CRITICAL: (perm.REPORT_CRITICAL,),
    I_COVERAGE: (perm.REPORT_CRITICAL,),
    I_N_TUNDISH: (perm.REPORT_N_TUNDISH,),
    I_CONSUMPTION: (perm.REPORT_PERIOD,),
    I_LAST_UPLOAD: (perm.FILE_INPUTS, perm.MAIN_SOURCE_EDIT) + tuple(perm.REPORT_FEATURES),
    I_PENDING: None,  # services.main_source_approvals.is_approver (مالک/مدیر)
    I_TUNDISH_STATS: (perm.MAIN_GOAL, perm.REPORT_CRITICAL),
    I_OPEN_REPORT: None,  # checked per button via keyboards.BUTTON_FEATURE
    I_HELP: None,
}

SECTION_FA = {"slab": "اسلب", "bloom": "بلوم", "billet": "بیلت"}

# ---------------------------------------------------------------- normalize
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_CHARS = str.maketrans({
    "ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "ؤ": "و",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "\u200c": " ", "\u200d": "", "\u200f": "", "\u200e": "", "\u00a0": " ", "\u0640": "",
    "؟": " ", "?": " ", "!": " ", "،": " ", ",": " ", "؛": " ", ";": " ", ":": " ",
    "«": " ", "»": " ", "\"": " ", "'": " ", "(": " ", ")": " ", "—": " ", "–": " ",
})
_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670]")


def normalize(text: Any) -> str:
    """Matching form: Arabic→Persian letters, Latin digits, no ZWNJ/diacritics, one space."""
    s = str(text or "").translate(_DIGITS)
    s = _DIACRITICS.sub("", s).translate(_CHARS).lower()
    s = re.sub(r"(?<=\d)٫(?=\d)", ".", s)
    s = re.sub(r"\s*([*/])\s*", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- data types
@dataclass
class Intent:
    name: str
    slots: dict[str, Any] = field(default_factory=dict)
    source: str = "rules"  # rules | llm | none

    def as_dict(self) -> dict[str, Any]:
        return {"intent": self.name, "slots": dict(self.slots), "source": self.source}


@dataclass
class Answer:
    text: str
    intent: Intent
    buttons: list[str] = field(default_factory=list)  # reply-keyboard labels
    ok: bool = True  # False = denied / no data / not understood
    rephrased: bool = False
    facts: dict[str, Any] = field(default_factory=dict)  # raw numbers (tests/web)


# ---------------------------------------------------------------- slot parsers
_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "امرداد": 5,
    "شهریور": 6, "مهر": 7, "ابان": 8, "اذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}
_WORD_NUM = {
    "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7,
    "هشت": 8, "نه": 9, "ده": 10, "یازده": 11, "دوازده": 12, "پانزده": 15, "پونزده": 15,
    "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "صد": 100,
}
_NUMW = r"(?<![\w.])(\d+(?![\d.])|(?:" + "|".join(sorted(_WORD_NUM, key=len, reverse=True)) + r")(?!\w))"


def _num(tok: str) -> int | None:
    tok = (tok or "").strip()
    if tok.isdigit():
        return int(tok)
    return _WORD_NUM.get(tok)


def parse_section(t: str) -> str | None:
    if re.search(r"اسلب", t):
        return "slab"
    if re.search(r"بلوم", t):
        return "bloom"
    if re.search(r"بیلت", t):
        return "billet"
    return None


def parse_reno(t: str) -> str:
    from analytics.critical_items import RENO_WITH, RENO_WITHOUT

    return RENO_WITHOUT if re.search(r"(بدون|بی) ?نوسازی|فقط وصله|فقط پچ", t) else RENO_WITH


def parse_period(t: str, today: Any = None) -> dict[str, Any] | None:
    """Jalali-aware period → {start, end (date), label, months [(y,m)...]} or None."""
    import jdatetime

    from bot.jalali import PERSIAN_MONTH_NAMES, jalali_today, month_year_to_gregorian_bounds

    jt = today or jalali_today()
    g_today = jt.togregorian()

    def months_span(y1: int, m1: int, y2: int, m2: int) -> list[tuple[int, int]]:
        out, y, m = [], y1, m1
        while (y, m) <= (y2, m2):
            out.append((y, m))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def back(y: int, m: int, k: int) -> tuple[int, int]:
        m -= k
        while m < 1:
            m += 12
            y -= 1
        return y, m

    def month_period(y: int, m: int, label: str | None = None) -> dict[str, Any]:
        s, e = month_year_to_gregorian_bounds((y, m), (y, m))
        return {"start": s, "end": min(e, g_today), "label": label or f"{PERSIAN_MONTH_NAMES[m]} {y}",
                "months": [(y, m)]}

    if re.search(r"\bامروز\b", t):
        return {"start": g_today, "end": g_today, "label": "امروز", "months": []}
    if re.search(r"\bدیروز\b", t):
        d = g_today - timedelta(days=1)
        return {"start": d, "end": d, "label": "دیروز", "months": []}
    m = re.search(_NUMW + r" ?روز", t)
    if m and _num(m.group(1)):
        n = min(_num(m.group(1)) or 1, 366)
        return {"start": g_today - timedelta(days=n - 1), "end": g_today, "label": f"{n} روز اخیر", "months": []}
    m = re.search(_NUMW + r" ?هفته", t)
    if m and _num(m.group(1)):
        n = min(_num(m.group(1)) or 1, 52)
        return {"start": g_today - timedelta(days=7 * n - 1), "end": g_today, "label": f"{n} هفتهٔ اخیر",
                "months": []}
    if re.search(r"هفته (گذشته|اخیر|قبل|پیش)|این هفته", t):
        return {"start": g_today - timedelta(days=6), "end": g_today, "label": "۷ روز اخیر", "months": []}
    m = re.search(_NUMW + r" ?ماه (اخیر|گذشته|قبل|پیش)", t)
    if m and _num(m.group(1)):
        n = min(_num(m.group(1)) or 1, 24)
        # n complete months before the current one
        y2, m2 = back(jt.year, jt.month, 1)
        y1, m1 = back(jt.year, jt.month, n)
        s, e = month_year_to_gregorian_bounds((y1, m1), (y2, m2))
        return {"start": s, "end": e, "label": f"{n} ماه کامل اخیر", "months": months_span(y1, m1, y2, m2)}
    if re.search(r"(این|همین) ماه|ماه جاری", t):
        return month_period(jt.year, jt.month, f"ماه جاری ({PERSIAN_MONTH_NAMES[jt.month]} {jt.year})")
    if re.search(r"ماه (گذشته|قبل|پیش)|ماه پیشین", t):
        y, mm = back(jt.year, jt.month, 1)
        return month_period(y, mm)
    if re.search(r"امسال|از اول سال|سال جاری", t):
        s, _e = month_year_to_gregorian_bounds((jt.year, 1), (jt.year, jt.month))
        return {"start": s, "end": g_today, "label": f"از ابتدای {jt.year}",
                "months": months_span(jt.year, 1, jt.year, jt.month)}
    for name, mm in _MONTHS.items():
        mt = re.search(rf"(?<![\w]){name}(?![\w])(?: ?(?:ماه|سال))?(?: ?(1[34]\d\d))?", t)
        if mt:
            if name == "دی" and not re.search(r"\bدی ?ماه\b|\bدی (1[34]\d\d)", t):
                continue  # «دی» is too ambiguous alone
            y = int(mt.group(1)) if mt.group(1) else (jt.year if mm <= jt.month else jt.year - 1)
            try:
                jdatetime.date(y, mm, 1)
            except ValueError:
                continue
            return month_period(y, mm)
    return None


_CODE_RE = re.compile(r"(?<![\d.])(\d{4})(?![\d.])")
_ID_RE = re.compile(r"(?<![\w])(\d{8,}[a-z]?)(?![\w])")

_STOP = set("""
از چقدر چه قدر چند چندتا تا داریم داره داری هست هستش است اند هستن موجودی موجود موجودیش انبار انباره الان فعلا
فعلی کل مجموع ما رو را برای به با در توی تو که کالای کالا قلم اقلام جنس مواد ماده مقدار میزان باقی مانده مونده
ماند چی چیه چیست چطور چگونه کجا ایا اقا لطفا بگو بگید بفرما بفرمایید بده بدید نشون نشان نمایش بهم بهمون ی یه
یک عدد کیلو کیلوگرم تن دونه تعداد کد شماره شناسه استاک وضعیت چندتاست چقدره چقدر داریم میشه میشود من مون شون
حد تحمل روز روزه پوشش کافی کافیه بس بسه دوام میکشه می کشه کشه جواب میده میدهد کفایت میکند میکنه تموم تمام
مصرف مصرفش شده شد خرج استفاده اخیر گذشته قبل این همین ماه هفته امروز دیروز امسال و یا هم اون ان اونها
وقت دیگه دیگر ای های ها بخش تاندیش تاندیشی کی میده میدن
""".split())


def extract_item_query(t: str) -> str:
    t = _ID_RE.sub(" ", t)
    t = _CODE_RE.sub(" ", t)
    t = re.sub(r"(بدون|بی) ?نوسازی|با نوسازی|فقط (وصله|پچ)", " ", t)
    t = re.sub(_NUMW + r" ?(تا|عدد|دونه)? ?(روز|هفته|ماه|تاندیش|سال)", " ", t)
    for name in _MONTHS:
        t = re.sub(rf"(?<![\w]){name}(?![\w])", " ", t)
    toks = [w for w in re.split(r"\s+", t) if w and w not in _STOP]
    return " ".join(toks).strip()


# ---------------------------------------------------------------- rules
_REPORTS: list[tuple[str, str]] = [  # (pattern, keyboards attr)
    (r"گزارش ?(های|ها)|منوی گزارش", "BTN_ANALYTICS"),
    (r"جامع", "BTN_COMPREHENSIVE"),
    (r"مصرف روزانه|گزارش روزانه", "BTN_DAILY"),
    (r"بازه ?ای|مصرف بازه", "BTN_PERIOD"),
    (r"پوشش کوتاه|کوتاه ?مدت", "BTN_REMAINING"),
    (r"بحرانی", "BTN_CRITICAL_ITEMS"),
    (r"\bn ?تاندیش|نیاز مواد", "BTN_N_TUNDISH"),
    (r"مازاد", "BTN_SURPLUS"),
    (r"ورودی|اقلام ورودی", "BTN_INBOUND"),
    (r"خلاصه مصرف|ماهیانه|ماهانه", "BTN_MONTHLY_SUMMARY"),
    (r"هدف اصلی", "BTN_MAIN_GOAL"),
    (r"موجودی (روزانه )?سایت", "BTN_SITE_STOCK"),
    (r"گزارش تاندیش|فرم تاندیش", "BTN_TR_MENU"),
    (r"درخواست مواد", "BTN_MATERIAL_REQUEST"),
    (r"برگشت (به )?انبار", "BTN_WAREHOUSE_RETURN"),
    (r"منبع اصلی", "BTN_MAIN_SOURCE_FILE"),
    (r"ورود فایل|اپلود فایل|بارگذاری فایل", "BTN_UPLOAD_MENU"),
]
_OPEN_VERB = (
    r"(بفرست|بفرستید|ارسال|باز کن|بازکن|باز شه|نشون بده|نشان بده|نمایش|بیار|بده|بدید|میخوام|می خوام|"
    r"می خواهم|میخواهم|ببینم|برو به|منوی|فایل|pdf|پی دی اف|اکسل|excel)"
)


def _report_button(t: str) -> str | None:
    for pat, attr in _REPORTS:
        if re.search(pat, t):
            return attr
    return None


def rule_route(text: str, *, known_codes: set[str] | None = None) -> Intent | None:
    """Fast deterministic routing. None = rules found nothing (→ LLM fallback)."""
    t = normalize(text)
    if not t:
        return Intent(I_HELP)
    slots: dict[str, Any] = {}
    sec = parse_section(t)
    if sec:
        slots["section"] = sec
    code_m = [c for c in _CODE_RE.findall(t) if known_codes is None or c in known_codes]
    if code_m:
        slots["code"] = code_m[0]
    id_m = _ID_RE.search(t)
    if id_m:
        slots["item_id"] = id_m.group(1).upper()

    def with_item(name: str, **extra: Any) -> Intent:
        s = dict(slots, **extra)
        q = extract_item_query(t)
        if q and not s.get("code") and not s.get("item_id"):
            s["query"] = q
        return Intent(name, s)

    # راهنما
    if re.fullmatch(r"(سلام|درود|سلام علیکم|hi|hello|help|/help|راهنما|کمک)( [\w ]{0,12})?", t) and not code_m \
            and not re.search(r"موجودی|مصرف|تاندیش|بحرانی", t):
        return Intent(I_HELP)
    if re.search(r"راهنما|چه کار\w* (از دستت )?(می ?)?(کنی|تونی|توانی|بلدی|میکنی)|چی (کار )?(می ?)?(تونی|بلدی|بلده)|"
                 r"چی کار (می ?)?(کنی|تونی)|(می ?)?(تونی|توانی) (چه|چی) |"
                 r"چه (سوال|سوالی|چیزی) (می ?)?(تونم|شه) (بپرسم|پرسید)|چی بپرسم|قابلیت|دستیار چیه|"
                 r"چه کمکی", t):
        return Intent(I_HELP)

    # درخواست‌های در انتظار تأیید
    if (re.search(r"تایید|تصویب", t) and re.search(r"درخواست|انتظار|منتظر|مونده|مانده|نشده|معطل", t)) \
            or re.search(r"درخواست ?(های|ها)? (در )?(انتظار|معلق|باز)|کارتابل", t):
        return Intent(I_PENDING)

    # آخرین بارگذاری موجودی
    if re.search(r"(اخرین|اخرین بار|کی|چه (روزی|تاریخی|موقع)|تاریخ) .*(اپلود|بارگذاری|بروز ?رسانی|به ?روز|اپدیت|"
                 r"وارد (شد|کرد)|ثبت شد|بالا (رفت|اومد)|گذاشت|فرستاد|زدن|زده شد)", t) \
            or re.search(r"(اپلود|بارگذاری|بروز ?رسانی|اپدیت) .*(اخرین|کی|چه (روزی|تاریخی))", t):
        return Intent(I_LAST_UPLOAD)

    # نیاز مواد برای N تاندیش (عدد + تاندیش)
    if not re.search(r"توالی|سکوئنس|سکانس|ریخته|ذوب", t):
        m = re.search(_NUMW + r" ?(تا|عدد|دونه)? ?تاندیش", t) or re.fullmatch(
            r"(?:نیاز )?(?:اسلب|بلوم|بیلت) " + _NUMW, t)
        if m and not re.search(r"چند ?(تا)? ?تاندیش", t):
            n = _num(m.group(1))
            if n and n > 0:
                return Intent(I_N_TUNDISH, dict(slots, n=n, reno=parse_reno(t)))

    # باز کردن گزارش / منو (فعل درخواست + نام گزارش، یا «گزارش X»)
    btn = _report_button(t)
    if btn and (re.search(_OPEN_VERB, t) or re.match(r"(گزارش|منو|منوی) ", t)) \
            and not re.search(r"چی کم|چه چیزی کم|کم داریم", t):
        return Intent(I_OPEN_REPORT, {"report": btn})

    # آمار توالی تاندیش / ریخته‌گری
    if re.search(r"توالی|سکوئنس|سکانس|ریخته ?گری|ذوب", t) or (
            re.search(r"تاندیش", t) and re.search(r"چند ?(تا)?|تعداد|امار|میانگین|کارکرد|مصرف شد|استفاده شد|"
                                                 r"زدیم|بستیم|عوض|داشتیم", t)):
        p = parse_period(t)
        if p:
            slots["period"] = p
        return Intent(I_TUNDISH_STATS, slots)

    # حد تحمل / روز پوشش یک قلم
    if re.search(r"حد تحمل|روز پوشش|پوشش", t) or (
            re.search(r"چند روز|تا کی|چه مدت|چقدر وقت", t)
            and re.search(r"کافی|بس|کفایت|دوام|جواب|پوشش|میکشه|می کشه|کار (می ?)?ده|تموم|تمام|داریم", t)):
        return with_item(I_COVERAGE, reno=parse_reno(t))

    # اقلام بحرانی / کم
    if re.search(r"بحرانی|کم داریم|کمبود|کسری|کسر داریم|چی کمه|چی کم|چیا کم|چه (چیزی|چیزهایی|اقلامی) کم|"
                 r"رو به (اتمام|پایان)|تموم (می ?)?شه|نقطه سفارش|سفارش (بد|بزن)|کم (اومده|است|هست|اند)|"
                 r"باید (بخریم|سفارش)|خرید", t):
        return Intent(I_CRITICAL, dict(slots, reno=parse_reno(t)))

    # مصرف بازه‌ای
    if re.search(r"مصرف|خرج (شد|کردیم)|استفاده (شد|کردیم)|مصرفی", t):
        it = with_item(I_CONSUMPTION)
        if it.slots.get("query"):
            q = re.sub(r"\b(اسلب|بلوم|بیلت)(ی)?\b", " ", it.slots["query"]).split()
            it.slots["query"] = " ".join(q)
            if not it.slots["query"]:
                it.slots.pop("query")
        p = parse_period(t)
        if p:
            it.slots["period"] = p
        return it

    # موجودی قلم
    if re.search(r"موجودی|چقدر|چند ?(تا)? داریم|داریم|مونده|مانده|باقی|انبار|استاک", t) or code_m or id_m:
        return with_item(I_STOCK)

    # «گزارش X» بدون فعل
    if btn:
        return Intent(I_OPEN_REPORT, {"report": btn})
    return None


# ---------------------------------------------------------------- LLM fallback
LLM_SCHEMA_KEYS = ("intent", "code", "query", "n", "section", "period", "report")
_LLM_SYSTEM = (
    "You label Persian questions sent to a refractory-materials (تاندیش) warehouse bot. "
    "Reply ONLY with one JSON object with keys intent, code, query, n, section, period, report.\n"
    "intent must be one of:\n"
    "stock = موجودی یک قلم مشخص (چقدر داریم، چندتا مونده، موجودی کد)\n"
    "critical = چه چیزهایی کم است / کسری / بحرانی / تموم میشه / ته کشیده / باید سفارش بدیم (بدون نام قلم)\n"
    "n_tundish = مواد لازم برای N تاندیش آینده (عدد + تاندیش)\n"
    "coverage = یک قلم چند روز کافی است / تا کی جواب میده / حد تحمل\n"
    "consumption = چقدر مصرف شد در یک بازه\n"
    "last_upload = آخرین بارگذاری یا به‌روزرسانی فایل/موجودی/دیتا کی بود\n"
    "pending_approvals = درخواست‌های منتظر تأیید\n"
    "tundish_stats = چند تاندیش یا ذوب یا توالی در گذشته داشتیم\n"
    "open_report = باز کردن یا فرستادن یک گزارش، فایل، PDF یا منو\n"
    "help = سلام، راهنما، کمک، چه کاری می‌توانی، چه بپرسم\n"
    "unknown = نامرتبط\n"
    "code = 4-digit item code written in the question or \"\"; query = item name words or \"\"; "
    "n = number of tundishes or 0; section = slab|bloom|billet|\"\"; period = Persian period words or \"\"; "
    "report = report name words or \"\". Copy values from the question; never invent numbers."
)


def _shot(intent: str, **kw: Any) -> dict[str, Any]:
    base = {"intent": intent, "code": "", "query": "", "n": 0, "section": "", "period": "", "report": ""}
    base.update(kw)
    return base


_LLM_SHOTS: list[tuple[str, dict[str, Any]]] = [
    ("از آجر ۱۶۰۳ چقدر داریم", _shot("stock", code="1603", query="آجر")),
    ("استاک استوپر رو بگو", _shot("stock", query="استوپر")),
    ("چیا داره ته میکشه", _shot("critical")),
    ("لیست کمبودها", _shot("critical")),
    ("واسه بیست تاندیش بیلت جنس چی لازمه", _shot("n_tundish", n=20, section="billet")),
    ("شرود تا چند وقت دیگه جواب میده", _shot("coverage", query="شرود")),
    ("ماه پیش چقدر نازل رفت", _shot("consumption", query="نازل", period="ماه گذشته")),
    ("فایل انبار رو کی گذاشتن", _shot("last_upload")),
    ("کسی منتظر جواب من هست", _shot("pending_approvals")),
    ("شهریور اسلب چندتا ریختیم", _shot("tundish_stats", section="slab", period="شهریور")),
    ("pdf مازاد رو میخوام", _shot("open_report", report="مازاد")),
    ("منوی گزارش جامع", _shot("open_report", report="جامع")),
    ("سلام وقت بخیر", _shot("help")),
    ("چطوری ازت استفاده کنم", _shot("help")),
    ("هوا امروز چطوره", _shot("unknown")),
]


def _ollama_url() -> str:
    import os

    from services import voice_assistant as va

    return (os.environ.get("OLLAMA_URL") or os.environ.get("OLLAMA_BASE_URL") or va.DEFAULT_OLLAMA_URL).rstrip("/")


def ollama_chat(messages: list[dict[str, str]], *, model: str, json_mode: bool, timeout: float = 30.0,
                num_predict: int = 160) -> str:
    """POST /api/chat (stream off, temperature 0). '' on any error (Ollama down = rules only)."""
    import httpx

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0, "num_predict": num_predict, "seed": 7},
        "keep_alive": "30m",
    }
    if json_mode:
        payload["format"] = "json"
    try:
        r = httpx.post(f"{_ollama_url()}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return str(((r.json() or {}).get("message") or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("assistant LLM unavailable: %s", type(exc).__name__)
        return ""


def llm_route(text: str, *, model: str, chat: Callable[..., str] | None = None,
              known_codes: set[str] | None = None) -> Intent | None:
    """Ollama JSON-mode classification → validated Intent (None on failure/invalid)."""
    msgs = [{"role": "system", "content": _LLM_SYSTEM}]
    for q, a in _LLM_SHOTS:
        msgs.append({"role": "user", "content": q})
        msgs.append({"role": "assistant", "content": json.dumps(a, ensure_ascii=False)})
    msgs.append({"role": "user", "content": str(text)[:400]})
    raw = (chat or ollama_chat)(msgs, model=model, json_mode=True)
    if not raw:
        return None
    try:
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except (ValueError, TypeError):
        logger.info("assistant LLM returned non-JSON")
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("intent") or "").strip().lower()
    if name not in INTENTS:
        return None
    t = normalize(text)
    slots: dict[str, Any] = {}
    code = normalize(data.get("code") or "")
    # slots must be grounded in the question itself (the model only labels)
    if re.fullmatch(r"\d{4}", code) and code in t and (known_codes is None or code in known_codes):
        slots["code"] = code
    q = normalize(data.get("query") or "")
    if q and not slots.get("code"):
        q_tok = [w for w in q.split() if w in t]
        if q_tok:
            slots["query"] = " ".join(q_tok)
    sec = str(data.get("section") or "").strip().lower()
    if sec in SECTION_FA:
        slots["section"] = sec
    elif parse_section(t):
        slots["section"] = parse_section(t)
    if name == I_N_TUNDISH:
        try:
            n = int(data.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        nums = [int(x) for x in re.findall(r"\d+", t)] + [v for k, v in _WORD_NUM.items() if re.search(rf"\b{k}\b", t)]
        if n <= 0 or n not in nums:
            return None
        slots["n"] = n
        slots["reno"] = parse_reno(t)
    if name in (I_CONSUMPTION, I_TUNDISH_STATS):
        p = parse_period(t) or parse_period(normalize(data.get("period") or ""))
        if p:
            slots["period"] = p
    if name in (I_CRITICAL, I_COVERAGE):
        slots["reno"] = parse_reno(t)
    if name == I_OPEN_REPORT:
        btn = _report_button(normalize(data.get("report") or "")) or _report_button(t)
        if not btn:
            return None
        slots["report"] = btn
    return Intent(name, slots, source="llm")


def route(text: str, *, use_llm: bool = True, model: str | None = None,
          chat: Callable[..., str] | None = None, known_codes: set[str] | None = None,
          inventory: Any = None) -> Intent:
    it = rule_route(text, known_codes=known_codes)
    if it is not None:
        return it
    # a bare item name («نازل پایینی چنجر») = stock question
    q = extract_item_query(normalize(text))
    if inventory is not None and q and len(q.split()) <= 5:
        ms = match_items(inventory, q, limit=2)
        if ms and ms[0].score >= 0.8:
            return Intent(I_STOCK, {"query": q})
    if use_llm and model:
        it = llm_route(text, model=model, chat=chat, known_codes=known_codes)
        if it is not None:
            return it
    return Intent(I_UNKNOWN, source="none")


# ---------------------------------------------------------------- numbers check
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> list[str]:
    s = str(text or "").translate(_DIGITS).replace("٬", ",").replace("٫", ".")
    out = []
    for m in _NUM_RE.findall(s):
        v = m.replace(",", "")
        if "." in v:
            v = v.rstrip("0").rstrip(".") if re.fullmatch(r"\d+\.\d+", v) else v
        out.append(v.lstrip("0") or "0")
    return out


def numbers_match(template: str, candidate: str) -> bool:
    """Every number of the template appears in the candidate and no new number appears."""
    a, b = numbers_in(template), numbers_in(candidate)
    return set(a) == set(b) and len(b) >= len(set(a))


def rephrase(text: str, *, model: str, chat: Callable[..., str] | None = None) -> str | None:
    """Optional fluent rewrite. Returns None unless all numbers survive verbatim."""
    msgs = [
        {"role": "system", "content": (
            "متن زیر را فقط به زبان فارسی، روان و کوتاه بازنویسی کن. فقط از همین اطلاعات استفاده کن. "
            "همهٔ اعداد، کدها و تاریخ‌ها را دقیقاً همان‌طور که هست بنویس. هیچ عدد یا اطلاعات جدیدی اضافه نکن.")},
        {"role": "user", "content": text[:1500]},
    ]
    out = (chat or ollama_chat)(msgs, model=model, json_mode=False, num_predict=400, timeout=60.0)
    if out and numbers_match(text, out) and _looks_persian(out) and 0.4 <= len(out) / max(1, len(text)) <= 1.8:
        return out
    return None


def _looks_persian(s: str) -> bool:
    """Small models sometimes switch language (e.g. Chinese): reject non-Persian rewrites."""
    letters = [c for c in s if c.isalpha()]
    if not letters or re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", s):
        return False
    fa = sum(1 for c in letters if "\u0600" <= c <= "\u06ff" or "\ufb50" <= c <= "\ufeff")
    return fa / len(letters) >= 0.6


# ---------------------------------------------------------------- data helpers
class _Ctx:
    """Per-question lazy data (one inventory load per answer)."""

    def __init__(self, db: Any, user: dict):
        self.db = db
        self.user = user
        self._inv = None
        self._loaded = False

    @property
    def inventory(self):
        if not self._loaded:
            from analytics.frames import load_primary_inventory

            self._inv = load_primary_inventory(self.db, self.user)
            self._loaded = True
        return self._inv


def _code_of(v: Any) -> str:
    s = str(v if v is not None else "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return "" if s.lower() in {"", "nan", "none"} else s


def known_codes(inv) -> set[str]:
    if inv is None or getattr(inv, "empty", True) or "category_code" not in inv.columns:
        return set()
    return {c for c in (_code_of(v) for v in inv["category_code"]) if c}


def _fmt(q: Any, unit: Any = None) -> str:
    from services.units import fmt_qty

    return fmt_qty(q, unit)


def _unit(u: Any) -> str:
    from services.units import unit_fa

    return unit_fa(u)


@dataclass
class ItemMatch:
    code: str
    label: str
    score: float


def match_items(inv, query: str, limit: int = 5) -> list[ItemMatch]:
    """Fuzzy match on keyword + product name, one entry per 4-digit code, best first."""
    if inv is None or inv.empty or not query:
        return []
    q = normalize(query)
    q_tok = [w for w in q.split() if len(w) >= 2]
    if not q_tok:
        return []
    best: dict[str, ItemMatch] = {}
    for _, r in inv.iterrows():
        code = _code_of(r.get("category_code"))
        if not code:
            continue
        kw = normalize(r.get("keyword"))
        name = normalize(r.get("product_name"))
        kw_tok, name_tok = kw.split(), name.split()

        def hit(w: str, toks: list[str]) -> float:
            if w in toks:
                return 1.0
            if len(w) >= 3 and any(w in x or (len(x) >= 3 and x in w) for x in toks):
                return 0.7
            close = difflib.get_close_matches(w, toks, n=1, cutoff=0.8)
            return 0.6 if close else 0.0

        kw_score = sum(hit(w, kw_tok) for w in q_tok) / len(q_tok)
        name_score = sum(hit(w, name_tok) for w in q_tok) / len(q_tok)
        ratio = difflib.SequenceMatcher(None, q, kw).ratio()
        score = max(kw_score, 0.8 * name_score) * 0.8 + 0.2 * ratio
        if q == kw:
            score = 1.0 + 0.0
        if score > (best[code].score if code in best else -1):
            best[code] = ItemMatch(code, str(r.get("keyword") or r.get("product_name") or "").strip(), score)
    out = sorted(best.values(), key=lambda m: (-m.score, m.code))
    return [m for m in out if m.score >= 0.45][:limit]


def resolve_item(ctx: _Ctx, slots: dict[str, Any]) -> tuple[str | None, list[ItemMatch], str | None]:
    """→ (code, candidates, error). candidates>1 and code None = ambiguous."""
    inv = ctx.inventory
    if inv is None or inv.empty:
        return None, [], "منبع اصلی یافت نشد."
    codes = known_codes(inv)
    if slots.get("code") in codes:
        return slots["code"], [], None
    if slots.get("item_id"):
        ids = inv["id"].astype(str).str.strip().str.upper()
        hit = inv.loc[ids == str(slots["item_id"]).upper()]
        if not hit.empty:
            return _code_of(hit.iloc[0]["category_code"]), [], None
        return None, [], f"شناسهٔ {slots['item_id']} در منبع اصلی نیست."
    q = slots.get("query") or ""
    if not q:
        return None, [], None
    ms = match_items(inv, q)
    if not ms:
        return None, [], f"قلمی شبیه «{q}» در منبع اصلی پیدا نشد."
    from config import SURPLUS_CATEGORY_CODE

    # «اقلام مازاد» (1800) shares keywords with real codes: prefer the real code
    if len(ms) > 1 and ms[0].code == SURPLUS_CATEGORY_CODE or (
            len(ms) > 1 and ms[1].code == SURPLUS_CATEGORY_CODE and ms[1].score >= ms[0].score - 0.08):
        real = [m for m in ms if m.code != SURPLUS_CATEGORY_CODE]
        if real and real[0].score >= ms[0].score - 0.08:
            ms = real
    top = ms[0]
    rest = [m for m in ms[1:] if m.score >= top.score - 0.08]
    if top.score >= 1.0 and not [m for m in rest if m.score >= 1.0]:
        return top.code, [], None
    if not rest and top.score >= 0.55:
        return top.code, [], None
    return None, ms, None


# ---------------------------------------------------------------- executors
def _deny(intent: Intent) -> Answer:
    feats = INTENT_FEATURES.get(intent.name) or ()
    label = "، ".join(perm.FEATURE_LABEL_FA.get(f, f) for f in feats[:3])
    msg = "⛔ برای این پرسش دسترسی ندارید"
    if label:
        msg += f" (همان دسترسی منوی «{label}»)"
    return Answer(msg + ".", intent, ok=False)


def _allowed(user: dict, intent: Intent) -> bool:
    if intent.name == I_PENDING:
        from services.main_source_approvals import is_approver

        return is_approver(user)
    feats = INTENT_FEATURES.get(intent.name)
    return True if not feats else perm.can_any(user, feats)


def _ambiguous(intent: Intent, ms: list[ItemMatch], verb: str) -> Answer:
    lines = ["چند قلم با این نام پیدا شد؛ کدام منظورتان است؟"]
    btns = []
    for m in ms:
        lines.append(f"• {m.code} — {m.label}")
        btns.append(f"{verb} {m.code} {m.label}"[:60])
    return Answer("\n".join(lines), intent, buttons=btns, ok=True, facts={"candidates": [m.code for m in ms]})


def exec_stock(ctx: _Ctx, it: Intent) -> Answer:
    code, ms, err = resolve_item(ctx, it.slots)
    if err:
        return Answer(err, it, ok=False)
    if ms:
        return _ambiguous(it, ms, "موجودی")
    if not code:
        return Answer("کد چهاررقمی یا نام قلم را بگویید؛ مثلاً «موجودی ۱۶۷۲» یا «از استوپر چقدر داریم».", it,
                      ok=False)
    inv = ctx.inventory
    rows = inv.loc[inv["category_code"].map(_code_of) == code]
    import pandas as pd

    qty = pd.to_numeric(rows["quantity"], errors="coerce").fillna(0)
    unit = next((u for u in rows["unit"].astype(str) if u and u.lower() != "nan"), "")
    total = float(qty.sum())
    kw = next((k for k in rows["keyword"].astype(str) if k and k.lower() != "nan"), code)
    lines = [f"📦 موجودی کد {code} — {kw}", f"جمع موجودی انبار: {_fmt(total, unit)} {_unit(unit)}"]
    by_seg: dict[str, float] = {}
    if "contractor_or_company" in rows.columns:
        for seg, q in zip(rows["contractor_or_company"].astype(str), qty):
            seg = seg.strip() if seg and seg.lower() != "nan" else "نامشخص"
            by_seg[seg] = by_seg.get(seg, 0.0) + float(q)
    if len(by_seg) > 1:
        lines.append(" | ".join(f"{k}: {_fmt(v, unit)}" for k, v in by_seg.items()))
    if len(rows) > 1:
        lines.append(f"{len(rows)} ردیف:")
        for (_, r), q in list(zip(rows.iterrows(), qty))[:8]:
            name = str(r.get("product_name") or "").strip()
            lines.append(f"• {r.get('id')} — {name[:45]}: {_fmt(q, unit)}")
    from config import SURPLUS_CATEGORY_CODE

    surplus = 0.0
    if code != SURPLUS_CATEGORY_CODE and kw:
        sur = inv.loc[(inv["category_code"].map(_code_of) == SURPLUS_CATEGORY_CODE)
                      & (inv["keyword"].map(normalize) == normalize(kw))]
        if not sur.empty:
            surplus = float(pd.to_numeric(sur["quantity"], errors="coerce").fillna(0).sum())
            lines.append(f"(جدا از این، در اقلام مازاد کد {SURPLUS_CATEGORY_CODE} هم {_fmt(surplus, unit)} "
                         f"{_unit(unit)} با همین نام هست.)")
    lines.append("مبنا: منبع اصلی + اصلاحات دفتر انبار (همان مبنای گزارش‌ها).")
    return Answer("\n".join(lines), it, facts={"code": code, "total": total, "unit": unit,
                                                "by_segment": by_seg, "surplus": surplus},
                  buttons=[f"پوشش {code} {kw}"[:60]])


def _critical_frames(ctx: _Ctx, reno: str, include_covered: bool = False):
    from services.critical_items_report import SEGMENTS, build_critical_items_segments, critical_basis

    counts = critical_basis(ctx.db)
    if counts is None:
        return None, None, ("برای ۳ ماه کامل گذشته نه لاگ توالی تاندیش هست و نه «تعداد تاندیش ماهانه»؛ "
                            "مبنای میانگین مصرف وجود ندارد.")
    inv = ctx.inventory
    if inv is None or inv.empty:
        return counts, None, "منبع اصلی یافت نشد."
    if include_covered:
        from analytics.critical_items import build_critical_items_rows

        frames = {seg: build_critical_items_rows(inv, counts, segment=seg, reno_mode=reno, include_covered=True)
                  for seg in SEGMENTS}
    else:
        frames = build_critical_items_segments(inv, counts, reno)
    return counts, frames, None


def exec_critical(ctx: _Ctx, it: Intent) -> Answer:
    from analytics.critical_items import COL_DAYS, COL_NEED, RENO_LABEL_FA, horizon_header_note
    from services.critical_items_report import SEGMENT_LABEL_FA, SEGMENTS, critical_item_count

    reno = it.slots.get("reno") or "with"
    counts, frames, err = _critical_frames(ctx, reno)
    if err:
        return Answer(err, it, ok=False)
    lines = [f"⚠️ اقلام بحرانی ({RENO_LABEL_FA.get(reno, reno)}) — نیاز مثبت در افق ۳ ماه داخلی / ۶ ماه وارداتی",
             horizon_header_note(counts), ""]
    facts: dict[str, Any] = {"counts": {}, "top": {}}
    for seg in SEGMENTS:
        df = frames.get(seg)
        n = critical_item_count(df)
        facts["counts"][seg] = n
        lines.append(f"• {SEGMENT_LABEL_FA.get(seg, seg)}: {n} قلم")
        if df is None or df.empty:
            continue
        shown = []
        for _, r in df.iterrows():
            if str(r.get("ردیف", "")).strip() == "":
                continue
            if len(shown) >= 5:
                break
            shown.append(str(r.get("کد چهاررقمی")))
            desc = str(r.get("کد و شرح کالا") or "")[:38]
            lines.append(
                f"  {r.get('کد چهاررقمی')} {desc} — موجودی {r.get('موجودی')} {_unit(r.get('واحد'))} | "
                f"نیاز {r.get(COL_NEED)} | حد تحمل {r.get(COL_DAYS)} روز"
            )
        facts["top"][seg] = shown
        if n > len(shown):
            lines.append(f"  … و {n - len(shown)} قلم دیگر")
    lines.append("")
    lines.append("فایل کامل (PDF/اکسل): دکمهٔ «اقلام بحرانی».")
    from bot import keyboards as kb

    return Answer("\n".join(lines), it, buttons=[kb.BTN_CRITICAL_ITEMS], facts=facts)


def exec_coverage(ctx: _Ctx, it: Intent) -> Answer:
    from analytics.critical_items import COL_DAYS, COL_HORIZON, COL_MONTHLY, COL_NEED, RENO_LABEL_FA
    from services.critical_items_report import SEGMENT_LABEL_FA, SEGMENTS

    code, ms, err = resolve_item(ctx, it.slots)
    if err:
        return Answer(err, it, ok=False)
    if ms:
        return _ambiguous(it, ms, "پوشش")
    if not code:
        # no item → the most urgent items are the answer
        return exec_critical(ctx, Intent(I_CRITICAL, {"reno": it.slots.get("reno") or "with"}, it.source))
    reno = it.slots.get("reno") or "with"
    counts, frames, err = _critical_frames(ctx, reno, include_covered=True)
    if err:
        return Answer(err, it, ok=False)
    lines: list[str] = []
    facts: dict[str, Any] = {"code": code, "days": {}}
    for seg in SEGMENTS:
        df = frames.get(seg)
        if df is None or df.empty:
            continue
        codes = df["کد چهاررقمی"].astype(str)
        groups = df["group_codes"].astype(str) if "group_codes" in df.columns else codes
        mask = (codes == code) & (df["row_kind"].astype(str) != "member") if "row_kind" in df.columns else codes == code
        hit = df.loc[mask]
        shared = False
        if hit.empty:
            hit = df.loc[(groups.str.split("/").map(lambda xs: code in xs)) & (codes.str.contains("/"))]
            shared = not hit.empty
        if hit.empty:
            continue
        r = hit.iloc[0]
        facts["days"][seg] = r.get(COL_DAYS)
        head = f"• {SEGMENT_LABEL_FA.get(seg, seg)}"
        if shared:
            head += f" (نیاز مشترک گروه {r.get('کد چهاررقمی')})"
        lines.append(
            f"{head}: حد تحمل {r.get(COL_DAYS)} روز | موجودی {r.get('موجودی')} {_unit(r.get('واحد'))} | "
            f"میانگین مصرف ماهانه {r.get(COL_MONTHLY)} | افق {r.get(COL_HORIZON)} ماه | نیاز {r.get(COL_NEED)}"
        )
    kw = ""
    inv = ctx.inventory
    rows = inv.loc[inv["category_code"].map(_code_of) == code]
    if not rows.empty:
        kw = str(rows.iloc[0].get("keyword") or "")
    if not lines:
        return Answer(
            f"برای کد {code} {kw} نرخ مصرف به ازای تاندیش در منبع اصلی ثبت نشده (یا اولویت ۰ است)؛ "
            "حد تحمل قابل محاسبه نیست.", it, ok=False, facts=facts)
    out = [f"⏳ حد تحمل کد {code} — {kw} ({RENO_LABEL_FA.get(reno, reno)})"] + lines
    out.append("مبنا: میانگین تعداد تاندیش ۳ ماه کامل گذشته (همان محاسبهٔ گزارش اقلام بحرانی).")
    return Answer("\n".join(out), it, facts=facts, buttons=[f"موجودی {code} {kw}"[:60]])


def exec_n_tundish(ctx: _Ctx, it: Intent) -> Answer:
    from datetime import datetime

    from bot.jalali import format_date
    from services import n_tundish_report as nt

    n = int(it.slots.get("n") or 0)
    if n < 1 or n > nt.MAX_N:
        return Answer(f"تعداد تاندیش باید بین ۱ و {nt.MAX_N} باشد.", it, ok=False)
    reno = it.slots.get("reno") or "with"
    inv = ctx.inventory
    today = format_date(datetime.now().date())
    sec = it.slots.get("section")
    from bot import keyboards as kb

    if sec:
        res = nt.build_rows(inv, sec, n, reno, report_date=today)
        if res.error:
            return Answer(res.error, it, ok=False)
        return Answer(res.bot_text(top=8) + "\n\nفایل PDF/اکسل: دکمهٔ «نیاز مواد برای N تاندیش».", it,
                      buttons=[kb.BTN_N_TUNDISH],
                      facts={"section": sec, "n": n, "items": res.item_count(), "short": res.shortage_count()})
    lines = [f"🧮 نیاز مواد برای {n} تاندیش — بخش را مشخص نکردید؛ خلاصهٔ هر سه بخش:"]
    facts: dict[str, Any] = {"n": n, "sections": {}}
    btns = []
    for key, fa in nt.SECTIONS.items():
        res = nt.build_rows(inv, key, n, reno, report_date=today)
        if res.error:
            lines.append(f"• {fa}: {res.error}")
            continue
        facts["sections"][key] = {"items": res.item_count(), "short": res.shortage_count()}
        lines.append(f"• {fa}: {res.item_count()} قلم با نیاز؛ {res.shortage_count()} قلم کسری")
        btns.append(f"نیاز {n} تاندیش {fa}")
    lines.append("برای جزئیات یک بخش، دکمهٔ همان بخش را بزنید.")
    return Answer("\n".join(lines), it, buttons=btns, facts=facts)


def exec_consumption(ctx: _Ctx, it: Intent) -> Answer:
    from bot.jalali import format_date, jalali_today
    from services import period_consumption as pc

    p = it.slots.get("period")
    if not p:
        g = jalali_today().togregorian()
        p = {"start": g - timedelta(days=29), "end": g, "label": "۳۰ روز اخیر"}
    sec = it.slots.get("section")
    label = f"{p['label']} (از {format_date(p['start'])} تا {format_date(p['end'])})"
    res = pc.build(ctx.db, p["start"], p["end"], range_label=label, section=sec)
    if res.error:
        return Answer(res.error, it, ok=False)
    q = it.slots.get("query") or ""
    code = it.slots.get("code") or ""
    if code and not q:
        inv = ctx.inventory
        if inv is not None and not inv.empty:
            rows = inv.loc[inv["category_code"].map(_code_of) == code]
            if not rows.empty:
                q = str(rows.iloc[0].get("keyword") or "")
    site, mg = list(res.site_rows), list(res.mg_rows)
    if q:
        nq = [w for w in normalize(q).split() if len(w) >= 2]

        def ok(s: Any) -> bool:
            ns = normalize(s)
            return all(w in ns for w in nq)

        site = [r for r in site if ok(r.get("شرح کالا"))]
        mg = [r for r in mg if ok(r.get("ماده"))]
    if not site and not mg and not res.mg_counts:
        av = pc.data_availability(ctx.db, sec)
        msg = f"برای «{label}»" + (f" و قلم «{q}»" if q else "") + " دادهٔ مصرفی پیدا نشد.\n"
        return Answer(msg + pc.availability_text_fa(av), it, ok=False)
    lines = [f"📅 مصرف {label} — {res.section_label}" + (f" — «{q}»" if q else ""), res.source_line()]
    facts: dict[str, Any] = {"site": {}, "mg": {}}
    if site:
        lines.append(f"الف) از موجودی روزانهٔ سایت: {len(site)} قلم")
        for r in sorted(site, key=lambda r: -float(r["مصرف (کاهش موجودی)"] or 0))[:8]:
            facts["site"][str(r["شرح کالا"])] = r["مصرف (کاهش موجودی)"]
            lines.append(f"• {r['بخش']} | {str(r['شرح کالا'])[:40]}: {r['مصرف (کاهش موجودی)']}")
    if mg:
        lines.append(f"ب) از جداول تاندیش ماهانه: {len(mg)} ردیف")
        for r in mg[:8]:
            facts["mg"][f"{r['بخش']}|{r['ماده']}"] = r["مصرف"]
            lines.append(f"• {r['بخش']} | {r['ماده']}: {r['مصرف']} {_unit(r['واحد'])}")
    elif res.mg_counts and not q:
        for r in res.mg_counts[:6]:
            lines.append(f"• {r['بخش']} | {r['ماه']}: {r['تعداد تاندیش']} تاندیش")
    from bot import keyboards as kb

    return Answer("\n".join(lines), it, buttons=[kb.BTN_PERIOD], facts=facts)


def exec_last_upload(ctx: _Ctx, it: Intent) -> Answer:
    from bot.jalali import format_date, format_datetime

    db = ctx.db
    lines = ["🗓 آخرین بارگذاری‌ها"]
    facts: dict[str, Any] = {}
    up = db.latest_stock_upload()
    if up:
        facts["stock_update"] = format_datetime(up.get("created_at"))
        lines.append(f"• به‌روزرسانی موجودی انبار: {facts['stock_update']} ({up.get('row_count') or 0} ردیف)")
    else:
        lines.append("• به‌روزرسانی موجودی انبار: هنوز ثبت نشده")
    prim = db.get_latest_extracted_any("product_inventory")
    if prim:
        facts["main_source"] = format_datetime(prim.get("created_at"))
        lines.append(f"• آخرین نسخهٔ منبع اصلی: {facts['main_source']}")
    mc = db.get_latest_extracted_any("monthly_consumption")
    if mc:
        facts["monthly_consumption"] = format_datetime(mc.get("created_at"))
        lines.append(f"• مصرف ماهیانهٔ اقلام: {facts['monthly_consumption']}")
    try:
        entries = db.list_site_stock_entries()
    except Exception:  # noqa: BLE001
        entries = []
    dates = sorted({str(e.get("entry_date") or "") for e in entries if e.get("entry_date")})
    if dates:
        facts["site_stock"] = format_date(dates[-1])
        lines.append(f"• آخرین ثبت موجودی روزانهٔ سایت: {facts['site_stock']}")
    lines.append("(ساعت‌ها به وقت تهران)")
    return Answer("\n".join(lines), it, facts=facts)


def exec_pending(ctx: _Ctx, it: Intent) -> Answer:
    from services import main_source_approvals as msa

    reqs = msa.list_pending(ctx.db)
    from bot import keyboards as kb

    if not reqs:
        return Answer("✅ هیچ درخواستی در انتظار تأیید نیست.", it, facts={"count": 0})
    lines = [f"📝 {len(reqs)} درخواست افزودن به منبع اصلی در انتظار تأیید است:"]
    for r in reqs[:5]:
        rec = r.get("record") or {}
        lines.append(f"• #{r['id']} — {r.get('requester_name') or r.get('requester_id')}: "
                     f"{str(rec.get('product_name') or rec.get('id') or '')[:45]}")
    if len(reqs) > 5:
        lines.append(f"… و {len(reqs) - 5} درخواست دیگر")
    lines.append("برای بررسی و تأیید/رد: دکمهٔ «درخواست‌های در انتظار تأیید».")
    return Answer("\n".join(lines), it, buttons=[kb.BTN_MS_PENDING], facts={"count": len(reqs)})


def exec_tundish_stats(ctx: _Ctx, it: Intent) -> Answer:
    from bot.jalali import PERSIAN_MONTH_NAMES

    db = ctx.db
    p = it.slots.get("period")
    months: list[tuple[int, int]] = list(p.get("months") or []) if p else []
    if p and not months:
        # day-level period → the Jalali months it touches
        import jdatetime

        a = jdatetime.date.fromgregorian(date=p["start"])
        b = jdatetime.date.fromgregorian(date=p["end"])
        y, m = a.year, a.month
        while (y, m) <= (b.year, b.month):
            months.append((y, m))
            m += 1
            if m > 12:
                m, y = 1, y + 1
    if not months:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT year, month FROM main_goal_tundish_sequences WHERE year IS NOT NULL "
                "ORDER BY year DESC, month DESC LIMIT 3").fetchall()
        months = sorted((int(r[0]), int(r[1])) for r in rows)
    sec_only = it.slots.get("section")
    secs = [sec_only] if sec_only else ["slab", "bloom", "billet"]
    manual = {(int(r["jalali_year"]), int(r["jalali_month"])): r for r in db.list_monthly_tundish_counts(limit=240)}
    lines = ["🧱 آمار توالی تاندیش" + (f" — {SECTION_FA[sec_only]}" if sec_only else "")]
    facts: dict[str, Any] = {}
    any_data = False
    for y, m in months:
        key = f"m:{y}-{m:02d}"
        parts = []
        for s in secs:
            agg = db.aggregate_sequences(key, s)
            n = int(agg.get("tundish_count") or 0)
            if not n:
                continue
            melts = float(agg.get("melt_count") or 0)
            facts[f"{y}-{m:02d}|{s}"] = {"tundish": n, "melts": melts}
            avg = f"{melts / n:.1f}".rstrip("0").rstrip(".")
            parts.append(f"{SECTION_FA[s]}: {n} تاندیش، {_fmt(melts)} ذوب (میانگین {avg} ذوب/تاندیش)")
        if parts:
            any_data = True
            lines.append(f"• {PERSIAN_MONTH_NAMES[m]} {y} (لاگ توالی): " + " | ".join(parts))
        elif (y, m) in manual:
            r = manual[(y, m)]
            vals = {"billet": r["count_billet"], "bloom": r["count_bloom"], "slab": r["count_slab"]}
            any_data = True
            facts[f"{y}-{m:02d}|manual"] = {s: int(vals[s] or 0) for s in secs}
            lines.append(f"• {PERSIAN_MONTH_NAMES[m]} {y} (ثبت دستی): " +
                         " | ".join(f"{SECTION_FA[s]}: {int(vals[s] or 0)} تاندیش" for s in secs))
        else:
            lines.append(f"• {PERSIAN_MONTH_NAMES[m]} {y}: دادهٔ توالی تاندیش ثبت نشده")
    if not any_data:
        return Answer("\n".join(lines) + "\nبرای این بازه لاگ توالی تاندیش یا تعداد دستی ثبت نشده است.", it,
                      ok=False, facts=facts)
    from bot import keyboards as kb

    return Answer("\n".join(lines), it, buttons=[kb.BTN_MAIN_GOAL], facts=facts)


def exec_open_report(ctx: _Ctx, it: Intent) -> Answer:
    from bot import keyboards as kb

    attr = it.slots.get("report") or ""
    label = getattr(kb, attr, None)
    if not label:
        return Answer("گزارش مورد نظر را پیدا نکردم. «راهنما» را بپرسید.", it, ok=False)
    need = kb.BUTTON_FEATURE.get(label)
    if need and not perm.can_any(ctx.user, need):
        return Answer(f"⛔ به «{label}» دسترسی ندارید.", it, ok=False)
    return Answer(f"برای «{label}» این دکمه را بزنید 👇", it, buttons=[label], facts={"button": label})


def help_text_fa(user: dict | None) -> str:
    ex = [
        (I_STOCK, "«از آجر ۱۶۰۳ چقدر داریم؟» یا «موجودی استوپر»"),
        (I_CRITICAL, "«چی کم داریم؟» / «اقلام بحرانی»"),
        (I_N_TUNDISH, "«برای ۱۰ تاندیش اسلب چی لازمه؟»"),
        (I_COVERAGE, "«شرود چند روز کافیه؟» / «حد تحمل ۱۶۷۲»"),
        (I_CONSUMPTION, "«مصرف ۳۰ روز اخیر» / «مصرف شهریور بیلت»"),
        (I_LAST_UPLOAD, "«آخرین بار موجودی کی آپلود شد؟»"),
        (I_PENDING, "«درخواست‌های در انتظار تأیید»"),
        (I_TUNDISH_STATS, "«شهریور چند تا تاندیش اسلب زدیم؟»"),
        (I_OPEN_REPORT, "«گزارش مازاد را بفرست»"),
    ]
    lines = ["🤖 دستیار هوشمند — با متن یا پیام صوتی بپرسید. پاسخ‌ها مستقیم از دادهٔ ربات است:"]
    for name, sample in ex:
        if user is None or _allowed(user, Intent(name)):
            lines.append(f"• {INTENT_LABEL_FA[name]}: {sample}")
    lines.append("مدل زبانی فقط برای فهم سؤال است و هیچ عددی نمی‌سازد.")
    return "\n".join(lines)


EXECUTORS: dict[str, Callable[[_Ctx, Intent], Answer]] = {
    I_STOCK: exec_stock,
    I_CRITICAL: exec_critical,
    I_COVERAGE: exec_coverage,
    I_N_TUNDISH: exec_n_tundish,
    I_CONSUMPTION: exec_consumption,
    I_LAST_UPLOAD: exec_last_upload,
    I_PENDING: exec_pending,
    I_TUNDISH_STATS: exec_tundish_stats,
    I_OPEN_REPORT: exec_open_report,
}

SETTING_REPHRASE = "assistant_rephrase"


def answer(db: Any, user: dict, text: str, *, use_llm: bool | None = None, model: str | None = None,
           rephrase_answer: bool | None = None, chat: Callable[..., str] | None = None,
           log: bool = True, channel: str = "bot") -> Answer:
    """Full pipeline: route → permission → execute → (optional verified rephrase) → log."""
    from services import voice_assistant as va

    import os

    model = model or db.get_setting(va.SETTING_MODEL) or os.environ.get("OLLAMA_MODEL") or va.DEFAULT_LLM
    if use_llm is None:
        use_llm = True
    if rephrase_answer is None:
        rephrase_answer = db.get_setting(SETTING_REPHRASE) == "1"
    ctx = _Ctx(db, user)
    try:
        codes = known_codes(ctx.inventory)
    except Exception:  # noqa: BLE001
        logger.exception("assistant: inventory load failed")
        codes = set()
    try:
        inv = ctx.inventory
    except Exception:  # noqa: BLE001
        inv = None
    it = route(text, use_llm=use_llm, model=model, chat=chat, known_codes=codes or None, inventory=inv)
    if it.name == I_HELP:
        ans = Answer(help_text_fa(user), it)
    elif it.name == I_UNKNOWN:
        ans = Answer("متوجه سؤال نشدم. " + help_text_fa(user), it, ok=False)
    elif not _allowed(user, it):
        ans = _deny(it)
    else:
        try:
            ans = EXECUTORS[it.name](ctx, it)
        except Exception:  # noqa: BLE001
            logger.exception("assistant executor %s failed", it.name)
            ans = Answer("محاسبهٔ پاسخ انجام نشد؛ لطفاً از منوی مربوط استفاده کنید.", it, ok=False)
    if rephrase_answer and ans.ok and it.name not in (I_HELP, I_OPEN_REPORT) and use_llm:
        better = rephrase(ans.text, model=model, chat=chat)
        if better:
            ans.text, ans.rephrased = better, True
    if log:
        try:
            from bot.activity import log_activity

            log_activity(db, user, "report_assistant_asked", intent=it.name, source=it.source,
                         channel=channel, question=str(text)[:200], ok=bool(ans.ok))
        except Exception:  # noqa: BLE001
            logger.warning("assistant: activity log failed")
    logger.info("assistant intent=%s source=%s ok=%s", it.name, it.source, ans.ok)
    return ans
