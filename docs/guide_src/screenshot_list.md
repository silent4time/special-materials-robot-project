# Screenshot list — راهنمای @nasoz_bot (rebuild 1405/07/19)

Source of truth: `bale-materials-bot` main after phases 1–4 + QA passes (guide `content.py` SRC_REV). Every `d.fig(id, …)` in `content.py` is listed below; the build omits a figure whose `screens_final/<id>.png` is missing, so the PDF can be built at any time but should only be built when every row is ✅.

## Workflow

1. Capture raw full-window PNGs (1280×800 browser, like before) and save as `/workspace/guide_screens/bot/<id>.png` or `/workspace/guide_screens/web/<id>.png` (id = first column).
2. `python3 crop_screens.py` → `screens_proc/`, then add a `SPEC` entry per id in `figures.py` (crop rows, blur boxes) and run `python3 figures.py` → `screens_final/<id>.png`.
3. Privacy: blur personal names, Bale IDs, phone numbers, web usernames (as before). Never show the bot token.
4. Bale: use Bale Web with the owner account on the live bot unless another role is named. **Read-only paths only** — every row says where to press ✖️ انصراف / ⬅️ بازگشت; never press ✅ تأیید / 💾 / 🗑 / 📨 for a screenshot.
5. Web: use the demo instance (copied data, demo accounts) — http://127.0.0.1:8010 on the box (open in the box browser). Login per row; credentials in `/tmp/guide_demo/credentials.txt`. Demo has no bot token, so nothing it does can message real users.
6. Old screenshots (1405/07/18 09:17–09:43) predate the phase 1–4 menu rewrite (`9fe0a48`, `9557b99`, `8a3903c`, `3a493e3`, `eddc23d`); old `screens_final/` was moved to `screens_final_old_1405-07-18/` so no stale image can enter the PDF.

Totals: Bale 61 + outputs 2 + web 15 = 78 figures; valid now: 1 (web_login); to capture: 77.

## A. Bale bot (ربات بله)

