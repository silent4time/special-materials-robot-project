"""Guide figures from the 1405-07-19 captures (/workspace/guide_screens/{bot,web}).
Crops (Bale: chat pane only; web: page content without browser chrome), pixelates personal data,
composes multi-panel figures. Output: screens_final/<id>.png. Raw screenshots are never modified."""
import json
import re
from pathlib import Path
from PIL import Image, ImageFilter

SRC = Path(__file__).parent
RAW = Path("/workspace/guide_screens")
OUT = SRC / "screens_final"
OUT.mkdir(exist_ok=True)
BOT_BOX = (267, 57, 772, 800)     # Bale Web chat pane (no chat list / browser bar)
WEB_BOX = (215, 57, 1065, 800)    # page content (no tab/address bar)
# Personal names to auto-blur are kept out of the repo: one name per line in private_names.txt (git-ignored).
_NAMES_FILE = SRC / "private_names.txt"
_NAMES = [n.strip() for n in _NAMES_FILE.read_text(encoding="utf-8").splitlines() if n.strip()] if _NAMES_FILE.exists() else []
NAME_RE = re.compile(r"^[(«]?(" + "|".join(map(re.escape, _NAMES)) + r")[)»،,:.\u200e\u200f]*$", re.I) if _NAMES else re.compile(r"(?!x)x")
OCR = json.loads((SRC / "ocr_hits.json").read_text(encoding="utf-8")) if (SRC / "ocr_hits.json").exists() else {}


def P(f, y=None, blur=(), x=None, auto=False):
    return dict(f=f, y=y, blur=list(blur), x=x, auto=auto)


# names in the main-goal records table (web): one box per row for the name parts
MG_NAMES = [b for y in (468, 509, 549) for b in ((137, y - 8, 193, y + 12), (230, y + 8, 256, y + 26))]

