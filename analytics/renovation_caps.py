"""سقف نوسازی تاندیش — یک منبع مشترک برای ربات و وب (نسخهٔ 1.7.0، قاعدهٔ کاربر ۱۴۰۵/۰۸/۱۸).

دادهٔ تأییدشدهٔ کاربر:
  • سقف سالانه: بیلت ≤ ۵، بلوم ≤ ۲، اسلب ≤ ۵ تاندیش در سال (جلالی).
  • سقف ماهانه: در هر بخش حداکثر ۱ تاندیش در ماه نوسازی می‌شود.
  • نوسازی پیوسته نیست و معمولاً در نیمهٔ دوم سال (مهر تا اسفند) انجام می‌شود.

پیامد برای همهٔ محاسبه‌ها («با نوسازی»):
  • مواد «پچینگ» همچنان برای همهٔ تاندیش‌ها حساب می‌شود؛
  • مواد «نوسازی» فقط برای تعداد محدودِ تاندیش نوسازی‌شده حساب می‌شود:
      - درخواست خام N تاندیش (بدون بازهٔ زمانی):  min(N، سقف سالانهٔ بخش)
        مثال: ۱۰۰ تاندیش اسلب → پچینگ × ۱۰۰ و نوسازی × ۵ (نه × ۱۰۰).
      - گزارش‌های افق‌دار (اقلام بحرانی: داخلی ۳ ماه، وارداتی ۶ ماه):
        نوسازی در افق = min(تعداد تاندیش افق، ماه‌های نیمهٔ دوم سالِ داخل افق × ۱،
        سقف سالانهٔ هر سال جلالی داخل افق). ماه‌های نیمهٔ اول (فروردین تا شهریور)
        نوسازی ندارند؛ نوسازی به‌طور یکنواخت در نیمهٔ اول پخش نمی‌شود.
  • «بدون نوسازی» بدون تغییر: فقط پچینگ.
  • سطح ریخته‌گری بدون تغییر: × کل تاندیش‌ها.
سابقهٔ نوسازی‌های انجام‌شده در سال جاری در دادهٔ ربات ثبت نمی‌شود؛ پس سقف سالانهٔ کامل
در نظر گرفته می‌شود (برآورد محافظه‌کارانه).
"""
from __future__ import annotations

from typing import Iterable, Mapping

SECTIONS: tuple[str, ...] = ("billet", "bloom", "slab")
SECTION_FA: dict[str, str] = {"billet": "بیلت", "bloom": "بلوم", "slab": "اسلب"}

# سقف سالانهٔ نوسازی (تعداد تاندیش در یک سال جلالی)
ANNUAL_RENOVATION_CAP: dict[str, int] = {"billet": 5, "bloom": 2, "slab": 5}
# سقف ماهانه برای هر بخش
MONTHLY_RENOVATION_CAP: int = 1
# نیمهٔ دوم سال جلالی: مهر (۷) تا اسفند (۱۲)
RENOVATION_FIRST_MONTH: int = 7


def is_renovation_month(month: int) -> bool:
    """True for مهر..اسفند (the months renovation usually happens in)."""
    return RENOVATION_FIRST_MONTH <= int(month) <= 12


def window_months(year: int, month: int, n_months: int) -> list[tuple[int, int]]:
    """``n_months`` Jalali months starting at (year, month), the start month included."""
    out: list[tuple[int, int]] = []
    y, m = int(year), int(month)
    for _ in range(max(0, int(n_months))):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def renovation_slots(section: str, months: Iterable[tuple[int, int]]) -> int:
    """Max renovations of one section allowed in ``months`` (monthly + annual caps, 2nd half only)."""
    per_year: dict[int, int] = {}
    for y, m in months:
        if is_renovation_month(m):
            per_year[int(y)] = per_year.get(int(y), 0) + MONTHLY_RENOVATION_CAP
    cap = ANNUAL_RENOVATION_CAP[section]
    return int(sum(min(n, cap) for n in per_year.values()))


def capped_renovations_for_n(section: str, n: float) -> float:
    """Raw «N تاندیش» request (no period): renovation count = min(N, annual cap)."""
    n = max(0.0, float(n or 0))
    return float(min(n, float(ANNUAL_RENOVATION_CAP[section])))


def capped_renovations_for_mix(mix: Mapping[str, float]) -> dict[str, float]:
    return {sec: capped_renovations_for_n(sec, mix.get(sec) or 0) for sec in SECTIONS}


def window_renovations(
    section: str, monthly_tundish: float, year: int, month: int, horizon_months: int
) -> float:
    """Renovations of one section over a horizon starting at (year, month).

    = min(tundishes in the horizon (monthly × H), 2nd-half months in the horizon × 1,
    annual cap per Jalali year in the horizon).
    """
    total = max(0.0, float(monthly_tundish or 0)) * max(0, int(horizon_months))
    slots = renovation_slots(section, window_months(year, month, horizon_months))
    return float(min(total, float(slots)))


def window_renovations_all(
    monthly_counts: Mapping[str, float], year: int, month: int, horizon_months: int
) -> dict[str, float]:
    return {
        sec: window_renovations(sec, monthly_counts.get(sec) or 0, year, month, horizon_months)
        for sec in SECTIONS
    }


def caps_text_fa() -> str:
    """«سقف نوسازی: اسلب ۵، بیلت ۵، بلوم ۲ در سال؛ هر بخش حداکثر ۱ در ماه؛ مهر تا اسفند»."""
    return (
        f"سقف نوسازی: اسلب {ANNUAL_RENOVATION_CAP['slab']}، بیلت {ANNUAL_RENOVATION_CAP['billet']} و "
        f"بلوم {ANNUAL_RENOVATION_CAP['bloom']} تاندیش در سال؛ هر بخش حداکثر "
        f"{MONTHLY_RENOVATION_CAP} تاندیش در ماه، معمولاً در نیمهٔ دوم سال (مهر تا اسفند)"
    )


def fmt(v: float) -> str:
    v = float(v)
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


# Shared Persian mode descriptions (bot keyboards/prompts + web forms use the SAME text).
MODE_WITH_TEXT_FA = (
    "پچینگ برای همهٔ تاندیش‌ها + نوسازی فقط برای تاندیش‌های نوسازی‌شده "
    "(حداکثر: اسلب ۵، بیلت ۵، بلوم ۲ در سال؛ هر بخش ۱ در ماه)"
)
MODE_WITHOUT_TEXT_FA = "فقط پچینگ"
