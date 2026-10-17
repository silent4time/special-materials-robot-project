# استقرار بازو روی سرور

ربات + پنل وب روی یک سرور لینوکس همیشه روشن اجرا می‌شوند. تست‌شده برای **Ubuntu 22.04 / 24.04** و **Debian 12** (Python 3.10+). دسترسی **root یا sudo** لازم است (نصب بسته‌ها و سرویس systemd).

## دو روش نصب

| روش | چه وقت | دستور اصلی |
|-----|--------|------------|
| **الف) آنلاین (یک دستور از GitHub)** | سرور به GitHub و PyPI دسترسی دارد | `bash <(curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh)` |
| **ب) آفلاین (بسته tar.gz)** | سرور به GitHub یا PyPI نمی رسد (مثلا داخل ایران) | `bash install_offline.sh` |

هر دو روش همان ویزارد فارسی، نوار پیشرفت، سرویس های systemd و تست سلامت را اجرا می کنند. دسترسی **root یا sudo** لازم است. همه دستورها را در یک ترمینال SSH واقعی (PuTTY، CMD/PowerShell با `ssh`، ...) اجرا کنید.

> **فقط یک نمونه از ربات با یک توکن:** اگر ربات روی سرور یا کامپیوتر دیگری با همین توکن روشن است، اول آن را خاموش کنید؛ دو نمونه هم زمان باعث از دست رفتن پیام ها می شود.

### الف) نصب آنلاین از GitHub (یک دستور)

**گام ۱ — ورود به سرور:** با PuTTY یا `ssh root@IP-سرور` وارد شوید (کاربر root یا کاربری با sudo).

**گام ۲ — اجرای نصب:**
```bash
cd /root
bash <(curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh)
```
شکل `curl ... | bash` هم کار می کند (ورودی ویزارد از `/dev/tty` خوانده می شود):
```bash
curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh | bash
```
ریپو در پوشه جاری کلون می شود (`/root/special-materials-robot-project`؛ برای مسیر دیگر: `INSTALL_DIR=/opt/nasoz` جلوی دستور).

**گام ۳ — منو و ویزارد:** منوی اصلی نمایش داده می شود (پایین تر: «منوی اصلی نصب کننده»)؛ عدد **1** (نصب جدید) را بزنید و به ویزارد پاسخ دهید: توکن بازو از `@botfather` (مخفی تایپ می شود)، نام کاربری ربات، شناسه مالک (اختیاری)، پورت وب (پیش فرض 8000) و ... — با Enter مقدار پیش فرض داخل `[ ]` پذیرفته می شود. جدول کامل سوال ها پایین تر است.

**گام ۴ — پایان:** در آخر خلاصه ای با آدرس پنل وب، نام کاربری و دستورهای سرویس چاپ می شود. سپس «کارهای بعد از نصب» را انجام دهید.

**به روزرسانی آنلاین** (`.env` و `data/` حفظ می شود و قبل از آن از DB پشتیبان گرفته می شود):
```bash
cd /root/special-materials-robot-project
bash install.sh --update          # فقط برنامه: git pull + pip + migrate + ری استارت + تست سلامت
bash install.sh --full-update     # اول ارتقای سیستم عامل (apt upgrade)، بعد همان --update
```

دستی (بدون curl):
```bash
git clone https://github.com/silent4time/special-materials-robot-project.git
cd special-materials-robot-project
bash install.sh
```

### ب) نصب آفلاین (سرور بدون دسترسی به GitHub/PyPI)

بسته آفلاین همه چیز را با خودش دارد: کد برنامه، همه wheel های Python برای **Ubuntu 24.04 / Python 3.12 / x86_64**، `install_offline.sh` و `SHA256SUMS`. فقط apt باید به یک mirror (مثلا ubuntu.parsvds.com یا mirror دیتاسنتر) برسد تا tesseract و python3-venv نصب شوند.

