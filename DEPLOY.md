# استقرار بازو روی سرور

ربات + پنل وب روی یک سرور لینوکس همیشه روشن اجرا می‌شوند. تست‌شده برای **Ubuntu 22.04 / 24.04** و **Debian 12** (Python 3.10+). دسترسی **root یا sudo** لازم است (نصب بسته‌ها و سرویس systemd).

## نصب یک‌خطی (پیشنهادی)

در یک ترمینال SSH واقعی:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh)
```

شکل `curl … | bash` هم کار می‌کند (ورودی ویزارد از `/dev/tty` خوانده می‌شود):

```bash
curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh | bash
```

یا دستی:

```bash
git clone https://github.com/silent4time/special-materials-robot-project.git
cd special-materials-robot-project
bash install.sh
```

ریپو در `./special-materials-robot-project` (یا `INSTALL_DIR=/opt/nasoz`) کلون می‌شود.

## نصب‌کننده چه می‌کند

1. بسته‌های سیستم: `python3 python3-venv python3-pip git curl tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng sqlite3 fontconfig` (فونت Vazirmatn داخل `fonts/` است)
2. **ویزارد فارسی** (قبل از نصب‌های طولانی) و نوشتن `.env` با `chmod 600`
3. `.venv` + `pip install -r requirements.txt` + تست import ماژول‌ها + ساخت/مهاجرت دیتابیس و پوشه‌های `data/ uploads/ reports/`
4. دو سرویس systemd: **`nasoz-bot`** (ربات) و **`nasoz-web`** (پنل وب) با `Restart=always`، فعال در بوت (در root/sudo پیش‌فرض بله). بدون systemd با `nohup` اجرا می‌شود.
5. تست سلامت: `getMe`، زنده بودن ربات، پاسخ `200` از `/login` پنل وب؛ و خلاصهٔ فارسی (آدرس وب، دستورات سرویس)

### سؤال‌های ویزارد

| # | سؤال | پیش‌فرض |
|---|------|---------|
| ۱ | توکن بازو از `@botfather` (مخفی تایپ می‌شود؛ با `getMe` روی `tapi.bale.ai` بررسی می‌شود) | — (الزامی) |
| ۲ | نام کاربری ربات (بدون @) | از `getMe` |
| ۳ | شناسه عددی مالک در بله | خالی = اولین `/start` مالک می‌شود |
| ۴ | پورت پنل وب | `8000` |
| ۵ | آدرس گوش‌دادن وب | `0.0.0.0` (پشت nginx: `127.0.0.1`) |
| ۶ | نام کاربری ورود وب | `admin` |
| ۷ | رمز ورود وب (مخفی) | ساخت خودکار |
| ۸ | `CRITICAL_DAYS` | `3` |
| ۹ | نصب سرویس systemd | بله (اگر root/sudo و systemd باشد) |
| ۱۰ | دستیار هوشمند Ollama | خیر |

`WEB_SECRET_KEY` خودکار ساخته می‌شود. اجرای دوباره، مقادیر فعلی را پیش‌فرض می‌گذارد و `.env` و `data/` را دست نمی‌زند.

### نوار پیشرفت و لاگ نصب
هر مرحله با سرخطی مثل `[۴/۱۱] ۳۶٪ ███░░░░░░░ محیط پایتون و وابستگی‌ها...` نمایش داده می‌شود. خروجی کامل apt/pip در `data/logs/nasoz_install_<تاریخ>.log` است (پیش از مشخص شدن پوشه، موقتاً در `/tmp`). در خطا، مرحلهٔ ناموفق، ۱۵ خط آخر لاگ و مسیر لاگ چاپ می‌شود و اجرای دوباره امن است. بدون ترمینال یا با `NO_COLOR=1` خروجی ساده و بی‌رنگ است.

## بعد از نصب

1. در بله به ربات `/start` بزنید — اولین نفر **مالک** می‌شود (یا همان شناسهٔ سؤال ۳).
2. فعال‌کردن ورود پنل وب برای مالک (اگر شناسه مالک را در ویزارد نداده‌اید):
   ```bash
   cd special-materials-robot-project && bash install.sh --web-login
   ```
   نام کاربری/رمز: `grep WEB_ADMIN_ .env`
3. پنل: `http://IP-سرور:8000/login` — اگر فایروال فعال است: `sudo ufw allow 8000/tcp` (و در پنل ابری، پورت را باز کنید).

