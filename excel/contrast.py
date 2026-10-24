"""Readable text on dark fills (owner rule 1405/08/02).

Any Excel cell whose solid fill is dark gets a white font (only when its font colour is
dark / unset), so headers like «#1F4E79» stay readable. Applied to every workbook just
before saving; template-locked reports included (only the font colour changes).
"""
from __future__ import annotations

from copy import copy

# Office 2013+ default theme (index → RGB) for theme-coloured fills/fonts
_THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"]


def luminance(hex_rgb: str) -> float:
    """WCAG relative luminance of ``RRGGBB`` / ``AARRGGBB`` (0 = black, 1 = white)."""
    h = str(hex_rgb or "").lstrip("#")[-6:]
    if len(h) != 6:
        return 1.0
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 1.0

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def is_dark(hex_rgb: str) -> bool:
    # «looks dark» threshold: mid-tones such as #b26a00 / #ef6c00 count as dark (owner asked
    # for light text on any dark background); pastel fills (#A9D08E, #FFF2CC …) stay black.
    return luminance(hex_rgb) < 0.30


def _tinted(h: str, tint: float) -> str:
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    if tint < 0:
        rgb = [round(c * (1 + tint)) for c in rgb]
    elif tint > 0:
        rgb = [round(c + (255 - c) * tint) for c in rgb]
    return "".join(f"{c:02X}" for c in rgb)


def color_hex(color) -> str | None:
    if color is None:
        return None
    t = getattr(color, "type", None)
    if t == "rgb" and isinstance(color.rgb, str):
        return color.rgb[-6:]
    if t == "theme" and color.theme is not None and 0 <= int(color.theme) < len(_THEME):
        return _tinted(_THEME[int(color.theme)], float(color.tint or 0))
    if t == "indexed" and color.indexed is not None:
        from openpyxl.styles.colors import COLOR_INDEX

        i = int(color.indexed)
        if 0 <= i < len(COLOR_INDEX):
            return COLOR_INDEX[i][-6:]
    return None


def fix_workbook_contrast(wb) -> int:
    """Set white font on dark-filled cells whose text is dark. Returns cells changed."""
    n = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                f = cell.fill
                if f is None or f.fill_type != "solid":
                    continue
                bg = color_hex(f.fgColor) or color_hex(f.start_color)
                if not bg or not is_dark(bg):
                    continue
                fc = color_hex(cell.font.color) if cell.font is not None else None
                if fc is None or is_dark(fc):
                    font = copy(cell.font)
                    font.color = "FFFFFFFF"
                    cell.font = font
                    n += 1
    return n