| # | id | Bale button path | account | what must be visible / how to leave | replaces old file(s) | status |
|---|---|---|---|---|---|---|
| 1 | `c01_start_owner` | /start (owner account) | مالک | welcome text + full main menu; blur name | b01_start_welcome_owner | ❌ retake |
| 2 | `c02_start_officer` | /start (کاردان account) | کاردان مسئول | main menu without ⚙️ تنظیمات | — | ❌ retake (new) |
| 3 | `c03_start_technician` | /start (technician or shift-supervisor account) | تکنسین / مسئول شیفت | 3-button menu | — | ❌ retake (new) |
| 4 | `c04_help` | ❓ راهنما | مالک | full help text (may need 2 panels) | b04_help | ❌ retake |
| 5 | `c05_status` | /status | مالک | data status lines | b05_status | ❌ retake |
| 6 | `c06_upload_menu` | 📤 ورود فایل‌ها | مالک | text + 3 buttons + nav row | b08_upload_menu | ❌ retake |
| 7 | `c07_stock_update_prompt` | 📤 ورود فایل‌ها → 📥 به‌روزرسانی موجودی انبار | مالک | new prompt text + ✖️/❓ راهنمای تهیهٔ فایل/❓/⬅️/🏠 — then ✖️ انصراف (do NOT send a file) | b09_upload_wait_file | ❌ retake |
| 8 | `c08_file_guide` | 📤 ورود فایل‌ها → 📥 به‌روزرسانی موجودی انبار → ❓ راهنمای تهیهٔ فایل | مالک | shows «راهنمای مسیر سیستم به‌زودی اضافه می‌شود»; then ✖️ انصراف | — | ❌ retake (new) |
| 9 | `c09_stock_update_result` | (capture from chat history the next time a real warehouse stock upload is done) | کاردان/مدیر | result message + inbound PDF/XLSX with Persian names. ⚠ do not upload just for the screenshot — it changes live stock | — | ❌ retake (new) |
| 10 | `c11_main_source_menu` | 📤 ورود فایل‌ها → 📦 منبع اصلی | مالک | 6 buttons + nav | b11_main_source_menu | ❌ retake |
| 11 | `c12_full_replace_confirm` | … → 📦 منبع اصلی → 📦 جایگزینی کامل منبع اصلی | مالک | warning + ✅ تأیید جایگزینی کامل / ✖️ انصراف — press ✖️ انصراف | — | ❌ retake (new) |
| 12 | `c13_download_main_source` | … → 📦 منبع اصلی → 📄 دانلود اکسل منبع اصلی | مالک | file «منبع_اصلی_<date>.xlsx» + «149 قلم/ردیف داده، بدون احتساب سطر عنوان» | b16_download_main_source | ❌ retake |
| 13 | `c14_add_record` | … → 📦 منبع اصلی → ➕ افزودن رکورد | مالک | first question «کد دسته بندی (کد دسته ۴ رقمی…)» + nav; then ✖️ انصراف | b12_main_source_add_record | ❌ retake |
| 14 | `c15_edit_record` | … → 📦 منبع اصلی → ✏️ ویرایش رکورد → send one ID | مالک | «رکورد فعلی» + «نام فیلد=مقدار» hint; then ✖️ انصراف (send nothing else) | b13_main_source_edit_record | ❌ retake |
| 15 | `c16_add_category` | … → 📦 منبع اصلی → ➕ افزودن کد دسته | مالک | text mentions ⬅️/🏠 and keyboard has them; then ✖️ انصراف | b14_add_category | ❌ retake |
| 16 | `c17_category_list` | … → 📦 منبع اصلی → 📋 کدهای دسته | مالک | PDF + XLSX + «149 قلم … + 1 کد دستهٔ فعال بدون قلم (0922)» | b15_list_categories | ❌ retake |
| 17 | `c18_site_stock_menu` | 📥 موجودی روزانه سایت | تکنسین (or مالک) | 3 sections + nav | b17_site_stock_menu | ❌ retake |
| 18 | `c19_site_stock_entry` | 📥 موجودی روزانه سایت → موجودی مواد اسلب | تکنسین (or مالک) | inline list + ⏭ رد کردن این قلم / ✖️ انصراف / ⬅️ بازگشت به گروه‌های سایت — press ✖️ انصراف (do not ✅ تأیید) | b18_site_stock_inline, b19_site_stock_prompt_qty | ❌ retake |
| 19 | `c21_tr_menu` | 🧾 گزارش تاندیش | مالک | 3 sections + 📜 + nav | b21_tr_menu_owner | ❌ retake |
| 20 | `c22_tr_mode` | 🧾 گزارش تاندیش → 🧾 گزارش تاندیش اسلب | مالک | «۵ فیلد ثابت + 7 قلم» + ✍️/📋/✖️ | b23_tr_mode | ❌ retake |
| 21 | `c23_tr_step` | … → ✍️ ورود مرحله‌ای → send «10» | مالک | «(2/12) خط / ماشین ریخته‌گری» with ↩️ مرحله قبل; then ✖️ انصراف | b24_tr_step | ❌ retake |
| 22 | `c24_tr_quick` | … → 📋 ورود سریع از متن | مالک | sample text; then ✖️ انصراف | b25_tr_quick_text | ❌ retake |
| 23 | `c26_mr_days` | 🛒 درخواست مواد | مالک | days prompt + ۱ روز / 📜 / ✖️ / nav | b34_mr_days | ❌ retake |
| 24 | `c27_mr_review` | 🛒 درخواست مواد → ۱ روز (پیش‌فرض) | مالک | proposal lines with «عدد» + ✅/✏️/📄 پیش‌نویس/✖️/nav — do NOT press ✅ تأیید همه | b35_mr_review | ❌ retake |
| 25 | `c29_mr_edit` | … → ✏️ اصلاح | مالک | edit list + ⬅️ بازگشت به بررسی; then ✖️ انصراف | b36_mr_edit | ❌ retake |
| 26 | `c31_wr_review` | ↩️ برگشت به انبار | مالک | proposal + PDF/XLSX «پیشنهاد_برگشت_به_انبار_…» + nav — do NOT confirm; ✖️ انصراف | b38_wr_review | ❌ retake |
| 27 | `c32_reports_menu` | 📊 گزارش‌ها | مالک | status lines + 9 report buttons + nav (2 panels if tall) | b39_analytics_menu (+_a…_d) | ❌ retake |
| 28 | `c33_range_picker` | 📊 گزارش‌ها → 📈 مصرف روزانه مواد | مالک | range buttons; optional 2nd panel: بازه سفارشی ماه → year buttons | b40_month_range, b41_month_range_period, b43_year_picker, b43b_month_picker | ❌ retake |
| 29 | `c34_section_step` | … → ماه جاری | مالک | «بخش (اختیاری)» همه/اسلب/بلوم/بیلت | — | ❌ retake (new) |
| 30 | `c35_daily_result` | … → همه بخش‌ها | مالک | «مصرف_روزانه_مواد_<date>.pdf/.xlsx» | b44_report_result | ❌ retake |
| 31 | `c36_surplus` | 📊 گزارش‌ها → 📦 گزارش مواد مازاد → ماه جاری | مالک | Persian file names | b50_surplus | ❌ retake |
| 32 | `c37_remaining` | 📊 گزارش‌ها → ⚠️ پوشش کوتاه‌مدت موجودی سایت → ماه جاری | مالک | renamed report | b49_remaining_critical | ❌ retake |
| 33 | `c38_inbound` | 📊 گزارش‌ها → 📄 گزارش اقلام ورودی به انبار | مالک | «تاریخ آپلود فعلی» vs «تاریخ پایهٔ مقایسه» + «گزارش_اقلام_ورودی_1405-07-06» | b51_inbound | ❌ retake |
| 34 | `c39_comprehensive` | 📊 گزارش‌ها → 📊 گزارش جامع → ماه جاری → همه بخش‌ها | مالک | «گزارش_جامع_…» files | b53_general_report | ❌ retake |
| 35 | `c41_period_result` | 📊 گزارش‌ها → 📅 گزارش مصرف بازه‌ای → ۳ ماه اخیر → همه بخش‌ها | مالک | header with sources used + files | — | ❌ retake (new) |
| 36 | `c40_critical_menu` | 📊 گزارش‌ها → 🚨 اقلام بحرانی (نیاز ۳/۶ ماه) | مالک | basis text + 2 buttons + nav | b45_critical_menu, b46_critical_counts | ❌ retake |
| 37 | `c42_critical_mode` | … → 📄 تولید گزارش اقلام بحرانی | مالک | inline 🔧 با نوسازی / 🩹 بدون نوسازی | b47_critical_reno | ❌ retake |
| 38 | `c43_critical_result` | … → 🔧 با نوسازی | مالک | 3 files «لیست_اقلام_بحرانی_<date>_با_نوسازی_شرکت/پیمانکار.pdf» + xlsx | b48_critical_result | ❌ retake |
| 39 | `c44_critical_summary` | (same chat, the text summary) | مالک | units «عدد»/«کیلوگرم» (no «No»/«Kg») | b48b_critical_result_summary | ❌ retake |
| 40 | `c45_n_tundish` | 📊 گزارش‌ها → 🧮 نیاز مواد برای N تاندیش → بلوم → ۲ | مالک | section / count / mode steps (stack 2–3 panels) | — (replaces b52 area) | ❌ retake |
| 41 | `c46_n_tundish_result` | … → 🔧 با نوسازی | مالک | summary with «عدد» + «نیاز_مواد_۲_تاندیش_بلوم_با_نوسازی_<date>» | — | ❌ retake (new) |
| 42 | `c50_mg_menu` | 🎯 هدف اصلی | مالک | menu text + months; Shahrivar «تناژ ثبت نشده» | b54_mg_menu (+_a…_c) | ❌ retake |
| 43 | `c51_mg_inputs` | 🎯 هدف اصلی → 📥 ثبت ورودی ماه | مالک | status of months + 8 buttons | b55_mg_inputs | ❌ retake |
| 44 | `c52_mg_file_guide` | … → 📥 ثبت ورودی ماه → ❓ راهنمای تهیهٔ فایل | مالک | otsteel path for production photo | — | ❌ retake (new) |
| 45 | `c53_mg_bulk` | … → 📥 ثبت ورودی ماه → 📦 آپلود گروهی | مالک | then ✖️ توقف آپلود گروهی (send no file) | b63_mg_bulk | ❌ retake |
| 46 | `c54_mg_history_range` | 🎯 هدف اصلی → 📊 درخواست گزارش از سابقه → ۳ ماهه | مالک | summary 237,865 تن + «هدف_اصلی_گزارش_سابقه_3_ماه_اخیر_…»; optional 2nd panel: ۶ ماهه warning | b58_mg_range, b59_mg_partial | ❌ retake |
| 47 | `c56_mg_saved_months` | 🎯 هدف اصلی → 🗂 ماه‌های ذخیره‌شده | مالک/مدیر | «تناژ ثبت نشده» + 🗑 حذف یک ماه از سابقه (do NOT press it) | b60_mg_history_owner | ❌ retake |
| 48 | `c57_mg_scn1` | 🎯 هدف اصلی → 🎯 سناریو ۱: تناژ هدف → ۳ ماه | مالک | section step with nav; then ✖️ انصراف | b61_mg_scn_target, b61b_mg_scn_target_sections | ❌ retake |
| 49 | `c58_mg_scn2_result` | 🎯 هدف اصلی → 🔮 سناریو ۲: پیش‌بینی ماه‌های آینده → ۳ ماه | مالک | used vs skipped months + «هدف_اصلی_سناریو_۲_پیش‌بینی_۳_ماه_آینده_…» | b62_mg_scn_forecast | ❌ retake |
| 50 | `c60_settings_menu` | ⚙️ تنظیمات | مالک | 7 sub-sections + nav | b70_settings_menu (+_a,_b) | ❌ retake |
| 51 | `c61_users_menu` | ⚙️ تنظیمات → 👥 کاربران | مالک | 4 buttons + nav | b65_users_menu (+_a,_b) | ❌ retake |
| 52 | `c62_users_roles` | … → 👥 کاربران → ➕ اضافه کردن کاربر | مالک | 5 roles incl. 👷 مسئول شیفت | b66_users_role_owner | ❌ retake |
| 53 | `c63_invite_confirm` | … → ➕ اضافه کردن کاربر → 👷 مسئول شیفت | مالک | confirm screen only — press ⬅️ بازگشت, NOT ✅ تأیید ساخت لینک | b67_invite_confirm | ❌ retake |
| 54 | `c64_users_list` | … → 👥 کاربران → 📋 لیست کاربران | مالک | blur names and IDs | b69_users_list | ❌ retake |
| 55 | `c65_activity` | ⚙️ تنظیمات → 📋 فعالیت کاربران → ماه جاری | مالک | files + keyboard back on ⚙️ تنظیمات; blur names | — | ❌ retake (new) |
| 56 | `c66_tr_settings` | ⚙️ تنظیمات → 🧾 اقلام فرم گزارش تاندیش → ⚙️ اقلام گزارش اسلب | مالک | items list + ➕/✏️/🗑/🏭 + nav | b28_tr_settings, b29_tr_settings_section, b30_tr_settings_item_edit | ❌ retake |
| 57 | `c67_perm_roles` | ⚙️ تنظیمات → 🔐 دسترسی نقش‌ها | مالک | role buttons + summary | — | ❌ retake (new) |
| 58 | `c68_perm_checkboxes` | … → 🔐 دسترسی نقش‌ها → 🔐 تکنسین | مالک | inline ✅/⬜ list + ↺ بازگشت به پیش‌فرض — do NOT tap any item | — | ❌ retake (new) |
| 59 | `c69_reminders` | ⚙️ تنظیمات → 🔔 یادآورها (+ 👥 نقش‌های دریافت‌کننده) | مالک | 2 panels; blur recipient names; do NOT press ✅ فعال‌سازی / 📨 ارسال | b74_reminders_menu (+_a,_b), b74b_reminder_status, b75_reminders_roles (+_a,_b), b76_reminders_schedule | ❌ retake |
| 60 | `c70_stock_group` | ⚙️ تنظیمات → 📣 گروه گزارش موجودی | مالک | Persian status text with the positive group ID (blurred in the guide) — do NOT press 🗑 | b73_settings_stock_group | ❌ retake |
| 61 | `c71_appearance` | ⚙️ تنظیمات → 🎨 ظاهر (+ 📝 متن دعوت‌نامه کاربران) | مالک | 2 panels | b70/b71_settings_item (+_a,_b), b72_settings_letterhead | ❌ retake |

