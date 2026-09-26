# استقرار بازو روی سرور

ربات الان برای توسعه روی یک ماشین موقت اجرا می‌شود. برای کار دائمی، همین پروژه را روی یک سرور لینوکس همیشه روشن (VPS) با Python 3.11+ اجرا کنید.

## ۱. آماده‌سازی سرور

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

## ۲. کلون و وابستگی‌ها

```bash
git clone https://github.com/silent4time/special-materials-robot-project.git
cd special-materials-robot-project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

اگر ریپو خالی از فایل‌های پروژه به نظر می‌رسد یا ساختار پوشه فرق دارد، محتویات را از شاخه `main` بررسی کنید؛ کد اصلی داخل ریشه همین ریپو است.

## ۳. توکن و مالک

```bash
cp .env.example .env
nano .env
```

حداقل این دو را پر کنید:

- `BALE_BOT_TOKEN` — توکن بازو (مثل رمز؛ در گیت نگذارید)
- `ADMIN_BALE_USER_ID` — شناسه عددی بلهٔ مالک (مثلاً `1644670601`)

```bash
python scripts/seed_admin.py
```

## ۴. اجرای آزمایشی

```bash
python main.py
```

در بله به `@nasoz_bot` پیام `/start` بدهید. اگر منو آمد، درست است. با `Ctrl+C` متوقف کنید.

## ۵. اجرای دائمی با systemd

فایل سرویس (مسیرها را با کاربر و مسیر واقعی عوض کنید):

```bash
sudo nano /etc/systemd/system/nasoz-bot.service
```

```ini
[Unit]
Description=Bale tundish materials report bot (nasoz_bot)
After=network.target

[Service]
Type=simple
User=YOUR_LINUX_USER
WorkingDirectory=/home/YOUR_LINUX_USER/special-materials-robot-project
Environment=PATH=/home/YOUR_LINUX_USER/special-materials-robot-project/.venv/bin
ExecStart=/home/YOUR_LINUX_USER/special-materials-robot-project/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nasoz-bot
sudo systemctl status nasoz-bot
journalctl -u nasoz-bot -f
```

## نکات مهم

- **فقط یک نمونه** از بازو با همان توکن باید long-poll کند؛ دو سرور همزمان با یک توکن تداخل می‌سازند.
- قبل از روشن کردن سرور دائمی، اجرای موقت روی ماشین توسعه را خاموش کنید.
- `.env` و دیتابیس `data/bot.db` را بکاپ بگیرید؛ توکن را در چت عمومی نفرستید.
- فایروال مخصوص پورت لازم نیست؛ بازو با HTTPS به `tapi.bale.ai` وصل می‌شود (خروجی اینترنت کافی است).

## به‌روزرسانی

```bash
cd special-materials-robot-project
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart nasoz-bot
```
