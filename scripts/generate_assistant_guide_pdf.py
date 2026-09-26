#!/usr/bin/env python3
"""Generate Persian RTL install guide PDF for دستیار هوشمند.

Uses reportlab + arabic_reshaper + bidi (same stack as pdf/generator.py).
Letterhead is NOT applied — standalone install document.
"""
from __future__ import annotations

import sys
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import FONTS_DIR  # noqa: E402

FONT_NAME = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"


def _register_fonts() -> None:
    regular = FONTS_DIR / "DejaVuSans.ttf"
    bold = FONTS_DIR / "DejaVuSans-Bold.ttf"
    if not regular.exists():
        raise FileNotFoundError(f"فونت فارسی یافت نشد: {regular}")
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(regular)))
    if bold.exists():
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
    else:
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(regular)))


def rtl(text: object) -> str:
    s = "" if text is None else str(text)
    if not s.strip():
        return ""
    try:
        return get_display(arabic_reshaper.reshape(s))
    except Exception:  # noqa: BLE001
        return s


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "GuideTitle",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=18,
            alignment=TA_CENTER,
            leading=28,
            spaceAfter=14,
            textColor=colors.HexColor("#1a365d"),
        ),
        "subtitle": ParagraphStyle(
            "GuideSub",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=10,
            alignment=TA_CENTER,
            leading=16,
            spaceAfter=18,
            textColor=colors.HexColor("#4a5568"),
        ),
        "heading": ParagraphStyle(
            "GuideH",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=13,
            alignment=TA_RIGHT,
            leading=20,
            spaceBefore=14,
            spaceAfter=8,
            textColor=colors.HexColor("#2c5282"),
        ),
        "body": ParagraphStyle(
            "GuideBody",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=10,
            alignment=TA_RIGHT,
            leading=17,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "GuideBullet",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=10,
            alignment=TA_RIGHT,
            leading=16,
            rightIndent=8,
        ),
        "code": ParagraphStyle(
            "GuideCode",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=8.5,
            alignment=TA_RIGHT,
            leading=13,
            backColor=colors.HexColor("#f7fafc"),
            borderPadding=6,
            spaceBefore=4,
            spaceAfter=8,
        ),
        "cell": ParagraphStyle(
            "GuideCell",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=8.5,
            alignment=TA_RIGHT,
            leading=13,
        ),
        "cell_h": ParagraphStyle(
            "GuideCellH",
            parent=base["Normal"],
            fontName=FONT_BOLD,
            fontSize=9,
            alignment=TA_CENTER,
            leading=13,
            textColor=colors.white,
        ),
        "footer": ParagraphStyle(
            "GuideFooter",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=8,
            alignment=TA_CENTER,
            leading=12,
            textColor=colors.HexColor("#718096"),
        ),
    }


def P(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(rtl(text), style)


def make_table(headers: list[str], rows: list[list[str]], styles: dict) -> Table:
    data = [[P(h, styles["cell_h"]) for h in headers]]
    for row in rows:
        data.append([P(c, styles["cell"]) for c in row])
    col_w = (A4[0] - 3.2 * cm) / len(headers)
    t = Table(data, colWidths=[col_w] * len(headers))
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c5282")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#edf2f7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                    colors.HexColor("#edf2f7"),
                    colors.white,
                ]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return t


def code_block(lines: list[str], styles: dict) -> list:
    """Render shell/code lines as RTL-safe paragraphs (LTR commands stay readable)."""
    out = []
    for line in lines:
        # Keep ASCII commands mostly as-is; wrap in monospace-ish body
        if any("\u0600" <= ch <= "\u06FF" for ch in line):
            out.append(P(line, styles["code"]))
        else:
            # LTR code: don't reshape; use right-aligned paragraph with raw text
            out.append(Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), styles["code"]))
    return out