## دستورات

| دستور | کار |
|------|-----|
| `bash install.sh` | **نصب کامل با یک دستور**: (پرسش ارتقای سیستم — پیش‌فرض بله) + بسته‌ها + venv + ویزارد `.env` + DB + سرویس‌ها + تست سلامت |
| `bash install.sh --update` | به‌روزرسانی برنامه: پشتیبان `data/backups/bot_pre_update_*.db` + `git pull --ff-only` + `pip install -r requirements.txt` + migrate + restart + تست سلامت؛ `.env` و `data/` حفظ می‌شود |
| `bash install.sh --system-upgrade` | ارتقای سیستم‌عامل: `DEBIAN_FRONTEND=noninteractive apt-get update -y && apt-get upgrade -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold` (پیکربندی‌های موجود حفظ) + restart سرویس‌ها؛ اگر `/var/run/reboot-required` باشد هشدار ریبوت |
| `bash install.sh --full-update` | `--system-upgrade` و سپس `--update` |
| `bash install.sh --restore FILE.zip` | بازگردانی بک‌آپ: بررسی (رد فایل نامعتبر بدون هیچ تغییر) ← توقف سرویس‌ها ← بک‌آپ «پیش از بازگردانی» در `data/backups/` ← بازگردانی DB + `uploads/` + `data/bot_assets/` ← migrate ← استارت ← تست سلامت (`--dry-run` فقط مراحل را چاپ می‌کند) |
| `bash install.sh --no-system-upgrade` | در نصب تازه ارتقای سیستم انجام نشود (در حالت غیرتعاملی پیش‌فرض: انجام می‌شود) |
| `bash install.sh --repair` | نصب دوبارهٔ بسته‌های گم‌شده (tesseract، venv …) + restart |
| `bash install.sh --reconfigure` | ویزارد دوباره |
| `bash install.sh --web-login` | ساخت/بازنشانی ورود وب مالک از روی `.env` |
| `bash install.sh --start` | استارت دوباره (مثلاً بعد از ریبوت در حالت nohup) |
| `bash install.sh --dry-run` | فقط نمایش بسته‌ها، دستورهای apt/git/پشتیبان و فایل‌های systemd، بدون تغییر (با هر پرچم دیگر ترکیب می‌شود) |
| `bash install.sh --with-assistant` | نصب دستیار هوشمند (Ollama) |
| `sudo systemctl status nasoz-bot nasoz-web` | وضعیت |
| `sudo systemctl restart nasoz-bot nasoz-web` | ری‌استارت |
| `journalctl -u nasoz-bot -f` / `journalctl -u nasoz-web -f` | لاگ زنده |

هر دو سرویس قبل از استارت `scripts/preflight.sh` را (به‌عنوان root) اجرا می‌کنند: اگر tesseract یا venv پاک شده باشد خودکار دوباره نصب می‌شود.

## نصب خودکار (بدون پرسش)

```bash
BALE_BOT_TOKEN='123456789:ABC...' WEB_PORT=8000 INSTALL_SYSTEMD=y NONINTERACTIVE=1 \
  bash <(curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh)
```

متغیرها: `BALE_BOT_TOKEN BOT_USERNAME ADMIN_BALE_USER_ID WEB_PORT WEB_HOST WEB_ADMIN_USERNAME WEB_ADMIN_PASSWORD CRITICAL_DAYS INSTALL_SYSTEMD=y|n INSTALL_DIR SERVICE_USER SKIP_GETME=1 SKIP_APT=1`.

## عیب‌یابی

