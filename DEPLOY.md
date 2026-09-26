# استقرار بازو روی سرور

ربات برای کار دائمی باید روی یک سرور لینوکس همیشه روشن (VPS، Ubuntu/Debian) با **Python 3.10+** اجرا شود.

## نصب سریع (پیشنهادی)

یک دستور از ریشهٔ ریپو (بعد از کلون، یا از raw روی GitHub):

```bash
curl -fsSL https://raw.githubusercontent.com/silent4time/special-materials-robot-project/main/install.sh | bash
```

یا به‌صورت دستی:

```bash
git clone https://github.com/silent4time/special-materials-robot-project.git
cd special-materials-robot-project
bash install.sh --seed-admin
```

اسکریپت `install.sh` (و `scripts/install.sh`) روی سرور تازه‌کار:

1. نسخهٔ Python را چک می‌کند (≥ 3.10)
2. در صورت نیاز `python3-venv` و `git` را با apt نصب می‌کند
3. در صورت نبود ریپو، کلون می‌کند (یا از `INSTALL_DIR` / cwd استفاده می‌کند)
4. `.venv` می‌سازد و `requirements.txt` را نصب می‌کند
5. اگر `.env` نباشد از `.env.example` کپی می‌کند و راهنمای فارسی چاپ می‌کند

پرچم‌های مفید:

| پرچم | کار |
|------|-----|
| `--seed-admin` | اجرای `scripts/seed_admin.py` |
| `--systemd` | نصب unit از `scripts/nasoz-bot.service.in` |
| `--start` | شروع سرویس systemd یا اجرای foreground |
| `--update` | `git pull` + pip + restart systemd |

مثال استقرار کامل:

```bash
bash install.sh --seed-admin --systemd --start
```

به‌روزرسانی بعدی:

```bash
bash install.sh --update
```

## توکن و مالک

```bash
cp .env.example .env   # اگر install.sh قبلاً نکرده
nano .env
```

حداقل این دو را پر کنید (توکن جعلی نسازید؛ از `@botfather` بگیرید):

- `BALE_BOT_TOKEN` — توکن بازو (مثل رمز؛ **در گیت نگذارید**)
- `ADMIN_BALE_USER_ID` — شناسه عددی بلهٔ مالک

```bash
bash install.sh --seed-admin
# یا: python scripts/seed_admin.py
```

## اجرای آزمایشی (بدون systemd)

```bash
source .venv/bin/activate
python main.py
```

در بله به بازو پیام `/start` بدهید. با `Ctrl+C` متوقف کنید.

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
cp .env.example .env && nano .env
python scripts/seed_admin.py
python main.py
```