**گام ۱ — دریافت بسته:** فایل `nasoz_offline_<تاریخ>_<commit>.tar.gz` را (از لینکی که در اختیارتان گذاشته شده) دانلود کنید. ساخت بسته روی سیستمی با اینترنت آزاد: `bash scripts/build_offline_bundle.sh` (خروجی در `dist/`).

اگر بسته بزرگ تر از 25 مگابایت باشد ممکن است به صورت چند تکه برسد: `...tar.gz.part0`، `...tar.gz.part1`، `...tar.gz.part2`. همه تکه ها را دانلود کنید و همه را (بدون تغییر نام) کنار هم نگه دارید. می توانید یا همین تکه ها را به سرور بفرستید و آنجا به هم بچسبانید (گام ۳)، یا در ویندوز (CMD) یکی کنید:
```bat
copy /b nasoz_offline_X.tar.gz.part0+nasoz_offline_X.tar.gz.part1+nasoz_offline_X.tar.gz.part2 nasoz_offline_X.tar.gz
```

**گام ۲ — ارسال با WinSCP:** در WinSCP با پروتکل SFTP و کاربر root به سرور وصل شوید، به پوشه `/root` بروید و فایل `tar.gz` (یا همه تکه های `.part*`) را به آنجا بکشید.

**گام ۳ — یکی کردن تکه ها (فقط اگر تکه تکه فرستادید):** در SSH:
```bash
cd /root
cat nasoz_offline_X.tar.gz.part0 nasoz_offline_X.tar.gz.part1 nasoz_offline_X.tar.gz.part2 > nasoz_offline_X.tar.gz
# یا کوتاه تر (ترتیب part0، part1، ... حفظ می شود):  cat nasoz_offline_X.tar.gz.part* > nasoz_offline_X.tar.gz
```
(به جای `nasoz_offline_X` نام واقعی فایل را بگذارید؛ با `ls /root` ببینید.)

**گام ۴ — بررسی سالم بودن فایل:**
```bash
sha256sum nasoz_offline_X.tar.gz
```
عدد چاپ شده باید دقیقا با sha256 ای که همراه بسته داده شده یکی باشد. اگر فرق داشت فایل ناقص است؛ دوباره بفرستید.

**گام ۵ — باز کردن و نصب:**
```bash
tar -xzf nasoz_offline_X.tar.gz
cd nasoz_offline_X
bash install_offline.sh          # منوی اصلی ← گزینه 1 (نصب جدید) — نصب در /opt/nasoz (مسیر دیگر: INSTALL_DIR=/مسیر)
# بدون منو، مستقیم: bash install_offline.sh --install
```
`install_offline.sh` اول SHA256SUMS همه فایل ها را بررسی می کند، منو را نشان می دهد و با گزینه 1 کد را در `/opt/nasoz` کپی و همان ویزارد را اجرا می کند (pip فقط از `wheels/` بسته نصب می کند، `--no-index`). برای دیدن مراحل بدون هیچ تغییری (چیزی در /opt/nasoz کپی یا نصب نمی شود): `bash install_offline.sh --dry-run`.

**به روزرسانی آفلاین با بسته جدید** (`.env` و `data/` و `uploads/` حفظ می شود، قبل از آن پشتیبان DB گرفته می شود):
```bash
cd /root
tar -xzf nasoz_offline_<جدید>.tar.gz && cd nasoz_offline_<جدید>
bash install_offline.sh --update          # یا --full-update برای ارتقای سیستم عامل هم
# یا بدون باز کردن دستی:
bash /opt/nasoz/scripts/install.sh --update --offline /root/nasoz_offline_<جدید>.tar.gz
```
در حالت آفلاین git pull انجام نمی شود و ارتقای سیستم عامل فقط با `--full-update` اجرا می شود. بسته ساخته شده با `--with-debs` اگر apt به هیچ mirror نرسد tesseract و python3-venv را از فایل های .deb خودش نصب می کند.

