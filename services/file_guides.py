"""«❓ راهنمای تهیهٔ فایل» — ONE editable place for every file/photo input guide.

Bot: the «❓ راهنمای تهیهٔ فایل» button next to each file/photo input.
Web: the same text is shown under each upload form (``guide_html``).

Fill ``SYSTEM_STEPS`` when the export path in the plant system is known; until then
the placeholder ``PLACEHOLDER_FA`` is shown + the required file shape (from code).
Not used for «📦 منبع اصلی» (that file is maintained inside the bot itself).
"""
from __future__ import annotations

from html import escape
from pathlib import Path

PLACEHOLDER_FA = "راهنمای مسیر سیستم به‌زودی اضافه می‌شود."

# kind → title
TITLES: dict[str, str] = {
    "product_inventory": "📥 فایل موجودی انبار (Excel)",
    "monthly_consumption": "📥 مصرف ماهیانه مواد (Excel)",
    "mg_production_stats": "📝 آمار تولید (ورود متنی)",
    "mg_sequence": "📤 گزارش جامع تاندیش در سکوئنس (Excel)",
    "mg_production_xlsx": "📤 اکسل آمار تولید (قدیمی)",
    "mg_consumption": "📤 اکسل مصرف تاندیش هر بخش (قدیمی)",
    "mg_bulk": "📦 آپلود گروهی (هدف اصلی)",
}

# The 4 owner-approved inputs (1405/07/19) — shown in the general guide in this order.
MAIN_INPUTS: tuple[str, ...] = ("product_inventory", "monthly_consumption", "mg_production_stats", "mg_sequence")

_PORTAL = (
    "در پورتال «شرکت فولاد خوزستان» (صفحهٔ سامانه‌ها) روی آیکون "
    "«برنامه‌ریزی و کنترل تولید فولادسازی» (SMC) بزنید."
)

# kind → step-by-step export instructions in the plant system (from the owner's screenshots).
SYSTEM_STEPS: dict[str, list[str] | None] = {
    "product_inventory": [
        _PORTAL,
        "در کادر راست «گزارشات» و در کادر چپ «گزارش موجودی مواد نسوز در انبار» را انتخاب کنید.",
        "در صفحهٔ «مقدار موجودی مواد نسوز در انبار»، «کد دسته‌بندی کالا» را از ۱۲۰۰ تا ۱۸۰۰ بگذارید و «جستجو» را بزنید.",
        "وقتی جدول بالا و «ریز اطلاعات» پر شد، آیکون سبز Excel در نوار ابزار بالای صفحه را بزنید.",
        "فایل در Excel باز می‌شود (شیت «ریز اطلاعات»: کد دسته‌بندی، کد و شرح کالا، موجودی). "
        "آن را با Save As به‌صورت «Excel Workbook (.xlsx)» ذخیره و در ربات بفرستید.",
    ],
    "monthly_consumption": [
        _PORTAL,
        "در کادر راست «گزارشات» و در کادر چپ «گزارشات ماهیانه نسوز» را انتخاب کنید.",
        "در صفحهٔ «Refractory Monthly Reports» سال (Year) و ماه (Month) را وارد کنید.",
        "در ستون TUNDISH گزینهٔ «Total Tundish Consumption / Life (4-14)» (هر سه بخش) "
        "یا برای یک بخش «Slab (4-11)»، «Bloom (4-12)» یا «Billet (4-13)» را انتخاب کنید.",
        "دکمهٔ «Search» و سپس «Excel» را بزنید؛ فایل را با پسوند .xlsx ذخیره و ارسال کنید.",
    ],
    "mg_production_stats": [
        "در مرورگر سایت otsteel.ksc.ir را باز کنید؛ در بخش «گزارش های تولید» روی «آمار تولید» بزنید.",
        "«از تاریخ» را ابتدای ماه و «تا تاریخ» را آخرین روز ثبت‌شدهٔ همان ماه بگذارید و «جستجو» را بزنید.",
        "از تب «ریخته گری» وزن محصول هر ماشین (CCM1 تا CCM5) را به کیلوگرم و از تب «کوره» تعداد کل ذوب‌ها "
        "(مجموع ذوب‌های نرمال و با کد فرعی) را بخوانید.",
        "در ربات «📝 آمار تولید» را بزنید و اعداد را به ترتیب پرسش‌ها وارد کنید (عکس پذیرفته نمی‌شود).",
    ],
    "mg_sequence": [
        "در مرورگر سایت otsteel.ksc.ir را باز کنید؛ بخش «نسوز تاندیش» را باز کنید و «گزارش جامع تاندیش در سکوئنس» را بزنید.",
        "در صفحهٔ «سکوئنس های ریخته گری» (otsteel.ksc.ir/ccssequence) «نوع تجهیز» و «تجهیز» را خالی بگذارید "
        "تا هر سه بخش (اسلب، بلوم، بیلت) بیاید؛ «از تاریخ» و «تا تاریخ» را تنظیم و «جستجو» را بزنید.",
        "دکمهٔ «خروجی Excel» را بزنید و همان فایل را (یک فایل برای هر سه بخش) در ربات بفرستید.",
    ],
    "mg_production_xlsx": None,
    "mg_consumption": None,
    "mg_bulk": None,
}

