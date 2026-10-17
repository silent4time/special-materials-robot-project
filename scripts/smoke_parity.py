#!/usr/bin/env python3
"""Bot ⇄ web parity guard (1405/07/25).

Every bot feature button (bot.keyboards.BUTTON_FEATURE) must map to a web route and every
web route must be covered by a bot button — unless whitelisted WITH a reason. Also checks the
permission gate is the same (button features ⊆ what the web page requires) by logging in as
an owner and as a technician and hitting the new parity pages.
"""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# bot button → web path
BOT_TO_WEB = {
    "📥 موجودی روزانه سایت": "/stock",
    "🧾 گزارش تاندیش": "/tundish-report",
    "🛒 درخواست مواد": "/materials/request",
    "↩️ برگشت به انبار": "/materials/return",
    "📊 گزارش‌ها": "/reports",
    "🎯 هدف اصلی": "/reports/main-goal",
    "📤 ورود فایل‌ها": "/files",
    "⚙️ تنظیمات": "/home",
    "📈 مصرف روزانه مواد": "/reports/daily",
    "📅 گزارش مصرف بازه‌ای": "/reports/period",
    "📊 گزارش جامع": "/reports/comprehensive",
    "⚠️ پوشش کوتاه‌مدت موجودی سایت": "/reports/remaining-critical",
    "🚨 اقلام بحرانی (نیاز ۳/۶ ماه)": "/reports/critical-items",
    "🧮 نیاز مواد برای N تاندیش": "/reports/n-tundish",
    "📦 گزارش مواد مازاد": "/reports/surplus",
    "📄 گزارش اقلام ورودی به انبار": "/reports/inbound/{report_id}.pdf",
    "📄 دریافت خلاصه مصرف ماهیانه": "/reports/monthly-summary",
    "📥 به‌روزرسانی موجودی انبار": "/files/stock",
    "📥 مصرف ماهیانه اقلام": "/files/monthly",
    "📤 گزارش جامع تاندیش در سکوئنس": "/reports/main-goal/inputs/sequence",
    "📸 عکس‌های آمار تولید": "/reports/main-goal/inputs/production-photo",
    "📦 منبع اصلی": "/settings/main-source",
    "📄 دانلود اکسل منبع اصلی": "/settings/main-source/download.xlsx",
    "📋 کدهای دسته": "/settings/main-source",
    "📦 جایگزینی کامل منبع اصلی": "/settings/main-source/upload",
    "➕ افزودن رکورد": "/settings/main-source/add",
    "✏️ ویرایش رکورد": "/settings/main-source/edit",
    "➕ افزودن کد دسته": "/settings/main-source/categories/add",
    "📝 درخواست‌های در انتظار تأیید": "/settings/main-source/requests/{req_id}/{action}",
    "👥 کاربران": "/settings/users",
    "➕ اضافه کردن کاربر": "/settings/users/invite",
    "✏️ اصلاح نقش کاربر": "/settings/users/{target_id}/role",
    "🗑 حذف کاربر": "/settings/users/{target_id}/delete",
    "📋 لیست کاربران": "/settings/users",
    "📋 فعالیت کاربران": "/reports/user-activity",
    "🔐 دسترسی نقش‌ها": "/settings/permissions",
    "🧾 اقلام فرم گزارش تاندیش": "/tundish-report/settings",
    "🔔 یادآورها": "/settings/reminders",
    "📣 گروه گزارش موجودی": "/settings/bot",
    "🎨 ظاهر": "/settings/bot",
    "📝 متن دعوت‌نامه کاربران": "/settings/bot/text/{which}",
    "👋 پیام خوشامدگویی": "/settings/bot/text/{which}",
    "🖼 لوگوی ربات": "/settings/bot/asset/{which}",
    "📄 سربرگ PDF": "/settings/bot/asset/{which}",
    "🗑 حذف یک ماه از سابقه": "/reports/main-goal/months/delete",
    "💾 بک‌آپ": "/settings/backups",
    "💾 ساخت بک‌آپ کامل": "/settings/backups/create",
    "📋 لیست بک‌آپ‌ها": "/settings/backups",
    "♻️ بازگردانی از بک‌آپ": "/settings/backups/restore/upload",
}
BOT_ONLY: dict[str, str] = {}