SPEC = {
    "c01_start_owner": ("stack", [P("bot/c01_start_owner_a", (446, 743), [(199, 454, 217, 475)]),
                                  P("bot/c01_start_owner_b", (700, 743))]),
    "c04_help": ("row", [P("bot/c04_help_a", (240, 600)), P("bot/c04_help_b", (20, 580))]),
    "c05_status": ("row", [P("bot/c05_status", (450, 605))]),
    "c06_upload_menu": ("row", [P("bot/c06_upload_menu", (500, 743))]),
    "c07_stock_update_prompt": ("row", [P("bot/c07_stock_update_prompt", (425, 743))]),
    "c08_file_guide": ("row", [P("bot/c08_file_guide", (440, 743))]),
    "c10_upload_cancel": ("row", [P("bot/c10_upload_cancel", (510, 743))]),
    "c11_main_source_menu": ("row", [P("bot/c11_main_source_menu", (495, 743))]),
    "c12_full_replace_confirm": ("row", [P("bot/c12_full_replace_confirm", (505, 743)),
                                         P("bot/c18_site_stock_menu", (385, 500))]),
    "c13_download_main_source": ("row", [P("bot/c13_download_main_source", (445, 743))]),
    "c14_add_record": ("row", [P("bot/c14_add_record", (482, 743))]),
    "c15_edit_record": ("row", [P("bot/c15_edit_record", (140, 743))]),
    "c16_add_category": ("row", [P("bot/c16_add_category", (520, 743))]),
    "c17_category_list": ("row", [P("bot/c17_category_list", (375, 743))]),
    "c18_site_stock_menu": ("row", [P("bot/c18_site_stock_menu", (520, 743))]),
    "c19_site_stock_entry": ("row", [P("bot/c19_site_stock_entry_a", (90, 743)), P("bot/c19_site_stock_entry_b", (30, 743))]),
    "c21_tr_menu": ("row", [P("bot/c21_tr_menu", (525, 743))]),
    "c22_tr_mode": ("row", [P("bot/c22_tr_mode", (530, 743))]),
    "c23_tr_step": ("row", [P("bot/c23_tr_step", (490, 743))]),
    "c24_tr_quick": ("row", [P("bot/c24_tr_quick", (400, 743))]),
    "c26_mr_days": ("row", [P("bot/c26_mr_days", (490, 743))]),
    "c27_mr_review": ("row", [P("bot/c27_mr_review_a", (110, 743)), P("bot/c27_mr_review_b", (20, 743))]),
    "c29_mr_edit": ("row", [P("bot/c29_mr_edit", (420, 743))]),
    "c31_wr_review": ("row", [P("bot/c31_wr_review", (400, 743))]),
    "c32_reports_menu": ("stack", [P("bot/c32_reports_menu_a", (450, 743)), P("bot/c32_reports_menu_b", (666, 743))]),
    "c33_range_picker": ("row", [P("bot/c33_range_picker", (440, 743))]),
    "c34_section_step": ("row", [P("bot/c34_section_step", (520, 743))]),
    "c35_daily_result": ("row", [P("bot/c35_daily_result", (375, 600))]),
    "c36_surplus": ("row", [P("bot/c36_surplus", (390, 600))]),
    "c37_remaining": ("row", [P("bot/c37_remaining", (390, 600))]),
    "c38_inbound": ("row", [P("bot/c38_inbound", (285, 743))]),
    "c39_comprehensive": ("row", [P("bot/c39_comprehensive", (350, 600))]),
    "c40_critical_menu": ("row", [P("bot/c40_critical_menu", (405, 743))]),
    "c41_period_result": ("row", [P("bot/c41_period_result", (180, 600))]),
    "c42_critical_mode": ("row", [P("bot/c42_critical_mode", (460, 743))]),
    "c43_critical_result": ("row", [P("bot/c43_critical_result", (60, 330))]),
    "c44_critical_summary": ("row", [P("bot/c43_critical_result", (330, 630)), P("bot/c44_critical_summary_b", (20, 630))]),
    "c45_n_tundish": ("row", [P("bot/c45_n_tundish_a", (500, 743)), P("bot/c45_n_tundish_b", (410, 743)),
                              P("bot/c45_n_tundish_c", (480, 743))]),
    "c46_n_tundish_result": ("row", [P("bot/c46_n_tundish_result", (300, 600))]),
    "c50_mg_menu": ("row", [P("bot/c50_mg_menu", (355, 743), auto=True)]),
    "c51_mg_inputs": ("stack", [P("bot/c51_mg_inputs_a", (395, 743)), P("bot/c51_mg_inputs_b", (700, 743))]),
    "c52_mg_file_guide": ("row", [P("bot/c52_mg_file_guide", (425, 743))]),
    "c53_mg_bulk": ("row", [P("bot/c53_mg_bulk", (405, 743))]),
    "c54_mg_history_range": ("row", [P("bot/c54_mg_history_range", (130, 600), auto=True)]),
    "c56_mg_saved_months": ("row", [P("bot/c56_mg_saved_months", (448, 743), auto=True)]),
    "c57_mg_scn1": ("row", [P("bot/c57_mg_scn1_a", (482, 743)), P("bot/c57_mg_scn1_b", (487, 743))]),
    "c58_mg_scn2_result": ("row", [P("bot/c58_mg_scn2_result", (85, 600), auto=True)]),
    "c60_settings_menu": ("stack", [P("bot/c60_settings_menu_a", (530, 712)), P("bot/c60_settings_menu_b", (673, 743))]),
    "c61_users_menu": ("row", [P("bot/c61_users_menu", (548, 743))]),
    "c62_users_roles": ("row", [P("bot/c62_users_roles", (550, 743))]),
    "c63_invite_confirm": ("row", [P("bot/c63_invite_confirm", (505, 743))]),
    "c64_users_list": ("row", [P("bot/c64_users_list", (505, 743), [(50, 516, 202, 574)])]),
    "c65_activity": ("row", [P("bot/c65_activity", (215, 600))]),
    "c66_tr_settings": ("row", [P("bot/c66_tr_settings_a", (505, 743)), P("bot/c66_tr_settings_b", (335, 743))]),
    "c67_perm_roles": ("row", [P("bot/c67_perm_roles", (175, 743))]),
    "c68_perm_checkboxes": ("row", [P("bot/c68_perm_checkboxes_a", (195, 743)), P("bot/c68_perm_checkboxes_b", (40, 610))]),
    "c69_reminders": ("row", [P("bot/c69_reminders_a", (300, 743), [(4, 350, 158, 366), (4, 364, 240, 380)], auto=True),
                              P("bot/c69_reminders_b", (525, 743))]),
    "c70_stock_group": ("row", [P("bot/c70_stock_group", (480, 743), [(10, 502, 68, 521)])]),
    "c71_appearance": ("row", [P("bot/c71_appearance_a", (550, 743)), P("bot/c71_appearance_b", (487, 743))]),
    "c72_home_reply": ("row", [P("bot/c72_home_reply", (525, 743))]),
    # ---- web (all x relative to WEB_BOX x0 = 215)
    "web_login": ("row", [P("web/web_login", (25, 355), x=(260, 570))]),
    "web_home_owner": ("row", [P("web/web_home_owner", (0, 330))]),
    "web_home_tech": ("row", [P("web/web_home_tech", (0, 280))]),
    "web_stock": ("row", [P("web/web_stock", (0, 600))]),
    "web_tundish_report": ("row", [P("web/web_tundish_report", (0, 600))]),
    "web_tundish_settings": ("row", [P("web/web_tundish_settings", (0, 600))]),
    "web_materials_request": ("row", [P("web/web_materials_request", (0, 600))]),
    "web_materials_return": ("row", [P("web/web_materials_return", (0, 560))]),
    "web_reports": ("row", [P("web/web_reports", (0, 660))]),
    "web_reports_b": ("row", [P("web/web_reports_b", (0, 690))]),
    "web_main_goal": ("row", [P("web/web_main_goal", (0, 595), MG_NAMES)]),
    "web_main_goal_scn2": ("row", [P("web/web_main_goal_scn2", (75, 440))]),
    "web_main_goal_scenarios": ("row", [P("web/web_main_goal_scenarios", (265, 680))]),
    "web_main_source": ("row", [P("web/web_main_source", (0, 445))]),
    "web_settings_web": ("row", [P("web/web_settings_web", (0, 630),
                                   [(600, 170, 765, 202), (435, 452, 540, 474), (675, 452, 767, 604)])]),
    "web_permissions": ("row", [P("web/web_permissions", (0, 600))]),
    "web_reminders": ("row", [P("web/web_reminders", (0, 640), [(520, 410, 747, 617)])]),
}