def build_story(styles: dict) -> list:
    s: list = []
    s.append(P("راهنمای نصب دستیار هوشمند", styles["title"]))
    s.append(P("بازوی مواد تاندیش (بله) — نصب محلی با Ollama — بدون API ابری", styles["subtitle"]))

    # 1
    s.append(P("۱. معرفی و محدودیت", styles["heading"]))
    s.append(P(
        "دستیار هوشمند یک گفتگوی محلی داخل ربات بله است که فقط دربارهٔ گزارش‌ها و اعداد "
        "گزارش‌محور پاسخ می‌دهد: خلاصه مصرف ماهیانه، ورودی انبار، مصرف روزانه و بازه‌ای، "
        "موجودی و مواد بحرانی، مواد مازاد، پیش‌بینی، پیشنهاد درخواست، فعالیت کاربران و "
        "مفاهیم PDF کامل تحلیل.",
        styles["body"],
    ))
    s.append(P("محدودیت‌های مهم:", styles["body"]))
    s.append(make_table(
        ["مورد", "توضیح"],
        [
            ["دامنه", "فقط گزارش‌ها — آپلود، ویرایش موجودی، درخواست مواد، مدیریت کاربر و تنظیمات را انجام نمی‌دهد"],
            ["اجرا", "مدل روی همین سرور با Ollama (یا سازگار) — بدون API ابری"],
            ["نقش‌ها", "همه نقش‌ها به‌جز تکنسین"],
            ["قطعی بودن", "اگر Ollama خاموش باشد ربات کرش نمی‌کند؛ پیام «دستیار محلی در دسترس نیست…» نشان داده می‌شود"],
        ],
        styles,
    ))
    s.append(Spacer(1, 0.3 * cm))

    # 2
    s.append(P("۲. پیش‌نیاز سخت‌افزار و نرم‌افزار", styles["heading"]))
    s.append(make_table(
        ["مورد", "پیشنهاد"],
        [
            ["سیستم‌عامل", "لینوکس (Ubuntu/Debian یا سازگار)"],
            ["Python", "۳٫۱۰ یا بالاتر (برای خود ربات)"],
            ["RAM", "حداقل ۴ گیگابایت برای qwen2.5:3b — با ۸ گیگابایت یا بیشتر راحت‌تر"],
            ["CPU", "چند هسته کافی است؛ GPU اختیاری"],
            ["فضای دیسک", "چند گیگابایت برای مدل (مدل ۳B حدود ۲ گیگابایت)"],
            ["شبکه", "نصب اولیه و ollama pull نیاز به اینترنت؛ پس از آن کار محلی است"],
        ],
        styles,
    ))
    s.append(Spacer(1, 0.2 * cm))
    s.append(P(
        "مدل پیش‌فرض ربات: qwen2.5:3b. اگر RAM کم است از qwen2.5:1.5b استفاده کنید.",
        styles["body"],
    ))

    # 3
    s.append(P("۳. نصب همراه ربات با install.sh", styles["heading"]))
    s.append(P(
        "در ترمینال واقعی (TTY) داخل ریشهٔ پروژه اجرا کنید:",
        styles["body"],
    ))
    s.extend(code_block([
        "cd special-materials-robot-project",
        "bash install.sh",
    ], styles))
    s.append(P(
        "ویزارد فارسی می‌پرسد: «دستیار هوشمند را هم نصب کنم؟ (Y/n)» — پیش‌فرض بله (Enter یا Y).",
        styles["body"],
    ))
    s.append(P("با تأیید:", styles["body"]))
    for item in [
        "در صورت نبود، Ollama نصب می‌شود",
        "مدل پیش‌فرض (qwen2.5:3b یا مقدار OLLAMA_MODEL) با ollama pull کشیده می‌شود",
        "متغیرهای OLLAMA_* در فایل .env نوشته یا به‌روز می‌شوند",
    ]:
        s.append(P(f"• {item}", styles["bullet"]))
    s.append(Spacer(1, 0.15 * cm))
    s.append(P("پرچم‌های غیرتعاملی:", styles["body"]))
    s.append(make_table(
        ["پرچم", "معنی"],
        [
            ["--with-assistant", "بدون پرسش، Ollama + مدل + .env را نصب/تنظیم کن"],
            ["--no-assistant", "دستیار را رد کن (Ollama نصب نشود)"],
        ],
        styles,
    ))
    s.append(Spacer(1, 0.15 * cm))
    s.extend(code_block([
        "bash install.sh --with-assistant --systemd --start",
        "bash install.sh --no-assistant",
    ], styles))

    # 4
    s.append(P("۴. نصب دستی Ollama و مدل", styles["heading"]))
    s.append(P(
        "اگر install.sh را با --no-assistant زده‌اید یا می‌خواهید دستی نصب کنید:",
        styles["body"],
    ))
    s.extend(code_block([
        "curl -fsSL https://ollama.com/install.sh | sh",
        "ollama serve    # اگر سرویس بالا نیست",
        "ollama pull qwen2.5:3b",
        "ollama pull qwen2.5:1.5b   # مدل سبک‌تر برای RAM کم",
    ], styles))
    s.append(P("سایر گزینه‌ها: llama3.2:3b ، phi3:mini", styles["body"]))

    # 5
    s.append(P("۵. تنظیم .env", styles["heading"]))
    s.append(P("در ریشهٔ پروژه (کنار main.py) این متغیرها را بگذارید:", styles["body"]))
    s.extend(code_block([
        "OLLAMA_BASE_URL=http://127.0.0.1:11434",
        "OLLAMA_MODEL=qwen2.5:3b",
        "OLLAMA_TIMEOUT=60",
    ], styles))
    for item in [
        "اگر این خطوط نباشند، همین مقادیر پیش‌فرض در کد استفاده می‌شوند.",
        "OLLAMA_BASE_URL باید به سرویس محلی اشاره کند؛ به سرویس ابری وصل نکنید.",
        "نمونهٔ کامل در فایل .env.example آمده است.",
    ]:
        s.append(P(f"• {item}", styles["bullet"]))

    # 6
    s.append(P("۶. راه‌اندازی و تست", styles["heading"]))
    s.append(P("تأیید نصب مدل:", styles["body"]))
    s.extend(code_block(["ollama list"], styles))
    s.append(P("تست API محلی با curl:", styles["body"]))
    s.extend(code_block([
        "curl -s http://127.0.0.1:11434/api/tags",
        'curl -s http://127.0.0.1:11434/api/chat -d \'{"model":"qwen2.5:3b","messages":[{"role":"user","content":"hi"}],"stream":false}\'',
    ], styles))
    s.append(P("روشن کردن ربات:", styles["body"]))
    s.extend(code_block([
        "source .venv/bin/activate",
        "python main.py",
        "# یا: bash install.sh --systemd --start",
    ], styles))
    s.append(P(
        "ربات و Ollama باید هم‌زمان روی همان ماشین در دسترس باشند.",
        styles["body"],
    ))

    # 7
    s.append(P("۷. استفاده در بله", styles["heading"]))
    for i, step in enumerate([
        "در بله به بازو /start بزنید.",
        "منوی «گزارش‌ها / تحلیل تاندیش» را باز کنید.",
        "دکمهٔ «🤖 دستیار هوشمند» را بزنید.",
        "سؤال خود را دربارهٔ گزارش‌ها بنویسید.",
        "برای خروج: «پایان گفتگو» یا بازگشت به تحلیل / منوی اصلی.",
    ], 1):
        s.append(P(f"{i}) {step}", styles["bullet"]))
    s.append(P(
        "نقش تکنسین به این منو دسترسی ندارد. امنیت: هیچ درخواست LLM به ابر ارسال نمی‌شود؛ "
        "فقط HTTP محلی به Ollama روی سرور شما.",
        styles["body"],
    ))

    # 8
    s.append(P("۸. رفع اشکال", styles["heading"]))
    s.append(make_table(
        ["مشکل", "کار پیشنهادی"],
        [
            [
                "پیام «دستیار محلی در دسترس نیست؛ Ollama را روی سرور بررسی کنید.»",
                "status سرویس ollama یا ollama serve؛ پورت 11434؛ OLLAMA_BASE_URL",
            ],
            ["مدل در ollama list نیست", "ollama pull مطابق OLLAMA_MODEL"],
            ["پاسخ خیلی کند", "مدل کوچک‌تر (1.5b)، افزایش OLLAMA_TIMEOUT، RAM/CPU بیشتر"],
            ["تکنسین دکمه را نمی‌بیند", "طبق طراحی؛ نقش‌های دیگر را استفاده کنید"],
            ["دو نمونه ربات با یک توکن", "فقط یک polling؛ نمونهٔ اضافه را خاموش کنید"],
        ],
        styles,
    ))
    s.append(Spacer(1, 0.2 * cm))
    s.append(P("لاگ ربات (systemd): journalctl -u nasoz-bot -f", styles["body"]))

    # 9
    s.append(P("۹. به‌روزرسانی", styles["heading"]))
    s.append(P("به‌روزرسانی کد ربات:", styles["body"]))
    s.extend(code_block(["bash install.sh --update"], styles))
    s.append(P("به‌روزرسانی یا تعویض مدل:", styles["body"]))
    s.extend(code_block([
        "ollama pull qwen2.5:3b",
        "ollama pull qwen2.5:1.5b",
        "# سپس OLLAMA_MODEL را در .env عوض کنید و ربات را restart کنید",
        "sudo systemctl restart nasoz-bot",
    ], styles))

    s.append(Spacer(1, 0.5 * cm))
    s.append(P(
        "خلاصه امنیتی: دستیار هوشمند فقط گزارش‌محور است، روی سرور شما اجرا می‌شود، "
        "و به LLM ابری وابسته نیست.",
        styles["footer"],
    ))
    return s


def generate(output: Path) -> Path:
    _register_fonts()
    styles = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=1.6 * cm,
        leftMargin=1.6 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="راهنمای نصب دستیار هوشمند",
        author="بازوی مواد تاندیش",
    )
    doc.build(build_story(styles))
    return output


def main() -> None:
    repo_out = ROOT / "docs" / "راهنمای_نصب_دستیار_هوشمند.pdf"
    user_out = Path("/workspace/راهنمای_نصب_دستیار_هوشمند.pdf")
    path = generate(repo_out)
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    user_out.write_bytes(path.read_bytes())
    print(f"copied {user_out} ({user_out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
