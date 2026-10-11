#!/usr/bin/env python3
"""Build the Persian user guide PDF for @nasoz_bot.

Re-run any time (e.g. after screenshots land in /workspace/guide_screens/{bot,web}/):
    /workspace/guide_src/.venv/bin/python /workspace/guide_src/build.py
Content lives in content.py; this file holds helpers, CSS, and the renderer.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
SCREENS = Path("/workspace/guide_screens")
FINAL = SRC / "screens_final"
import os
OUT_PDF = Path(os.environ.get("GUIDE_OUT_PDF", "/workspace/nasoz_guide.pdf"))
OUT_HTML = SRC / "guide.html"

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(s: object) -> str:
    return str(s).translate(FA_DIGITS)


# ----------------------------------------------------------------- document model
class Doc:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.toc: list[tuple[int, str, str]] = []  # (level, id, title)
        self.n = [0, 0, 0]
        self.fig_n = 0
        self.figs_total = 0
        self.figs_found = 0
        self.missing: list[str] = []

    # headings ---------------------------------------------------------------
    def h1(self, title: str, anchor: str) -> None:
        self.n = [self.n[0] + 1, 0, 0]
        num = fa(self.n[0])
        full = f"{num}. {title}"
        self.toc.append((1, anchor, full))
        self.parts.append(f'<h1 id="{anchor}" class="chapter">{html.escape(full)}</h1>')

    def h2(self, title: str, anchor: str) -> None:
        self.n[1] += 1
        self.n[2] = 0
        num = fa(f"{self.n[0]}.{self.n[1]}")
        full = f"{num} {title}"
        self.toc.append((2, anchor, full))
        self.parts.append(f'<h2 id="{anchor}">{html.escape(full)}</h2>')

    def h3(self, title: str) -> None:
        self.parts.append(f"<h3>{html.escape(title)}</h3>")

    # blocks -------------------------------------------------------------------
    def raw(self, s: str) -> None:
        self.parts.append(s)

    def p(self, s: str) -> None:
        self.parts.append(f"<p>{s}</p>")

    def ul(self, items: list[str], cls: str = "") -> None:
        li = "".join(f"<li>{i}</li>" for i in items)
        self.parts.append(f'<ul class="{cls}">{li}</ul>')

    def ol(self, items: list[str]) -> None:
        li = "".join(f"<li>{i}</li>" for i in items)
        self.parts.append(f"<ol>{li}</ol>")

    def table(self, headers: list[str], rows: list[list[str]], cls: str = "", widths: list[str] | None = None) -> None:
        cols = ""
        if widths:
            cols = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
        th = "".join(f"<th>{h}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        self.parts.append(f'<table class="{cls}">{cols}<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>')

    def callout(self, kind: str, title: str, body: str) -> None:
        icon = {"info": "ℹ", "warn": "⚠", "rule": "§", "tip": "✓", "danger": "⛔"}.get(kind, "•")
        self.parts.append(
            f'<div class="callout {kind}"><div class="ct"><span class="ci">{icon}</span>{title}</div><div class="cb">{body}</div></div>'
        )

    def flow(self, steps: list[str], title: str = "") -> None:
        cells = []
        for i, s in enumerate(steps):
            cells.append(f'<td class="fstep"><div class="fbox">{s}</div></td>')
            if i < len(steps) - 1:
                cells.append('<td class="farrow">◀</td>')
        t = f'<div class="ftitle">{title}</div>' if title else ""
        self.parts.append(f'<div class="flow">{t}<table class="flowt"><tr>{"".join(cells)}</tr></table></div>')

    def vflow(self, steps: list[str], title: str = "") -> None:
        out = []
        for i, s in enumerate(steps):
            out.append(f'<div class="vbox">{s}</div>')
            if i < len(steps) - 1:
                out.append('<div class="varrow">▼</div>')
        t = f'<div class="ftitle">{title}</div>' if title else ""
        self.parts.append(f'<div class="vflow">{t}{"".join(out)}</div>')

    def kbd(self, rows: list[list[str]], title: str = "") -> None:
        """Mock-up of a Bale reply keyboard (always shown; complements real screenshots)."""
        trs = []
        for r in rows:
            tds = "".join(f'<td colspan="{12 // len(r)}"><span class="key">{html.escape(b)}</span></td>' for b in r)
            trs.append(f"<tr>{tds}</tr>")
        t = f'<div class="kt">{title}</div>' if title else ""
        self.parts.append(f'<div class="kbd">{t}<table>{"".join(trs)}</table></div>')

    def fig(self, fid: str, caption: str, width_mm: float | None = None) -> None:
        """Real screenshot figure from screens_final/<fid>.png (built by figures.py).
        Figures whose screenshot was not captured are omitted entirely (no placeholder)."""
        from PIL import Image as _Im
        cand = FINAL / f"{fid}.png"
        self.figs_total += 1
        if not cand.is_file():
            self.missing.append(fid)
            return
        self.fig_n += 1
        self.figs_found += 1
        w, h = _Im.open(cand).size
        if width_mm is None:
            if fid.startswith("w"):
                width_mm = 140.0
            elif w > 1000:
                width_mm = 176.0
            elif w > 700:
                width_mm = 115.0
            elif w > 450:
                width_mm = 98.0
            else:
                width_mm = 80.0
        width_mm = min(width_mm, 176.0, 165.0 * w / h)
        cap = f'<figcaption>شکل {fa(self.fig_n)} — {caption}</figcaption>'
        self.parts.append(f'<figure class="fig"><img src="{cand.as_uri()}" style="width:{width_mm:.1f}mm">{cap}</figure>')

    def pagebreak(self) -> None:
        self.parts.append('<div class="pb"></div>')


# ----------------------------------------------------------------- CSS
def css() -> str:
    font_r = (SRC / "fonts" / "Vazirmatn-Regular.ttf").as_uri()
    font_b = (SRC / "fonts" / "Vazirmatn-Bold.ttf").as_uri()
    return f"""