# web routes with no own bot button (reason required)
WEB_ONLY = {
    "/login": "ورود وب (ربات با حساب بله احراز می‌شود)",
    "/logout": "خروج وب",
    "/web-login": "تنظیم نام‌کاربری/رمز ورود وب — فقط برای خود وب معنا دارد",
    "/settings/web-login": "تنظیم نام‌کاربری/رمز ورود وب — فقط برای خود وب معنا دارد",
    "/file-guides/{name}": "تصاویر ❓ راهنما (ربات همان تصاویر را آلبوم می‌فرستد)",
    "/files/inbound/{report_id}.{ext}": "ربات فایل گزارش ورودی را بلافاصله پس از آپلود موجودی می‌فرستد",
    "/settings/main-source/sibling-defaults": "پیشنهاد پیش‌پر فرم (ربات: پیشنهاد «+» در گفتگو)",
    "/settings/bot/stock-group/clear": "ربات: «📣 گروه گزارش موجودی» ← حذف",
    "/assistant": "ربات: «🤖 از دستیار بپرس» / متن آزاد / پیام صوتی (فقط وقتی مالک دستیار را فعال کرده؛ خارج از BUTTON_FEATURE، مجوز هر پرسش جدا)",
    "/assistant/ask": "ارسال پرسش همان صفحهٔ دستیار",
    "/assistant/misfire": "ربات: دکمهٔ «❌ منظورم این نبود» زیر پاسخ دستیار (ثبت + انتخاب نیت)",
    "/assistant/file/{token}/{kind}": "ربات: PDF جدول پاسخ دستیار (پیوست پیام)؛ وب: دانلود PDF/اکسل همان جدول",
    "/settings/bot/assistant": "ربات: ⚙️ ← 🤖 دستیار هوشمند صوتی (فقط مالک، خارج از BUTTON_FEATURE)",
    "/tundish-report/parse": "مرحلهٔ پیش‌نمایش فرم (ربات: گفتگو مرحله‌ای)",
    "/tundish-report/submit": "ثبت فرم گزارش تاندیش",
    "/stock/save": "ثبت فرم موجودی سایت",
    "/reports/critical-items/counts": "ربات: پرسش تعداد تاندیش در جریان «🚨 اقلام بحرانی»",
    "/reports/main-goal/inputs/production-excel": "سازگاری قدیمی (طبق تصمیم مالک آمار تولید فقط عکس است)",
    "/reports/main-goal/inputs/production-manual": "ربات: «✏️ اصلاح دستی تولید» داخل 🎯 هدف اصلی",
    "/reports/main-goal/inputs/consumption": "سازگاری قدیمی مصرف تاندیش (جایگزین: گزارش جامع سکوئنس)",
    "/reports/main-goal/report/range": "ربات: «📈 گزارش بازه» داخل 🎯 هدف اصلی",
    "/reports/main-goal/run": "ربات: داخل 🎯 هدف اصلی",
    "/reports/main-goal/months/upload": "ربات: داخل 🎯 هدف اصلی",
    "/reports/main-goal/months/bulk": "ربات: داخل 🎯 هدف اصلی",
    "/reports/main-goal/months/{month_id}/delete": "همان «🗑 حذف یک ماه از سابقه»",
    "/reports/main-goal/scenario/target": "ربات: «🎯 سناریوی هدف» داخل 🎯 هدف اصلی",
    "/reports/main-goal/scenario/forecast": "ربات: «🔮 پیش‌بینی» داخل 🎯 هدف اصلی",
    "/reports/main-goal/download/{stem}.pdf": "دانلود خروجی (ربات فایل را می‌فرستد)",
    "/reports/main-goal/download/{stem}.xlsx": "دانلود خروجی (ربات فایل را می‌فرستد)",
    "/settings/reminders/send-now": "ربات: «🔔 یادآورها» ← ارسال فوری",
    "/settings/permissions/reset": "ربات: «🔐 دسترسی نقش‌ها» ← بازنشانی",
    "/settings/backups/file/{name}": "ربات: دکمهٔ ⬇️ هر ردیف در «📋 لیست بک‌آپ‌ها»",
    "/settings/backups/restore/confirm": "ربات: دو تأیید inline پس از ارسال ZIP",
    "/tundish-report/settings/lines": "ربات: «🧾 اقلام فرم گزارش تاندیش»",
}