### بازگردانی بک آپ روی سرور (هر دو روش)
فایل بک آپ (`nasoz_backup_....zip`) را با WinSCP به `/root` بفرستید، سپس در پوشه نصب (`/opt/nasoz` در روش آفلاین یا `/root/special-materials-robot-project` در روش آنلاین):
```bash
cd /opt/nasoz
bash scripts/install.sh --restore /root/nasoz_backup_1405-07-25_0200.zip    # همان: bash install.sh --restore ...
```
اول فایل بررسی می شود (فایل نامعتبر بدون هیچ تغییری رد می شود)، سرویس ها متوقف می شوند، یک بک آپ «پیش از بازگردانی» گرفته می شود، داده ها برمی گردند، migrate و استارت و تست سلامت انجام می شود. با `--dry-run` فقط مراحل چاپ می شود.

## منوی اصلی نصب کننده

اگر `install.sh` (یا `bash install_offline.sh` در بسته آفلاین) **بدون هیچ پرچمی** در یک ترمینال SSH اجرا شود، اول لوگوی NASOZ و نسخه برنامه و سپس این منوی فارسی نمایش داده می شود (ورودی از `/dev/tty` خوانده می شود، پس با `curl ... | bash` هم کار می کند):

```text
  1) نصب جدید
  2) به روزرسانی
  3) وضعیت
  4) گرفتن بک آپ
  5) بازیابی بک آپ
  6) تعمیر
  7) مدیریت سرویس ها
  8) مشاهده لاگ ها
  9) تغییر تنظیمات (.env)
  0) خروج
```

| گزینه | کار |
|------|-----|
| 1 نصب جدید | همان ویزارد نصب کامل با نوار پیشرفت (مثل `--install`)؛ در بسته آفلاین نصب در `/opt/nasoz` |
| 2 به روزرسانی | می پرسد ارتقای سیستم عامل (apt upgrade) هم انجام شود (`--full-update`) یا فقط برنامه (`--update`)؛ مسیر بسته آفلاین را هم می پذیرد (خالی = آنلاین از GitHub) |
| 3 وضعیت | نسخه (commit/تاریخ)، مسیر نصب، نسخه پایتون، خلاصه `.env` (توکن هرگز نمایش داده نمی شود؛ نام ربات با getMe)، شناسه مالک/مدیرها، پورت وب، وضعیت سرویس های nasoz-bot و nasoz-web و مدت روشن بودن، اتصال به tapi.bale.ai، پنل وب محلی، github.com، pypi.org و mirror apt (✓/✗ با کد HTTP)، حجم data/ و پایگاه داده، آخرین بک آپ و وضعیت بک آپ شبانه |
| 4 گرفتن بک آپ | بک آپ کامل دستی با همان سرویس بک آپ ربات؛ مسیر، حجم و sha256 چاپ می شود و در صورت تمایل برای مالک در بله فرستاده می شود |
| 5 بازیابی بک آپ | فهرست شماره دار بک آپ های data/backups یا مسیر دلخواه؛ تایید با شناسه عددی مالک در بله؛ مثل `--restore` اول بک آپ «پیش از بازگردانی» گرفته می شود |
| 6 تعمیر | مثل `--repair` |
| 7 مدیریت سرویس ها | استارت، توقف، ری استارت یا وضعیت ربات، پنل وب یا هر دو (systemd یا nohup) |
| 8 مشاهده لاگ ها | آخرین خط های لاگ ربات، پنل وب، آخرین لاگ نصب یا journalctl (توکن در خروجی پوشانده می شود) |
| 9 تغییر تنظیمات | مثل `--reconfigure` |

بعد از هر کار با Enter به منو برمی گردید. اگر ربات هنوز نصب نشده باشد، گزینه های نیازمند نصب پیام «ابتدا گزینه 1» می دهند. **همه پرچم ها مثل قبل بدون منو کار می کنند** (`--update`، `--repair`، `--restore FILE`، ...)؛ نصب مستقیم بدون منو: `bash install.sh --install`؛ منو همراه با پرچم: `bash install.sh --menu --dry-run`. بدون ترمینال (cron، `NONINTERACTIVE=1`) منو نمایش داده نمی شود.

## کارهای بعد از نصب