- **نصب چیزی نپرسید / ربات بالا نیامد** (نسخه‌های قبل از ۱۴۰۵/۰۷/۲۵): در `curl | bash` ورودی پایپ بود و ویزارد بی‌صدا رد می‌شد. نسخهٔ جدید از `/dev/tty` می‌پرسد و اگر هیچ ترمینالی نباشد با پیام واضح متوقف می‌شود. روی سرور قبلی کافی است:
  ```bash
  cd special-materials-robot-project && git pull && bash install.sh
  ```
- «ترمینال تعاملی پیدا نشد»: در SSH واقعی اجرا کنید (نه از cron/پنل وب‌هاست)، یا از متغیرهای نصب خودکار استفاده کنید.
- getMe ناموفق: توکن را از `@botfather` دوباره کپی کنید؛ دسترسی سرور به `https://tapi.bale.ai` را با `curl -I https://tapi.bale.ai` چک کنید.
- پنل وب باز نمی‌شود: `journalctl -u nasoz-web -n 50`، `tail data/web.log`، پورت در ufw/فایروال ابری باز باشد، `WEB_HOST=0.0.0.0`.
- OCR عکس تولید کار نمی‌کند: `tesseract --list-langs` باید `fas` داشته باشد؛ `bash install.sh --repair`.
- ربات پاسخ نمی‌دهد: `journalctl -u nasoz-bot -n 50` و `tail data/bot.log`؛ **فقط یک نمونه** با یک توکن (نسخهٔ قدیمی روی ماشین دیگر را خاموش کنید).

## نکات مهم

- **فقط یک نمونه** از بازو با همان توکن باید long-poll کند.
- `.env` و `data/bot.db` را بکاپ بگیرید؛ توکن را جایی منتشر نکنید.
- ربات فقط اینترنت خروجی به `tapi.bale.ai` لازم دارد؛ برای پنل وب پورت ورودی (پیش‌فرض 8000) باید باز باشد.

## نصب دستی (بدون install.sh)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
git clone https://github.com/silent4time/special-materials-robot-project.git
cd special-materials-robot-project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env   # فقط BALE_BOT_TOKEN الزامی است
python main.py                      # اولین /start → مالک
```

## دستیار هوشمند (Ollama محلی)

دستیار فقط از **Ollama (یا سازگار) روی همین سرور** استفاده می‌کند؛ هیچ LLM ابری فراخوانی نمی‌شود. اگر Ollama خاموش باشد، ربات کرش نمی‌کند و پیام فارسی «دستیار محلی در دسترس نیست…» نشان می‌دهد.

**پیش‌نیاز پیشنهادی:** حدود ۴ گیگابایت RAM برای `qwen2.5:3b` (با ۸ گیگابایت راحت‌تر)؛ CPU چند‌هسته‌ای کافی است؛ GPU اختیاری.

راهنمای کامل PDF (فارسی RTL): [`docs/راهنمای_نصب_دستیار_هوشمند.pdf`](docs/راهنمای_نصب_دستیار_هوشمند.pdf) — منبع Markdown: [`docs/دستیار-هوشمند.md`](docs/دستیار-هوشمند.md).

### نصب همراه `install.sh`

```bash
# تعاملی: پرسش «دستیار هوشمند را هم نصب کنم؟ (Y/n)» — پیش‌فرض بله
bash install.sh

# غیرتعاملی
bash install.sh --with-assistant
bash install.sh --no-assistant
```

با `--with-assistant` (یا پاسخ Y): نصب Ollama در صورت نبود، `ollama pull` مدل، و نوشتن `OLLAMA_*` در `.env`.

### نصب دستی Ollama و مدل پیش‌فرض

```bash
# نصب (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# سرویس معمولاً روی http://127.0.0.1:11434 گوش می‌دهد
ollama serve   # اگر به‌صورت سرویس بالا نباشد