## B. Output files (PDF / Excel)

| id | source | how | replaces | status |
|---|---|---|---|---|
| `o01_pdf_sample_critical` | first page of «لیست_اقلام_بحرانی_<date>_با_نوسازی_شرکت.pdf» (from c43) | render page 1 to PNG (pdftoppm -r 110) | w14_report_pdf_sample | ❌ retake (old sample predates Vazirmatn/units/Persian names) |
| `o02_xlsx_sample` | any XLSX output opened in LibreOffice on the box (e.g. نیاز_مواد_۲_تاندیش_بلوم_با_نوسازی_<date>.xlsx) | screenshot of the sheet showing RTL, centred, borders, «عدد» units | — | ❌ retake (old sample predates Vazirmatn/units/Persian names) |

## C. Web panel (demo http://127.0.0.1:8010)

| # | id | URL path | login | notes | replaces old file(s) | status |
|---|---|---|---|---|---|---|
| 1 | `web_login` | `/login` | (logged out) | login form | w01_login | VALID — login.html, style.css unchanged; already composed in screens_final/web_login.png |
| 2 | `web_home_owner` | `/home` | demo_owner | new top bar (📊 گزارش‌ها, 🎯 هدف اصلی, 📤 ورود فایل‌ها / منبع اصلی, ⚙️, 🔐, 🔔, 📋) + cards | w02_home_owner | RETAKE — base.html nav + home cards changed |
| 3 | `web_home_tech` | `/home` | demo_tech (or demo_shift) | top bar with only موجودی سایت / گزارش تاندیش | w03_home_technician | RETAKE — nav now permission-driven |
| 4 | `web_stock` | `/stock?group=slab` | demo_owner | do not press ذخیره | w04_stock | RETAKE — description text + nav |
| 5 | `web_tundish_report` | `/tundish-report?section=slab` | demo_owner | form + recent; do not submit | w05_tundish_report | RETAKE — description text + nav |
| 6 | `web_tundish_settings` | `/tundish-report/settings?section=slab` | demo_owner | items + lines | w06_tundish_settings | RETAKE — nav bar |
| 7 | `web_materials_request` | `/materials/request` | demo_owner | do not submit | w07_materials_request | RETAKE — text + units + nav |
| 8 | `web_materials_return` | `/materials/return` | demo_owner | do not submit | w08_materials_return | RETAKE — text + nav |
| 9 | `web_reports` | `/reports` | demo_owner | all cards (2 panels: top/bottom) | w09_reports, w09_reports_a | RETAKE — merged/renamed cards (گزارش جامع، نیاز N تاندیش، مصرف بازه‌ای، پوشش کوتاه‌مدت) |
| 10 | `web_main_goal` | `/reports/main-goal (top: سابقه + الف) ثبت ورودی)` | demo_owner | saved months «تناژ ثبت نشده» | w10_main_goal, w10_main_goal_a | RETAKE — moved to own nav item, text changed |
| 11 | `web_main_goal_scenarios` | `/reports/main-goal (bottom: ب) گزارش + سناریو ۱/۲ + قدیمی)` | demo_owner | optionally run سناریو ۲ ۳ ماه to show used/skipped months | w10_main_goal_b, w10_main_goal_c | RETAKE — scenario 2 output/text changed |
| 12 | `web_main_source` | `/settings/main-source` | demo_owner | Persian column labels; do not upload/save | w11_main_source (+_a,_b) | RETAKE — labels + nav |
| 13 | `web_settings_web` | `/settings/web-login` | demo_owner | blur usernames list | w12_web_login_settings | RETAKE — text + nav |
| 14 | `web_permissions` | `/settings/permissions?role=technician` | demo_owner | checkboxes + overview; do NOT press 💾 ذخیره | — | NEW page |
| 15 | `web_reminders` | `/settings/reminders` | demo_owner | blur recipients; do NOT press 📨 ارسال | w13_reminders | RETAKE — nav bar |

