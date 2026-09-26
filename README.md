# بازوی گزارش مواد (بله)

ربات پیام‌رسان **بله** برای دریافت **سه فایل Excel مشخص**، اعمال کنترل دسترسی نقش‌محور (RBAC)، و تولید **یک گزارش PDF ترکیبی**.

## رویکرد فنی API

از **فراخوانی مستقیم HTTPS** به API بله استفاده شده است:

```text
https://tapi.bale.ai/bot<TOKEN>/<METHOD>
```

کلاینت در `bot/bale_api.py` با کتابخانه `httpx` پیاده‌سازی شده (long-polling با `getUpdates`).  
دلیل انتخاب: نصب پایدار بدون نیاز به کامپایل `aiohttp` (وابستگی `python-bale-bot`) روی برخی سرورها. رفتار API مشابه تلگرام است.

## پیش‌نیاز

- Python 3.11+
- توکن بازو از بله
- شناسه عددی کاربر ادمین در بله

## ساخت بازو در بله و تنظیم توکن

1. در اپ بله به `@botfather` پیام دهید و بازوی جدید بسازید.
2. توکن را کپی کنید.
3. یک بار به بازوی خودتان `/start` بزنید تا چت فعال شود.
4. شناسه عددی خودتان را پیدا کنید (از لاگ بازو پس از اولین پیام، یا ابزارهای رایج شناسه کاربر بله/تلگرام).

```bash
cd bale-materials-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# سپس BALE_BOT_TOKEN و ADMIN_BALE_USER_ID را ویرایش کنید
```

### مقداردهی اولیه مدیر

با اجرای برنامه، اگر `ADMIN_BALE_USER_ID` تنظیم شده باشد، کاربر مدیر ساخته/به‌روز می‌شود. یا:

```bash
python scripts/seed_admin.py
# یا
python scripts/seed_admin.py --user-id 123456789 --name "مدیر سیستم"
```

### اجرا

```bash
python main.py
```

## جریان کار (درخواست‌محور)

```text
منوی اصلی
   │
   ├─► انتخاب «مقدار مصرفی هر تانک» ──► ارسال فایل .xlsx ──► ذخیره اسلات
   ├─► انتخاب «موجودی محصولات»     ──► ارسال فایل .xlsx ──► ذخیره اسلات
   ├─► انتخاب «مصرف ماهانه مواد»   ──► ارسال فایل .xlsx ──► ذخیره اسلات
   ├─► «وضعیت فایل‌ها»
   └─► «تولید گزارش PDF»  (فقط وقتی هر سه اسلات پر شده)
              │
              ▼
     فیلتر RBAC روی هر سه جدول + PDF ترکیبی
```

کاربر **نمی‌تواند** آزادانه فایل بفرستد؛ اول باید نوع را از منو/دکمه انتخاب کند، سپس Document را پیوست کند. پس از ذخیره، اسلات از حالت «در انتظار» خارج می‌شود.

## نقش‌ها (RBAC)

| نقش | کلید انگلیسی | دسترسی در گزارش |
|-----|--------------|-----------------|
| مالک | `owner` | همه ردیف‌ها + مدیریت کاربران؛ تنها کسی که می‌تواند نقش مالک بدهد. ادمین اولیه از `.env` به‌عنوان مالک ساخته می‌شود |
| مدیر | `manager` | همه ردیف‌ها + دستورات مدیریت کاربران (به‌جز اعطای نقش مالک) |
| کاردان مسئول | `responsible_officer` | فقط ردیف‌هایی که ستون `domain` برابر `scope` کاربر است |
| تکنسین | `technician` | فقط ردیف‌هایی که `assignee_id` یا `assignee_name` با کاربر یکی است |

### دستورات مدیر

```text
/users
/adduser <bale_id> <owner|manager|responsible_officer|technician> [scope] [name...]
/setrole <bale_id> <role>
/setscope <bale_id> <scope>
/reset
```

نمونه‌ها:

```text
/adduser 1001 technician خط-A علی رضایی
/adduser 1002 responsible_officer خط-B مریم احمدی
/setrole 1001 technician
/setscope 1002 خط-B
```

## سه فایل Excel و ستون‌ها

قالب‌های نمونه در پوشه `samples/`:

| فایل نمونه | نوع | عنوان فارسی |
|------------|-----|-------------|
| `01_tank_consumption.xlsx` | `tank_consumption` | مقدار مصرفی هر تانک |
| `02_product_inventory.xlsx` | `product_inventory` | موجودی محصولات |
| `03_monthly_consumption.xlsx` | `monthly_consumption` | مصرف ماهانه مواد |

ساخت مجدد نمونه‌ها:

```bash
python scripts/make_samples.py
```

### ۱) مقدار مصرفی هر تانک

ستون‌های توصیه‌شده:

`domain`, `assignee_id`, `assignee_name`, `tank_id`, `material_name`, `quantity`, `unit`, `date`, `notes`

### ۲) موجودی محصولات

`domain`, `assignee_id`, `assignee_name`, `product_name`, `quantity`, `unit`, `location`, `date`, `notes`

### ۳) مصرف ماهانه مواد

`domain`, `assignee_id`, `assignee_name`, `material_name`, `month`, `quantity`, `unit`, `status`, `notes`

**حداقلی برای RBAC:** وجود `domain` و حداقل یکی از `assignee_id` / `assignee_name` (ترجیحاً هر دو).  
نام‌های فارسی معادل (مثل «حوزه»، «نام»، «مقدار») هم تا حدی پشتیبانی می‌شوند.

## خروجی PDF

- عنوان: **گزارش مواد / خلاصه داده‌های آپلود‌شده**
- بخش خلاصه (تعداد ردیف کل و پس از فیلتر نقش)
- سه بخش جداگانه برای هر منبع
- فونت بسته‌شده: `fonts/DejaVuSans.ttf` (+ Bold) با `arabic_reshaper` و `python-bidi` برای نمایش بهتر فارسی

## ساختار پروژه

```text
bale-materials-bot/
├── main.py                 # نقطه ورود (polling)
├── config.py
├── requirements.txt
├── .env.example
├── README.md
├── bot/
│   ├── bale_api.py         # کلاینت HTTPS بله
│   ├── handlers.py         # دستورات و جریان آپلود
│   └── keyboards.py        # دکمه‌های فارسی
├── auth/rbac.py
├── excel/processor.py
├── pdf/generator.py
├── db/models.py            # SQLite
├── fonts/                  # DejaVuSans
├── samples/                # سه قالب Excel
└── scripts/
    ├── seed_admin.py
    ├── make_samples.py
    └── smoke_test.py
```

## تست آفلاین

```bash
python scripts/smoke_test.py
```

بدون نیاز به توکن بله، فیلتر نقش و ساخت PDF را بررسی می‌کند.

## محدودیت‌ها

- فونت DejaVu بسیاری از حروف فارسی را پوشش می‌دهد؛ برای تایپوگرافی حرفه‌ای‌تر می‌توانید Noto Naskh/Sans Arabic را جایگزین کنید.
- دانلود فایل از بله به `getFile` و مسیر `tapi.bale.ai/file/bot...` وابسته است؛ در صورت تغییر زیرساخت بله ممکن است نیاز به تنظیم باشد.
- حداکثر حدود ۲۰۰ ردیف از هر جدول در PDF نمایش داده می‌شود تا حجم فایل معقول بماند.
- long-polling برای استقرار ساده است؛ برای مقیاس بالا webhook پیشنهاد می‌شود.