1. **باز کردن پورت پنل وب:** روی سرور
   ```bash
   sudo ufw allow 8000/tcp
   ```
   و در پنل ابری یا فایروال شرکت ارائه دهنده سرور (Security Group / Firewall) هم پورت ورودی **8000/TCP** را باز کنید (اگر پورت دیگری در ویزارد دادید، همان را).
2. **مالک ربات:** در بله به ربات `/start` بفرستید — **اولین کسی که /start بزند مالک می شود** (مگر شناسه مالک را در سوال ۳ داده باشید).
3. **ورود پنل وب** (اگر شناسه مالک را در ویزارد نداده اید، بعد از اولین /start):
   ```bash
   cd /opt/nasoz && bash install.sh --web-login      # در روش آنلاین: cd /root/special-materials-robot-project
   ```
   نام کاربری و رمز: `grep WEB_ADMIN_ .env` — پنل: `http://IP-سرور:8000/login`
4. **فقط یک نمونه با هر توکن:** نسخه قدیمی ربات روی هر ماشین دیگری را خاموش کنید.

## نصب کننده چه می کند

1. بسته های سیستم: `python3 python3-venv python3-pip git curl tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng sqlite3 fontconfig` (فونت Vazirmatn داخل `fonts/` است)
2. **ویزارد فارسی** (قبل از نصب های طولانی) و نوشتن `.env` با `chmod 600`
3. `.venv` + `pip install -r requirements.txt` + تست import ماژول ها + ساخت/مهاجرت دیتابیس و پوشه های `data/ uploads/ reports/`
4. دو سرویس systemd: **`nasoz-bot`** (ربات) و **`nasoz-web`** (پنل وب) با `Restart=always`، فعال در بوت (در root/sudo پیش فرض بله). بدون systemd با `nohup` اجرا می شود.
5. تست سلامت: `getMe`، زنده بودن ربات، پاسخ `200` از `/login` پنل وب؛ و خلاصه فارسی (آدرس وب، دستورات سرویس)

خروجی ترمینال نصب کننده عمدا بدون نیم فاصله (ZWNJ) است، چون PuTTY و CMD آن را نشان نمی دهند و کلمه ها به هم می چسبند.

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

## دستورات

| دستور | کار |
|------|-----|
| `bash install.sh` | در ترمینال: **منوی اصلی** (بالا)؛ بدون ترمینال: نصب کامل |
| `bash install.sh --install` | **نصب کامل با یک دستور، بدون منو**: (پرسش ارتقای سیستم — پیش‌فرض بله) + بسته‌ها + venv + ویزارد `.env` + DB + سرویس‌ها + تست سلامت |
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
- **دستیار هوشمند از سرور داخل ایران نصب نمی شود** (`curl: (22) ... 403`): سایت Ollama و مخزن مدل های آن آی پی ایران را مسدود می کنند. از این نسخه این مرحله اختیاری هرگز نصب را متوقف نمی کند: اول یک بررسی سریع دسترسی (حداکثر چند ثانیه) انجام می شود و در صورت خطا پیام «دستیار هوشمند نصب نشد (...). ربات بدون دستیار کار می کند.» چاپ، در لاگ نصب ثبت و `ASSISTANT_ENABLED=0` در `.env` نوشته می شود و نصب با مراحل بعدی ادامه می یابد. برای نصب دستیار از ایران پروکسی لازم است، مثلا: `HTTPS_PROXY=http://IP-پروکسی:پورت bash install.sh --with-assistant` (یا نصب دستی Ollama و مدل؛ پایین تر). بسته آفلاین هم Ollama/مدل را ندارد و همین رفتار را دارد.

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

با `--with-assistant` (یا پاسخ Y): نصب Ollama در صورت نبود، `ollama pull` مدل، و نوشتن `OLLAMA_*` در `.env`. هر خطا در این مرحله (سایت در دسترس نیست، 403، دانلود ناموفق، مدل) فقط هشدار می دهد و نصب ربات ادامه پیدا می کند؛ دستیار غیرفعال می ماند (`ASSISTANT_ENABLED=0`). از ایران بدون پروکسی ممکن نیست.

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