@font-face {{ font-family: 'Vazirmatn'; src: url('{font_r}'); font-weight: normal; }}
@font-face {{ font-family: 'Vazirmatn'; src: url('{font_b}'); font-weight: bold; }}
@font-face {{ font-family: 'EmojiOnly'; src: url('file:///usr/share/fonts/truetype/noto/NotoColorEmoji.ttf'); unicode-range: U+A9, U+AE, U+200D, U+203C, U+2049, U+20E3, U+2122, U+2139, U+2194-2199, U+21A9-21AA, U+231A-231B, U+2328, U+23CF, U+23E9-23F3, U+23F8-23FA, U+24C2, U+25AA-25AB, U+25B6, U+25C0, U+25FB-25FE, U+2600-2604, U+260E, U+2611, U+2614-2615, U+2618, U+261D, U+2620, U+2622-2623, U+2626, U+262A, U+262E-262F, U+2638-263A, U+2640, U+2642, U+2648-2653, U+265F-2660, U+2663, U+2665-2666, U+2668, U+267B, U+267E-267F, U+2692-2697, U+2699, U+269B-269C, U+26A0-26A1, U+26A7, U+26AA-26AB, U+26B0-26B1, U+26BD-26BE, U+26C4-26C5, U+26C8, U+26CE-26CF, U+26D1, U+26D3-26D4, U+26E9-26EA, U+26F0-26F5, U+26F7-26FA, U+26FD, U+2702, U+2705, U+2708-270D, U+270F, U+2712, U+2714, U+2716, U+271D, U+2721, U+2728, U+2733-2734, U+2744, U+2747, U+274C, U+274E, U+2753-2755, U+2757, U+2763-2764, U+2795-2797, U+27A1, U+27B0, U+27BF, U+2934-2935, U+2B05-2B07, U+2B1B-2B1C, U+2B50, U+2B55, U+3030, U+303D, U+3297, U+3299, U+1F004, U+1F0CF, U+1F170-1F171, U+1F17E-1F17F, U+1F18E, U+1F191-1F19A, U+1F1E6-1F1FF, U+1F201-1F202, U+1F21A, U+1F22F, U+1F232-1F23A, U+1F250-1F251, U+1F300-1F321, U+1F324-1F393, U+1F396-1F397, U+1F399-1F39B, U+1F39E-1F3F0, U+1F3F3-1F3F5, U+1F3F7-1F4FD, U+1F4FF-1F53D, U+1F549-1F54E, U+1F550-1F567, U+1F56F-1F570, U+1F573-1F57A, U+1F587, U+1F58A-1F58D, U+1F590, U+1F595-1F596, U+1F5A4-1F5A5, U+1F5A8, U+1F5B1-1F5B2, U+1F5BC, U+1F5C2-1F5C4, U+1F5D1-1F5D3, U+1F5DC-1F5DE, U+1F5E1, U+1F5E3, U+1F5E8, U+1F5EF, U+1F5F3, U+1F5FA-1F64F, U+1F680-1F6C5, U+1F6CB-1F6D2, U+1F6D5-1F6D8, U+1F6DC-1F6E5, U+1F6E9, U+1F6EB-1F6EC, U+1F6F0, U+1F6F3-1F6FC, U+1F7E0-1F7EB, U+1F7F0, U+1F90C-1F93A, U+1F93C-1F945, U+1F947-1F9FF, U+1FA70-1FA7C, U+1FA80-1FA8A, U+1FA8E-1FAC6, U+1FAC8, U+1FACD-1FADC, U+1FADF-1FAEA, U+1FAEF-1FAF8, U+E0030-E0039, U+E0061-E007A, U+E007F, U+FE4E5-FE4EE, U+FE82C, U+FE82E-FE837; }}
@page {{
  size: A4 portrait; margin: 22mm 17mm 18mm 17mm;
  @top-right {{ content: "راهنمای جامع ربات مدیریت مواد نسوز ‎@nasoz_bot"; font-family: Vazirmatn; font-size: 8pt; color: #5b6b7f; vertical-align: bottom; padding-bottom: 3mm; }}
  @top-left {{ content: string(chap); font-family: Vazirmatn; font-size: 8pt; color: #1f4e79; font-weight: bold; vertical-align: bottom; padding-bottom: 3mm; }}
  @bottom-center {{ content: "صفحه " counter(page, persian) " از " counter(pages, persian); font-family: Vazirmatn; font-size: 8pt; color: #5b6b7f; }}
  @bottom-right {{ content: "۱۴۰۵/۰۷/۱۹"; font-family: Vazirmatn; font-size: 7.5pt; color: #8a97a8; }}
  border-top: none;
}}
@page cover {{ margin: 0; @top-right {{ content: none; }} @top-left {{ content: none; }} @bottom-center {{ content: none; }} @bottom-right {{ content: none; }} }}
html {{ direction: rtl; }}
body {{ font-family: 'Vazirmatn', 'EmojiOnly', 'DejaVu Sans', 'Noto Sans Symbols'; font-size: 10pt; line-height: 1.75; color: #1c2733; text-align: right; }}
.cover {{ page: cover; height: 297mm; width: 210mm; position: relative; background: #0f3057; color: white; text-align: center; }}
.cover .band {{ position: absolute; top: 0; right: 0; left: 0; height: 14mm; background: #e8a33d; }}
.cover .band2 {{ position: absolute; bottom: 0; right: 0; left: 0; height: 30mm; background: #0a2240; }}
.cover .inner {{ padding-top: 70mm; }}
.cover .t1 {{ font-size: 28pt; font-weight: bold; line-height: 1.5; margin: 0 18mm; }}
.cover .t2 {{ font-size: 22pt; font-weight: bold; color: #e8a33d; margin-top: 6mm; direction: ltr; }}
.cover .t3 {{ font-size: 13pt; margin-top: 14mm; color: #d7e3f1; line-height: 1.9; }}
.cover .meta {{ position: absolute; bottom: 42mm; right: 0; left: 0; font-size: 11pt; color: #d7e3f1; }}
.cover .meta b {{ color: #fff; }}
.cover .chips {{ margin-top: 16mm; }}
.cover .chip {{ display: inline-block; border: 1px solid #e8a33d; color: #fff; border-radius: 4mm; padding: 1mm 5mm; margin: 1.5mm; font-size: 10pt; }}
.cover .foot {{ position: absolute; bottom: 10mm; right: 0; left: 0; font-size: 9pt; color: #9fb3c8; }}
h1.chapter {{ string-set: chap content(); page-break-before: always; font-size: 18pt; color: #0f3057; border-bottom: 2.5pt solid #e8a33d; padding-bottom: 2mm; margin: 0 0 5mm 0; bookmark-level: 1; }}
h2 {{ font-size: 13pt; color: #1f4e79; margin: 7mm 0 2mm 0; padding-right: 3mm; border-right: 4pt solid #1f4e79; bookmark-level: 2; page-break-after: avoid; }}
h3 {{ font-size: 11pt; color: #0f3057; margin: 4mm 0 1mm 0; bookmark-level: none; page-break-after: avoid; }}
h1.toc-title {{ string-set: chap "فهرست مطالب"; font-size: 18pt; color: #0f3057; border-bottom: 2.5pt solid #e8a33d; bookmark-level: 1; margin-top: 0; }}
p {{ margin: 1.5mm 0; }}
ul, ol {{ margin: 1mm 0; padding-right: 6mm; padding-left: 0; }}
li {{ margin: 0.6mm 0; }}
span.ltr {{ direction: ltr; unicode-bidi: embed; }}
code, .code {{ font-family: 'Vazirmatn', 'DejaVu Sans'; font-size: 8pt; background: #eef2f7; padding: 0 1mm; border-radius: 1mm; direction: ltr; unicode-bidi: embed; }}
.btn {{ background: #e3edf8; border: 0.6pt solid #9db7d5; border-radius: 1.2mm; padding: 0 1.4mm; font-size: 9pt; white-space: nowrap; color: #0f3057; }}
table {{ border-collapse: collapse; width: 100%; margin: 2.5mm 0 3.5mm 0; font-size: 8.8pt; line-height: 1.55; page-break-inside: auto; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th {{ background: #1f4e79; color: #fff; font-weight: bold; padding: 1.4mm 1.6mm; border: 0.5pt solid #1f4e79; text-align: center; }}
td {{ border: 0.5pt solid #b9c6d6; padding: 1.2mm 1.6mm; vertical-align: top; text-align: right; }}
tbody tr:nth-child(even) td {{ background: #f4f7fb; }}
table.matrix td {{ text-align: center; }}
table.compact {{ font-size: 8.2pt; line-height: 1.4; margin: 1.5mm 0 2mm 0; }}
table.keep {{ page-break-inside: avoid; }}
table.compact td, table.compact th {{ padding: 0.7mm 1.4mm; }}
table.matrix td:first-child {{ text-align: right; font-weight: bold; }}
.y {{ color: #1b7a3a; font-weight: bold; }} .n {{ color: #b23b3b; }} .c {{ color: #b7791f; font-weight: bold; }}
.callout {{ border-radius: 2mm; padding: 2mm 3.5mm; margin: 3mm 0; page-break-inside: avoid; border-right: 4pt solid; }}
.callout .ct {{ font-weight: bold; margin-bottom: 0.8mm; }}
.callout .ci {{ display: inline-block; width: 5mm; }}
.callout.info {{ background: #eaf3fc; border-color: #2f74c0; }}
.callout.warn {{ background: #fff6e5; border-color: #e09b1a; }}
.callout.rule {{ background: #eef7ee; border-color: #2e8b57; }}
.callout.tip  {{ background: #f1f0fb; border-color: #6a5acd; }}
.callout.danger {{ background: #fdeeee; border-color: #c0392b; }}
.flow {{ margin: 3mm 0; page-break-inside: avoid; }}
.ftitle {{ font-weight: bold; color: #1f4e79; margin-bottom: 1mm; font-size: 9.5pt; }}
table.flowt {{ width: 100%; border: none; margin: 0; }}
table.flowt td {{ border: none; background: none !important; padding: 0; vertical-align: middle; }}
.fbox {{ background: #0f3057; color: #fff; border-radius: 2mm; padding: 2mm 1.5mm; text-align: center; font-size: 8.4pt; line-height: 1.5; }}
td.farrow {{ width: 5mm; text-align: center !important; color: #e8a33d; font-size: 11pt; }}
.vflow {{ margin: 3mm 18mm; page-break-inside: avoid; text-align: center; }}
.vbox {{ background: #eaf3fc; border: 0.8pt solid #2f74c0; border-radius: 2mm; padding: 1.5mm 3mm; font-size: 9pt; }}
.varrow {{ color: #e8a33d; font-size: 10pt; line-height: 1.2; }}
.kbd {{ background: #dfe7ee; border-radius: 2.5mm; padding: 2mm; margin: 3mm 22mm; page-break-inside: avoid; }}
.kbd .kt {{ font-size: 8pt; color: #5b6b7f; text-align: center; margin-bottom: 1mm; }}
.kbd table {{ margin: 0; table-layout: fixed; }}
.kbd td {{ border: none; background: none !important; padding: 0.7mm; text-align: center; }}
.key {{ display: block; background: #fff; border-radius: 1.5mm; padding: 1mm 1mm; font-size: 8.3pt; color: #16324f; box-shadow: 0 0.4mm 0 #aab7c4; }}
figure.fig {{ margin: 3mm 0; text-align: center; page-break-inside: avoid; }}
figure.fig img {{ height: auto; }}
figcaption {{ font-size: 8.5pt; color: #5b6b7f; margin-top: 1mm; }}
.ph {{ border: 1pt dashed #9db7d5; background: #f6f9fc; color: #6b7f96; border-radius: 2mm; height: 24mm; margin: 0 35mm; padding-top: 3mm; font-size: 9pt; line-height: 1.5; }}
.phi {{ font-size: 12pt; }} .phn {{ font-size: 7.5pt; font-family: 'DejaVu Sans Mono', monospace; }}
.pb {{ page-break-after: always; }}
/* TOC */
.toc {{ font-size: 9.6pt; }}
.toc a {{ color: #1c2733; text-decoration: none; }}
.toc .l1 {{ font-weight: bold; color: #0f3057; margin-top: 2.2mm; font-size: 10.5pt; }}
.toc .l2 {{ margin-right: 7mm; line-height: 1.6; }}
.toc a::after {{ content: leader('.') target-counter(attr(href), page, persian); }}
.lead {{ font-size: 10.5pt; color: #33475b; }}
.small {{ font-size: 8.5pt; color: #5b6b7f; }}
.two {{ column-count: 2; column-gap: 6mm; }}
"""


def cover() -> str:
    import content  # noqa: E402
    return """
<section class="cover">
  <div class="band"></div>
  <div class="inner">
    <div class="t1">راهنمای جامع ربات مدیریت مواد نسوز</div>
    <div class="t2">@nasoz_bot</div>
    <div class="t3">ربات بله + پنل وب مدیریت مواد ویژهٔ تاندیش<br>منوها، نقش‌ها، ورودی‌ها، گزارش‌ها و نحوهٔ استفاده</div>
    <div class="chips"><span class="chip">مالک</span><span class="chip">مدیر</span><span class="chip">کاردان مسئول</span><span class="chip">تکنسین</span><span class="chip">مسئول شیفت</span></div>
  </div>
  <div class="meta">تاریخ تهیه: <b>""" + content.GUIDE_DATE_FA + """</b> &nbsp;|&nbsp; مبنا: کد سامانه در نسخهٔ <span style="font-family:DejaVu Sans; font-weight:bold; unicode-bidi:isolate; direction:ltr">""" + content.SRC_REV + """</span> (شاخهٔ main)</div>
  <div class="band2"></div>
  <div class="foot">سند راهنمای کاربری — قابل ارائه</div>
</section>
"""


def toc_html(doc: Doc) -> str:
    items = []
    for level, anchor, title in doc.toc:
        items.append(f'<div class="l{level}"><a href="#{anchor}">{html.escape(re.sub(r"\s*\(\u200e*/[^)]*\)", "", title))}</a></div>')
    return '<h1 class="toc-title" id="toc">فهرست مطالب</h1><div class="toc">' + "".join(items) + "</div>"


_LTR_RE = re.compile(r"(?<![\w/\u200e])\u200e*(/[A-Za-z][\w\-/.?=&;|]*|\{[a-z_]+\}|\.(?:xlsx|xls|pdf|png|jpg)\b)\u200e*")


def isolate_ltr(markup: str) -> str:
    """Wrap Latin paths/commands/placeholders in text nodes with LRI..PDI so RTL bidi keeps them intact."""
    out = []
    for i, seg in enumerate(re.split(r"(<[^>]+>)", markup)):
        if seg.startswith("<"):
            out.append(seg)
        else:
            out.append(_LTR_RE.sub(lambda m: '<span class="ltr">' + m.group(1) + "</span>", seg).replace("\u200e</span>", "</span>"))
    return "".join(out)


def build() -> None:
    sys.path.insert(0, str(SRC))
    import content  # noqa: E402

    doc = Doc()
    content.write(doc)
    body = isolate_ltr(cover() + toc_html(doc) + "".join(doc.parts))
    page = f'<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>راهنمای جامع ربات مدیریت مواد نسوز @nasoz_bot</title><style>{css()}</style></head><body>{body}</body></html>'
    OUT_HTML.write_text(page, encoding="utf-8")
    from weasyprint import HTML

    HTML(string=page, base_url=str(SRC)).write_pdf(str(OUT_PDF))
    print(f"PDF: {OUT_PDF}")
    print(f"figures: {doc.figs_found}/{doc.figs_total} real screenshots found")
    if doc.missing:
        (SRC / "missing_screens.txt").write_text("\n".join(doc.missing) + "\n", encoding="utf-8")
        print(f"missing list -> {SRC / 'missing_screens.txt'}")


if __name__ == "__main__":
    build()