# مدل کوچک مناسب CPU (پیش‌فرض ربات):
ollama pull qwen2.5:3b
```

### متغیرهای `.env`

```bash
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_TIMEOUT=60
```

مدل پیش‌فرض در کد: `qwen2.5:3b`. اگر RAM کم است، `qwen2.5:1.5b` را بکشید و `OLLAMA_MODEL=qwen2.5:1.5b` بگذارید. سایر گزینه‌ها: `llama3.2:3b`, `phi3:mini`.

### تأیید و تست

```bash
ollama list
curl -s http://127.0.0.1:11434/api/tags
```

سپس ربات را روشن کنید (`python main.py` یا `bash install.sh --start`).

### استفاده در بله

منوی «گزارش‌ها / تحلیل تاندیش» → «🤖 دستیار هوشمند» (نقش **تکنسین** دسترسی ندارد). دامنه فقط گزارش‌ها است.

### رفع اشکال و به‌روزرسانی مدل

| مشکل | کار |
|------|-----|
| پیام «دستیار محلی در دسترس نیست…» | `ollama serve` / سرویس ollama؛ پورت `11434`؛ `OLLAMA_BASE_URL` |
| مدل نیست | `ollama pull` مطابق `OLLAMA_MODEL` |
| کندی | مدل کوچک‌تر یا افزایش `OLLAMA_TIMEOUT` |

به‌روزرسانی مدل: `ollama pull …` سپس در صورت نیاز تغییر `OLLAMA_MODEL` و restart ربات (`bash install.sh --update` یا `systemctl restart nasoz-bot`).

## داشبورد وب (فرآیند جدا)

پنل وب روی **همان ماشین و همان `DATABASE_PATH`** اجرا می‌شود ولی **فرآیند جدا** از `main.py` است (`web_main.py` / `python -m web`). توکن بله لازم نیست.

**هماهنگی ربات و وب:** منطق کسب‌وکار در ماژول‌های مشترک است (`db`, `auth.rbac`, `analytics`, `pdf`, `excel`, `bot.activity`). نقش‌ها همان `users.role` ربات‌اند. هنگام تغییر کد مشترک، **ربات و وب را با هم دیپلوی** کنید.

```bash
# وابستگی‌ها
.venv/bin/pip install -r requirements.txt

# اولین ورود وب برای مالک موجود (اولین /start در بله)
.venv/bin/python scripts/seed_web_admin.py
# رمز در data/web_admin_credentials.txt (gitignore، mode 600)

# اجرا
.venv/bin/python web_main.py
# یا systemd:
# sed ... scripts/nasoz-web.service.in → /etc/systemd/system/nasoz-web.service
```

در `.env`: `WEB_SECRET_KEY`, `WEB_HOST=0.0.0.0`, `WEB_PORT=8000`.

`PRAGMA journal_mode=WAL` و `busy_timeout` در `Database.connect` برای هم‌زیستی ربات+وب فعال است. اتصال‌های جدید وب WAL را می‌گیرند؛ ربات در **ری‌استارت بعدی** همان pragma را روی اتصال‌های تازه اعمال می‌کند (نیازی به ری‌استارت فوری نیست مگر قفل‌های طولانی ببینید).

## منبع اصلی (canonical inventory)

Cleaned `product_inventory` extracts («منبع اصلی») are the **canonical** warehouse
master for bot and web reports. Prefer the latest `extracted_datasets` clean path
via shared `analytics/frames.py`. Site daily stock is separate (on-site remaining /
critical only). Do not point plant reports at stale session slots when a newer
extract exists. Restart Bale + web only after smoke tests pass.


## 💾 بک‌آپ

- فایل‌ها در `data/backups/nasoz_backup_<تاریخ شمسی>_<ساعت>[_manual|_pre-restore].zip` (کنار پشتیبان‌های قدیمی `*.db`).
- خودکار هر شب ۰۲:۰۰ تهران (داخل ربات) + ارسال برای مالک در بله؛ نگهداری ۷ روزانه + ۴ هفتگی؛ دستی/پیش از بازگردانی تا ۳۰ روز محفوظ.
- `.env` کامل هرگز داخل بک‌آپ نیست (فقط کلیدهای غیرمحرمانه در `config/env_public.txt`)؛ پس از بازگردانی روی سرور جدید، `.env` را با `bash install.sh --reconfigure` بسازید.
- برای نگهداری بیرون از سرور، فایل‌ها را از `/settings/backups` دانلود کنید.