## D. Status of every existing file in /workspace/guide_screens

| file | status | new id / reason |
|---|---|---|
| bot/b01_start_welcome_owner.png | ❌ retake | → `c01_start_owner` (menu/text changed after phases 1–4) |
| bot/b04_help.png | ❌ retake | → `c04_help` (menu/text changed after phases 1–4) |
| bot/b05_status.png | ❌ retake | → `c05_status` (menu/text changed after phases 1–4) |
| bot/b08_upload_menu.png | ❌ retake | → `c06_upload_menu` (menu/text changed after phases 1–4) |
| bot/b09_upload_wait_file.png | ❌ retake | → `c07_stock_update_prompt` (menu/text changed after phases 1–4) |
| bot/b11_main_source_menu.png | ❌ retake | → `c11_main_source_menu` (menu/text changed after phases 1–4) |
| bot/b12_main_source_add_record.png | ❌ retake | → `c14_add_record` (menu/text changed after phases 1–4) |
| bot/b13_main_source_edit_record.png | ❌ retake | → `c15_edit_record` (menu/text changed after phases 1–4) |
| bot/b14_add_category.png | ❌ retake | → `c16_add_category` (menu/text changed after phases 1–4) |
| bot/b15_list_categories.png | ❌ retake | → `c17_category_list` (menu/text changed after phases 1–4) |
| bot/b16_download_main_source.png | ❌ retake | → `c13_download_main_source` (menu/text changed after phases 1–4) |
| bot/b17_site_stock_menu.png | ❌ retake | → `c18_site_stock_menu` (menu/text changed after phases 1–4) |
| bot/b18_site_stock_inline.png | ❌ retake | → `c19_site_stock_entry` (menu/text changed after phases 1–4) |
| bot/b19_site_stock_prompt_qty.png | ❌ retake | → `c19_site_stock_entry` (menu/text changed after phases 1–4) |
| bot/b21_tr_menu_owner.png | ❌ retake | → `c21_tr_menu` (menu/text changed after phases 1–4) |
| bot/b23_tr_mode.png | ❌ retake | → `c22_tr_mode` (menu/text changed after phases 1–4) |
| bot/b24_tr_step.png | ❌ retake | → `c23_tr_step` (menu/text changed after phases 1–4) |
| bot/b25_tr_quick_text.png | ❌ retake | → `c24_tr_quick` (menu/text changed after phases 1–4) |
| bot/b27_tr_recent.png | 🗑 obsolete | not used in new guide (optional) |
| bot/b28_tr_settings.png | ❌ retake | → `c66_tr_settings` (menu/text changed after phases 1–4) |
| bot/b29_tr_settings_section.png | ❌ retake | → `c66_tr_settings` (menu/text changed after phases 1–4) |
| bot/b30_tr_settings_item_edit.png | ❌ retake | → `c66_tr_settings` (menu/text changed after phases 1–4) |
| bot/b31_catalog_menu.png | 🗑 obsolete | feature removed (تنظیمات اقلام سایت) |
| bot/b32_catalog_list.png | 🗑 obsolete | feature removed |
| bot/b33_catalog_assign.png | 🗑 obsolete | feature removed |
| bot/b34_mr_days.png | ❌ retake | → `c26_mr_days` (menu/text changed after phases 1–4) |
| bot/b35_mr_review.png | ❌ retake | → `c27_mr_review` (menu/text changed after phases 1–4) |
| bot/b36_mr_edit.png | ❌ retake | → `c29_mr_edit` (menu/text changed after phases 1–4) |
| bot/b37_mr_history.png | 🗑 obsolete | not used (optional) |
| bot/b38_wr_review.png | ❌ retake | → `c31_wr_review` (menu/text changed after phases 1–4) |
| bot/b39_analytics_menu.png | ❌ retake | → `c32_reports_menu` (menu/text changed after phases 1–4) |
| bot/b39_analytics_menu_a.png | ❌ retake | → `c32_reports_menu` (menu/text changed after phases 1–4) |
| bot/b39_analytics_menu_b.png | ❌ retake | → `c32_reports_menu` (menu/text changed after phases 1–4) |
| bot/b39_analytics_menu_c.png | ❌ retake | → `c32_reports_menu` (menu/text changed after phases 1–4) |
| bot/b39_analytics_menu_d.png | ❌ retake | → `c32_reports_menu` (menu/text changed after phases 1–4) |
| bot/b40_month_range.png | ❌ retake | → `c33_range_picker` (menu/text changed after phases 1–4) |
| bot/b41_month_range_period.png | ❌ retake | → `c33_range_picker` (menu/text changed after phases 1–4) |
| bot/b43_year_picker.png | ❌ retake | → `c33_range_picker` (menu/text changed after phases 1–4) |
| bot/b43b_month_picker.png | ❌ retake | → `c33_range_picker` (menu/text changed after phases 1–4) |
| bot/b44_report_result.png | ❌ retake | → `c35_daily_result` (menu/text changed after phases 1–4) |
| bot/b45_critical_menu.png | ❌ retake | → `c40_critical_menu` (menu/text changed after phases 1–4) |
| bot/b46_critical_counts.png | ❌ retake | → `c40_critical_menu` (menu/text changed after phases 1–4) |
| bot/b47_critical_reno.png | ❌ retake | → `c42_critical_mode` (menu/text changed after phases 1–4) |
| bot/b48_critical_result.png | ❌ retake | → `c43_critical_result` (menu/text changed after phases 1–4) |
| bot/b48b_critical_result_summary.png | ❌ retake | → `c44_critical_summary` (menu/text changed after phases 1–4) |
| bot/b49_remaining_critical.png | ❌ retake | → `c37_remaining` (menu/text changed after phases 1–4) |
| bot/b50_surplus.png | ❌ retake | → `c36_surplus` (menu/text changed after phases 1–4) |
| bot/b51_inbound.png | ❌ retake | → `c38_inbound` (menu/text changed after phases 1–4) |
| bot/b52_tundish_filter.png | 🗑 obsolete | feature removed (فیلتر نوع تاندیش) |
| bot/b53_general_report.png | ❌ retake | → `c39_comprehensive` (menu/text changed after phases 1–4) |
| bot/b54_mg_menu.png | ❌ retake | → `c50_mg_menu` (menu/text changed after phases 1–4) |
| bot/b54_mg_menu_a.png | ❌ retake | → `c50_mg_menu` (menu/text changed after phases 1–4) |
| bot/b54_mg_menu_b.png | ❌ retake | → `c50_mg_menu` (menu/text changed after phases 1–4) |
| bot/b54_mg_menu_c.png | ❌ retake | → `c50_mg_menu` (menu/text changed after phases 1–4) |
| bot/b55_mg_inputs.png | ❌ retake | → `c51_mg_inputs` (menu/text changed after phases 1–4) |
| bot/b58_mg_range.png | ❌ retake | → `c54_mg_history_range` (menu/text changed after phases 1–4) |
| bot/b59_mg_partial.png | ❌ retake | → `c54_mg_history_range` (menu/text changed after phases 1–4) |
| bot/b60_mg_history_owner.png | ❌ retake | → `c56_mg_saved_months` (menu/text changed after phases 1–4) |
| bot/b61_mg_scn_target.png | ❌ retake | → `c57_mg_scn1` (menu/text changed after phases 1–4) |
| bot/b61b_mg_scn_target_sections.png | ❌ retake | → `c57_mg_scn1` (menu/text changed after phases 1–4) |
| bot/b62_mg_scn_forecast.png | ❌ retake | → `c58_mg_scn2_result` (menu/text changed after phases 1–4) |
| bot/b63_mg_bulk.png | ❌ retake | → `c53_mg_bulk` (menu/text changed after phases 1–4) |
| bot/b64_mg_recent.png | 🗑 obsolete | not used (optional) |
| bot/b65_users_menu.png | ❌ retake | → `c61_users_menu` (menu/text changed after phases 1–4) |
| bot/b65_users_menu_a.png | ❌ retake | → `c61_users_menu` (menu/text changed after phases 1–4) |
| bot/b65_users_menu_b.png | ❌ retake | → `c61_users_menu` (menu/text changed after phases 1–4) |
| bot/b66_users_role_owner.png | ❌ retake | → `c62_users_roles` (menu/text changed after phases 1–4) |
| bot/b67_invite_confirm.png | ❌ retake | → `c63_invite_confirm` (menu/text changed after phases 1–4) |
| bot/b69_users_list.png | ❌ retake | → `c64_users_list` (menu/text changed after phases 1–4) |
| bot/b70_settings_menu.png | ❌ retake | → `c60_settings_menu` (menu/text changed after phases 1–4) |
| bot/b70_settings_menu_a.png | ❌ retake | → `c60_settings_menu` (menu/text changed after phases 1–4) |
| bot/b70_settings_menu_b.png | ❌ retake | → `c60_settings_menu` (menu/text changed after phases 1–4) |
| bot/b71_settings_item.png | ❌ retake | → `c71_appearance` (menu/text changed after phases 1–4) |
| bot/b71_settings_item_a.png | ❌ retake | → `c71_appearance` (menu/text changed after phases 1–4) |
| bot/b71_settings_item_b.png | ❌ retake | → `c71_appearance` (menu/text changed after phases 1–4) |
| bot/b72_settings_letterhead.png | ❌ retake | → `c71_appearance` (menu/text changed after phases 1–4) |
| bot/b73_settings_stock_group.png | ❌ retake | → `c70_stock_group` (menu/text changed after phases 1–4) |
| bot/b74_reminders_menu.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b74_reminders_menu_a.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b74_reminders_menu_b.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b74b_reminder_status.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b75_reminders_roles.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b75_reminders_roles_a.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b75_reminders_roles_b.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| bot/b76_reminders_schedule.png | ❌ retake | → `c69_reminders` (menu/text changed after phases 1–4) |
| web/w01_login.png | ✅ valid | `web_login` (login page unchanged) |
| web/w02_home_owner.png | ❌ retake | → `web_home_owner` (menu/text changed after phases 1–4) |
| web/w03_home_technician.png | ❌ retake | → `web_home_tech` (menu/text changed after phases 1–4) |
| web/w04_stock.png | ❌ retake | → `web_stock` (menu/text changed after phases 1–4) |
| web/w05_tundish_report.png | ❌ retake | → `web_tundish_report` (menu/text changed after phases 1–4) |
| web/w06_tundish_settings.png | ❌ retake | → `web_tundish_settings` (menu/text changed after phases 1–4) |
| web/w07_materials_request.png | ❌ retake | → `web_materials_request` (menu/text changed after phases 1–4) |
| web/w08_materials_return.png | ❌ retake | → `web_materials_return` (menu/text changed after phases 1–4) |
| web/w09_reports.png | ❌ retake | → `web_reports` (menu/text changed after phases 1–4) |
| web/w09_reports_a.png | ❌ retake | → `web_reports` (menu/text changed after phases 1–4) |
| web/w10_main_goal.png | ❌ retake | → `web_main_goal` (menu/text changed after phases 1–4) |
| web/w10_main_goal_a.png | ❌ retake | → `web_main_goal` (menu/text changed after phases 1–4) |
| web/w10_main_goal_b.png | ❌ retake | → `web_main_goal_scenarios` (menu/text changed after phases 1–4) |
| web/w10_main_goal_c.png | ❌ retake | → `web_main_goal_scenarios` (menu/text changed after phases 1–4) |
| web/w11_main_source.png | ❌ retake | → `web_main_source` (menu/text changed after phases 1–4) |
| web/w11_main_source_a.png | ❌ retake | → `web_main_source` (menu/text changed after phases 1–4) |
| web/w11_main_source_b.png | ❌ retake | → `web_main_source` (menu/text changed after phases 1–4) |
| web/w12_web_login_settings.png | ❌ retake | → `web_settings_web` (menu/text changed after phases 1–4) |
| web/w13_reminders.png | ❌ retake | → `web_reminders` (menu/text changed after phases 1–4) |
| web/w14_report_pdf_sample.png | ❌ retake | → `o01_pdf_sample_critical` (menu/text changed after phases 1–4) |

