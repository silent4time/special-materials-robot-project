# استقرار بازو روی سرور

ربات برای کار دائمی باید روی یک سرور لینوکس همیشه روشن (VPS، Ubuntu/Debian) با **Python 3.10+** اجرا شود.

## نصب سریع (پیشنهادی)

یک دستور از ریشهٔ ریپو (بعد از کلون، یا از raw روی GitHub):

```bash
curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh | bash
```

اگر نصب از طریق `curl | bash` باشد (بدون TTY)، بعداً در ترمینال واقعی ویزارد توکن و نام کاربری ربات را اجرا کنید:

```bash
cd special-materials-robot-project
bash install.sh
```

یا به‌صورت دستی:

```bash
git clone https://github.com/silent4time/special-materials-robot-project.git
cd special-materials-robot-project
bash install.sh
```

اسکریپت `install.sh` (و `scripts/install.sh`) روی سرور تازه‌کار:

1. نسخهٔ Python را چک می‌کند (≥ 3.10)
2. در صورت نیاز `python3-venv` و `git` را با apt نصب می‌کند
3. در صورت نبود ریپو، کلون می‌کند (یا از `INSTALL_DIR` / cwd استفاده می‌کند)
4. `.venv` می‌سازد و `requirements.txt` را نصب می‌کند
5. اگر `.env` پیکربندی نشده باشد و stdin یک TTY باشد، **ویزارد فارسی** توکن، نام کاربری ربات و تنظیمات را می‌پرسد و `.env` می‌نویسد
6. مالک را لازم نیست از قبل بدانید: **اولین `/start` در بله → نقش مالک**

پرچم‌های مفید:

| پرچم | کار |
|------|-----|
| `--seed-admin` | اختیاری؛ مالک با اولین `/start` ساخته می‌شود (`scripts/seed_admin.py` اگر `ADMIN_BALE_USER_ID` ست باشد) |
| `--systemd` | نصب unit از `scripts/nasoz-bot.service.in` |
| `--start` | شروع سرویس systemd یا اجرای foreground |
| `--update` | `git pull` + pip + restart systemd |
| `--with-assistant` | نصب **دستیار هوشمند**: Ollama + `ollama pull` مدل + نوشتن `OLLAMA_*` در `.env` (بدون پرسش) |
| `--no-assistant` | رد نصب دستیار هوشمند (بدون پرسش) |

در حالت تعاملی (TTY) اگر هیچ‌کدام از دو پرچم بالا نباشد، اسکریپت می‌پرسد: **«دستیار هوشمند را هم نصب کنم؟ (Y/n)»** (پیش‌فرض بله).

مثال استقرار کامل (بعد از ویزارد، یا با پاسخ y به سوال systemd):

```bash
bash install.sh --systemd --start
```

به‌روزرسانی بعدی:

```bash
bash install.sh --update
```

## توکن و مالک

در ویزارد تعاملی:

1. **توکن بازو** (`BALE_BOT_TOKEN`) — الزامی؛ از `@botfather`
2. **نام کاربری ربات در بله** بدون `@` — الزامی (برای لینک دعوت؛ از `getMe` پیشنهاد می‌شود)
3. نصب/استارت systemd؟ (`y/N`)
4. `CRITICAL_DAYS` — پیش‌فرض `3`

`ADMIN_BALE_USER_ID` دیگر الزامی نیست. اگر خالی باشد، اولین کسی که در بله `/start` بزند مالک می‌شود.

در حالت غیرتعاملی می‌توانید قبل از نصب ست کنید:

```bash
export BALE_BOT_TOKEN='123456789:ABC...'
bash install.sh
```

## اجرای آزمایشی (بدون systemd)

```bash
source .venv/bin/activate
python main.py
```

در بله به بازو پیام `/start` بدهید (اولین نفر = مالک). با `Ctrl+C` متوقف کنید.

## systemd دستی

اگر `--systemd` را نمی‌خواهید، قالب اینجاست:

`scripts/nasoz-bot.service.in` با جای‌نگهدار `__WORKDIR__` و `__USER__`.

```bash
# بعد از جایگزینی مسیر/کاربر:
sudo cp /tmp/nasoz-bot.service /etc/systemd/system/nasoz-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now nasoz-bot
sudo systemctl status nasoz-bot
journalctl -u nasoz-bot -f
```

## نکات مهم

- **فقط یک نمونه** از بازو با همان توکن باید long-poll کند؛ دو سرور همزمان با یک توکن تداخل می‌سازند.
- قبل از روشن کردن سرور تولید، اجرای موقت روی ماشین توسعه / باکس موقت را **خاموش** کنید.
- `.env` و دیتابیس `data/bot.db` را بکاپ بگیرید؛ توکن را در چت عمومی نفرستید.
- فایروال مخصوص پورت لازم نیست؛ بازو با HTTPS به `tapi.bale.ai` وصل می‌شود (خروجی اینترنت کافی است).

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

سپس ربات را روشن کنید (`python main.py` یا `bash install.sh --systemd --start`).

### استفاده در بله

منوی «گزارش‌ها / تحلیل تاندیش» → «🤖 دستیار هوشمند» (نقش **تکنسین** دسترسی ندارد). دامنه فقط گزارش‌ها است.

### رفع اشکال و به‌روزرسانی مدل

| مشکل | کار |
|------|-----|
| پیام «دستیار محلی در دسترس نیست…» | `ollama serve` / سرویس ollama؛ پورت `11434`؛ `OLLAMA_BASE_URL` |
| مدل نیست | `ollama pull` مطابق `OLLAMA_MODEL` |
| کندی | مدل کوچک‌تر یا افزایش `OLLAMA_TIMEOUT` |

به‌روزرسانی مدل: `ollama pull …` سپس در صورت نیاز تغییر `OLLAMA_MODEL` و restart ربات (`bash install.sh --update` یا `systemctl restart nasoz-bot`).