def pixelate(im, box):
    x0, y0, x1, y1 = (max(0, box[0]), max(0, box[1]), min(im.width, box[2]), min(im.height, box[3]))
    if x1 <= x0 or y1 <= y0:
        return
    reg = im.crop((x0, y0, x1, y1))
    w, h = reg.size
    small = reg.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR)
    reg = small.resize((w, h), Image.NEAREST).filter(ImageFilter.GaussianBlur(2.5))
    im.paste(reg, (x0, y0))


def load(f):
    kind = f.split("/")[0]
    im = Image.open(RAW / f"{f}.png").convert("RGB")
    return im.crop(BOT_BOX if kind == "bot" else WEB_BOX)


def panel(p):
    im = load(p["f"])
    boxes = list(p["blur"])
    if p["auto"]:
        hits = [h for h in OCR.get(f"crop/{p['f']}.png", []) if NAME_RE.search(h[0])]
        lines: list[list] = []
        for t, l, tp, r, b in sorted(hits, key=lambda h: (h[2] + h[4]) / 2):
            cy = (tp + b) / 2
            if lines and abs(lines[-1][4] - cy) <= 7:
                ln = lines[-1]
                ln[0], ln[1], ln[2], ln[3] = min(ln[0], l), min(ln[1], tp), max(ln[2], r), max(ln[3], b)
            else:
                lines.append([l, tp, r, b, cy])
        for l, tp, r, b, _ in lines:  # whole name span on each line, generous padding
            boxes.append((l - 6, tp - 4, r + 6, b + 4))
    for b in boxes:
        pixelate(im, b)
    x0, x1 = p["x"] or (0, im.width)
    y0, y1 = p["y"] or (0, im.height)
    im = im.crop((x0, y0, x1, min(y1, im.height)))
    fr = Image.new("RGB", (im.width + 2, im.height + 2), (190, 198, 210))
    fr.paste(im, (1, 1))
    return fr


def compose(kind, panels):
    ims = [panel(p) for p in panels]
    gap = 14
    if kind == "row" and len(ims) > 1:
        W = sum(i.width for i in ims) + gap * (len(ims) - 1)
        H = max(i.height for i in ims)
        out = Image.new("RGB", (W, H), "white")
        x = W  # RTL: first panel on the right
        for i in ims:
            x -= i.width
            out.paste(i, (x, 0))
            x -= gap
        return out
    if kind == "stack" and len(ims) > 1:
        W = max(i.width for i in ims)
        H = sum(i.height for i in ims) + gap * (len(ims) - 1)
        out = Image.new("RGB", (W, H), "white")
        y = 0
        for i in ims:
            out.paste(i, ((W - i.width) // 2, y))
            y += i.height + gap
        return out
    return ims[0]


def main(only=None):
    for fid, (kind, panels) in SPEC.items():
        if only and fid not in only:
            continue
        compose(kind, panels).save(OUT / f"{fid}.png", optimize=True)
    print(len(SPEC), "figures ->", OUT)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:] or None)
