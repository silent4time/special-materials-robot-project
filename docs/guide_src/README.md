# منابع راهنمای کاربری (`docs/nasoz_guide.pdf`)

- `content.py` — متن کامل راهنما (فارسی)؛ `build.py` — قالب، CSS (Vazirmatn، راست‌به‌چپ، A4، فهرست و بوکمارک قابل کلیک) و ساخت PDF با WeasyPrint.
- `screens_final/` — تصاویر نهایی **برش‌خورده و محوشده** (نام‌ها، شناسه‌ها، نام کاربری، شناسهٔ گروه و آواتارها محو شده‌اند). تصاویر خام عمداً در مخزن نیستند.
- `figures2.py` — ساخت `screens_final/` از تصاویر خام (برش، محوسازی). نام‌های محوشونده از فایل محلی `private_names.txt` خوانده می‌شود که در مخزن نیست.
- `screenshot_list.md` — فهرست تصاویر، مسیر دکمه/نشانی هر تصویر و وضعیت آن.

ساخت دوباره (نیاز: `pip install weasyprint pillow`):

```
GUIDE_OUT_PDF=docs/nasoz_guide.pdf python docs/guide_src/build.py
```