# kind → step screenshots (personal names blurred) in services/file_guides_img/
IMG_DIR = Path(__file__).resolve().parent / "file_guides_img"
IMAGE_COUNTS: dict[str, int] = {
    "product_inventory": 5,
    "monthly_consumption": 3,
    "mg_production_stats": 2,
    "mg_sequence": 2,
}


def guide_images(kind: str | None) -> list[Path]:
    kind = ALIASES.get(kind or "", kind)
    n = IMAGE_COUNTS.get(kind or "", 0)
    prefix = IMG_PREFIX.get(kind or "", kind)
    return [p for p in (IMG_DIR / f"{prefix}_{i}.jpg" for i in range(1, n + 1)) if p.is_file()]


# kind → screenshot file prefix (otsteel «آمار تولید» page screenshots are reused)
IMG_PREFIX: dict[str, str] = {"mg_production_stats": "mg_production_photo"}


# older guide kinds → current input
ALIASES: dict[str, str] = {"mg_consumption": "mg_sequence", "mg_production_photo": "mg_production_stats"}

# kind → required file shape (kept in sync with the parsers in code)
SHAPES: dict[str, list[str]] = {
    "product_inventory": [
        "فرمت: .xlsx — شیت «ریز اطلاعات» (یا اولین شیت).",
        "ستون‌های لازم: کد کالا (شناسه)، محصول/شرح کالا، مقدار، واحد؛ کد دسته‌بندی از ۴ رقم اول شناسه.",
        "فقط شناسه‌های موجود به‌روز می‌شوند؛ شناسهٔ جدید فقط اگر کد ۴رقمی‌اش در منبع اصلی باشد و ۱۸۰۰ نباشد.",
        "نرخ‌های مصرف (نوسازی/وصله/کف ریخته‌گری) از این فایل خوانده نمی‌شوند؛ برای کدهای تازه از «📦 منبع اصلی ← 📥 ورود نرخ‌های مصرف» استفاده کنید.",
    ],
    "monthly_consumption": [
        "فرمت: .xlsx",
        "ستون‌های لازم: ماده (نام ماده)، ماه، مقدار، واحد؛ اختیاری: نوع تاندیش، کد کالا، کد دسته‌بندی، وضعیت، توضیحات.",
    ],
    "mg_production_stats": [
        "ورود متنی (بدون فایل): سال/ماه شمسی (مثلاً 1405/07)، سپس وزن محصول ccm1 (اسلب ۱)، ccm2 (اسلب ۲)، "
        "ccm3 (بلوم)، ccm4 (بیلت ۱)، ccm5 (بیلت ۲) به کیلوگرم از ابتدای ماه، و تعداد کل ذوب‌ها.",
        "ارقام فارسی/انگلیسی و جداکنندهٔ هزارگان «,» پذیرفته است؛ در محاسبات به تن تبدیل می‌شود.",
    ],
    "mg_production_xlsx": [
        "فرمت: .xlsx — سطرهای CCM/PRODUCT با تناژ هر ماشین؛ ماه شمسی در نام فایل/شیت/سربرگ (مثل «شهریور ۱۴۰۵»).",
    ],
    "mg_consumption": [
        "فرمت: .xlsx — جدول مصرف مواد تاندیش بخش (ردیف‌های تاندیش + ستون‌های مواد).",
        "یا لاگ سکوئنس تاندیش (ستون‌های ماشین، تاندیش، تعداد ذوب، مدت، شرود/نازل/تیوب).",
        "ماه شمسی در نام فایل بیاید، مثلاً «مصرف تاندیش بیلت شهریور ۱۴۰۵.xlsx».",
    ],
    "mg_sequence": [
        "فرمت: .xlsx — خروجی Excel همان صفحه با ستون‌های ماشین، تاندیش، تاندیشکار، تعداد ذوب، مدت سکوئنس، "
        "ذوب اول/آخر سکوئنس (شماره ذوب، شروع/پایان ریخته‌گری)، تعویض شرود، تعویض نازل بیرونی، تیوب چنجر.",
        "بخش از ستون «ماشین» (اسلب ۱/۲ ← اسلب، بلوم ← بلوم، بیلت ۱/۲ ← بیلت) و ماه از تاریخ شروع ریخته‌گری "
        "تشخیص داده می‌شود؛ هر ماه/بخش جداگانه جایگزین می‌شود. جایگزین آپلودهای جداگانهٔ مصرف تاندیش هر بخش.",
    ],
    "mg_bulk": [
        "هر تعداد فایل و هر ترتیب: اکسل آمار تولید، "
        "اکسل مصرف تاندیش بیلت/بلوم/اسلب، اکسل لاگ سکوئنس تاندیش.",
        "نوع فایل و ماه خودکار تشخیص داده می‌شود؛ ماه شمسی را در نام فایل بنویسید.",
    ],
}
SHAPES["mg_bulk"] = SHAPES["mg_bulk"] + ["—"] + SHAPES["mg_production_xlsx"] + SHAPES["mg_consumption"][:1]