def _covered(route: str, mapped: set[str]) -> bool:
    if route in mapped or route.rstrip("/") in mapped:
        return True
    for m in mapped:  # sub-actions / alternate formats of a mapped page
        if route.startswith(m + "/") or route.startswith(m + ".") or (m.endswith(".pdf") and route == m[:-4] + ".xlsx"):
            return True
    return False


def main() -> int:
    from fastapi.testclient import TestClient

    from bot import keyboards as kb
    from db.models import Database
    from web.app import create_app
    from web.auth_web import set_credential
    from web.deps import reset_db_singleton

    errors = []
    for btn in kb.BUTTON_FEATURE:
        if btn not in BOT_TO_WEB and btn not in BOT_ONLY:
            errors.append(f"bot button without web route: {btn}")
    app = create_app()
    routes = {p.rstrip("/") or "/" for p in app.openapi().get("paths", {})}
    routes.discard("/")
    for btn, path in BOT_TO_WEB.items():
        if path not in routes:
            errors.append(f"mapped web route missing: {btn} → {path}")
    mapped = set(BOT_TO_WEB.values())
    for r in sorted(routes):
        if not _covered(r, mapped) and r not in WEB_ONLY:
            errors.append(f"web route without bot feature (add to map or WEB_ONLY with reason): {r}")
    assert all(WEB_ONLY.values())
    if errors:
        print("\n".join(errors))
        return 1
    print(f"parity map OK: {len(BOT_TO_WEB)} bot buttons, {len(routes)} web routes, {len(WEB_ONLY)} justified web-only")

    # same gates on the new pages
    reset_db_singleton()
    db = Database()
    db.upsert_user("8801", "owner", display_name="مالک آزمون")
    db.upsert_user("8802", "technician", display_name="تکنسین آزمون")
    set_credential(db, bale_user_id="8801", username="owner8801", password="secret88")
    set_credential(db, bale_user_id="8802", username="tech8802", password="secret88")
    own, tech = TestClient(app), TestClient(app)
    assert own.post("/login", data={"username": "owner8801", "password": "secret88"}, follow_redirects=False).status_code in (302, 303)
    assert tech.post("/login", data={"username": "tech8802", "password": "secret88"}, follow_redirects=False).status_code in (302, 303)
    for p in ("/files", "/settings/users", "/settings/bot", "/settings/main-source", "/reports", "/settings/backups"):
        r = own.get(p)
        assert r.status_code == 200, (p, r.status_code, r.text[:300])
    assert "🤖 دستیار هوشمند صوتی" in own.get("/settings/bot").text
    assert "📈 مصرف روزانه مواد" in own.get("/reports").text
    for p in ("/files", "/settings/users", "/settings/bot", "/settings/backups"):
        r = tech.get(p, follow_redirects=False)
        assert r.status_code in (302, 303, 403), (p, r.status_code)
    j = own.get("/settings/main-source/sibling-defaults?code=1203&item_id=120300009002").json()
    assert j["contractor_or_company"] == "پیمانکار", j
    assert tech.get("/settings/main-source/sibling-defaults?code=1203", follow_redirects=False).status_code in (302, 303, 403)
    r = own.post("/files/stock", files={"file": ("x.csv", io.BytesIO(b"a"), "text/csv")})
    assert r.status_code == 400 and ".xlsx" in r.text, r.status_code
    r = own.post("/settings/users/8801/delete")
    assert r.status_code == 400 and "خودتان" in r.text
    r = own.post("/settings/users/invite", data={"role": "technician"})
    assert r.status_code == 200 and "ble.ir/" in r.text
    r = own.post("/settings/main-source/add", data={"category_code": "1203", "id": "1203-0000-0009", "product_name": "x", "quantity": "1"})
    assert r.status_code == 400 and "اولویت" in r.text
    print("smoke_parity OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