## E. Pending content (not screenshots)

- «❓ راهنمای تهیهٔ فایل»: company-system paths for موجودی انبار / مصرف ماهیانه / اکسل‌های تاندیش are still «به‌زودی» — waiting for the user. Update `services/file_guides.py` and re-take `c08_file_guide` afterwards.
- c09 (stock-update result) can only be captured when a real warehouse upload happens; skip it (figure is omitted automatically) if not available.

## F. Final status (1405/07/19 build)

- All captures taken 1405/07/19 (Bale account role: مدیر; web: demo instance on port 8010). Processed by `figures2.py`: Bale crops = chat pane only, web crops = page content without browser chrome; names, Bale IDs, usernames, group ID and avatars pixelated (OCR-assisted + manual boxes). Names for OCR auto-blur are read from a local, git-ignored `private_names.txt`.
- 79/79 referenced figures present in `screens_final/` (incl. `o01` from page 1 of the critical PDF at 110 dpi, `o02` from the N-tundish xlsx via LibreOffice → PDF → PNG).
- Not captured, described in text instead: `c02_start_officer`, `c03_start_technician` (menus drawn from code as keyboard mock-ups), `c09_stock_update_result` (message lines documented in a table; no real warehouse upload happened).
- Still pending from the user: company-system paths for «❓ راهنمای تهیهٔ فایل» (guide states they are not yet received).