GENERAL_FA = (
    "راهنمای تهیهٔ فایل — چهار ورودی اصلی («📤 ورود فایل‌ها»):\n"
    + "\n".join(f"{i}. {TITLES[k]}" for i, k in enumerate(MAIN_INPUTS, 1))
    + "\n\nهنگام انتظار برای هر فایل، همین دکمه راهنمای همان فایل را نشان می‌دهد."
)


def kinds() -> list[str]:
    return list(TITLES)


def guide_text(kind: str | None) -> str:
    """Plain-text guide for the bot (general list when kind unknown)."""
    kind = ALIASES.get(kind or "", kind)
    if not kind or kind not in TITLES:
        return GENERAL_FA
    steps = SYSTEM_STEPS.get(kind)
    lines = [f"❓ راهنمای تهیهٔ فایل — {TITLES[kind]}", ""]
    if steps:
        lines.append("مسیر در سیستم:")
        lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    else:
        lines.append(f"مسیر در سیستم: {PLACEHOLDER_FA}")
    lines.append("")
    lines.append("شکل فایل لازم:")
    lines += [f"• {s}" for s in SHAPES.get(kind, []) if s != "—"]
    return "\n".join(lines)


def guide_html(kind: str) -> str:
    """Small HTML block for web upload forms (text + step screenshots)."""
    kind = ALIASES.get(kind, kind)
    steps = SYSTEM_STEPS.get(kind)
    parts = [f"<details class=\"file-guide\"><summary>❓ راهنمای تهیهٔ فایل — {escape(TITLES.get(kind, kind))}</summary>"]
    if steps:
        parts.append("<ol>" + "".join(f"<li>{escape(s)}</li>" for s in steps) + "</ol>")
    else:
        parts.append(f"<p class=\"muted\">مسیر در سیستم: {escape(PLACEHOLDER_FA)}</p>")
    parts.append("<ul>" + "".join(f"<li>{escape(s)}</li>" for s in SHAPES.get(kind, []) if s != "—") + "</ul>")
    imgs = guide_images(kind)
    if imgs:
        parts.append("<div class=\"file-guide-imgs\">" + "".join(
            f"<a href=\"/file-guides/{escape(p.name)}\" target=\"_blank\">"
            f"<img src=\"/file-guides/{escape(p.name)}\" alt=\"مرحلهٔ {i}\" loading=\"lazy\" "
            f"style=\"max-width:100%;margin:4px 0;border:1px solid #ccc\"></a>"
            for i, p in enumerate(imgs, 1)) + "</div>")
    parts.append("</details>")
    return "".join(parts)
