# Real plant Excel samples

Copied from production-style attachments for loader regression tests.

## `inventory_sample.xlsx` (منبع اصلی / موجودی انبار ورودی)

- Prefer sheet **«ریز اطلاعات»** (workbook also has Sheet1 / کل موجودی / Sheet3).
- Columns (Arabic yeh `ي` U+064A often appears instead of Persian `ی` U+06CC):
  - `کد دسته بندي` → `category_code`
  - `کد و شرح کالا` → `item_code_desc` (id parsed before first ` - ` / `–` / `—`)
  - `موجودي` → `quantity`
- ~357 detail rows, **122** distinct category codes.
- Smoke: `allowlist=['1203','1206']` keeps 8 rows (3 + 5); ids filled; `priority=1`.

## `monthly_consumption_sample.xlsx` (مصرف ماهیانه)

- Also has sheet **«ریز اطلاعات»** with `کد دسته بندي`, `کد کالا`, `مقدار`, …
- **29** category codes, all overlapping the inventory sample.
Default bot allowlist (`DEFAULT_CATEGORY_CODES` in `config.py`) is these **29** overlapping codes; more can still be added from the bot menu.
- Current bot monthly schema is still the older tundish/RBAC layout; do not treat this file as a drop-in for `monthly_consumption` yet. Warehouse inventory path is the one fixed against these samples.

Loader rules live in `excel/processor.py` (`_pick_excel_sheet`, `_normalize_fa_header`).
