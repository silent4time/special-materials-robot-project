"""Seed data for «گزارش تاندیش بعد از ریخته‌گری» (DB-free; imported by db.models).

Slab items were derived from the operator sample (billet/bloom sample below):

    «تاندیش ۱۰ اسلب ۱ سکونس ۸ ذوبه مارک ۳۷۱۰ . نازل هر دو خط در ذوب ۵ بعلت
    گرفتگی تعویض گردید شرود تا پایان سکونس نرمال سولار پودر قالب کاسپین
    پودر تاندیش فارس ریزان»

Admins edit everything afterwards from the bot/web settings (CRUD).
"""
from __future__ import annotations

SECTIONS: dict[str, str] = {
    "slab": "اسلب",
    "bloom": "بلوم",
    "billet": "بیلت",
}
SECTION_ORDER = ("slab", "bloom", "billet")

# Default casting lines per section (editable list stored in bot_settings).
DEFAULT_LINES: dict[str, list[str]] = {
    "slab": ["اسلب ۱ (CCM1)", "اسلب ۲ (CCM2)"],
    "bloom": ["بلوم (CCM3)"],
    "billet": ["بیلت ۱ (CCM4)", "بیلت ۲ (CCM5)"],
}

_POWDER_BRANDS = ["کاسپین", "فارس ریزان", "سولار"]

DEFAULT_ITEMS: dict[str, list[dict]] = {
    "slab": [
        {
            "label": "نازل — وضعیت",
            "item_type": "choice",
            "options": [
                "نرمال تا پایان سکونس",
                "تعویض خط ۱",
                "تعویض خط ۲",
                "تعویض هر دو خط",
            ],
            "required": True,
        },
        {"label": "نازل — ذوب تعویض", "item_type": "number", "options": [], "required": False},
        {
            "label": "نازل — علت تعویض",
            "item_type": "choice",
            "options": ["گرفتگی", "فرسایش", "ترک / شکستگی", "نشتی"],
            "required": False,
        },
        {
            "label": "شرود — وضعیت",
            "item_type": "choice",
            "options": ["نرمال تا پایان سکونس", "تعویض", "شکستگی"],
            "required": True,
        },
        {
            "label": "شرود — سازنده",
            "item_type": "choice",
            "options": ["سولار", "کاسپین", "فارس ریزان"],
            "required": False,
        },
        {"label": "پودر قالب", "item_type": "choice", "options": list(_POWDER_BRANDS), "required": True},
        {
            "label": "پودر تاندیش",
            "item_type": "choice",
            "options": ["فارس ریزان", "کاسپین", "سولار"],
            "required": True,
        },
    ],
}

# Billet sample (bloom shares the same structure):
#   «تاندیش 8 بیلت 1 بعلت محدودیت سکونس 12 ذوبه پایان یافت / مارک ذوب 4446 /
#    نوع ایمپکت رایان / مدت ریخته گری 835»
_BILLET_BLOOM_ITEMS: list[dict] = [
    {
        "label": "علت پایان سکونس",
        "item_type": "choice",
        "options": [
            "محدودیت سکونس",
            "گرفتگی نازل",
            "مشکل ماشین ریخته‌گری",
            "دما / کیفیت مذاب",
            "برنامه تولید",
        ],
        "required": True,
    },
    {
        "label": "نوع ایمپکت",
        "item_type": "choice",
        "options": ["رایان", "کاسپین", "فارس ریزان"],
        "required": True,
    },
    {"label": "مدت ریخته‌گری (دقیقه)", "item_type": "number", "options": [], "required": True},
]
DEFAULT_ITEMS["billet"] = [dict(it, options=list(it["options"])) for it in _BILLET_BLOOM_ITEMS]
DEFAULT_ITEMS["bloom"] = [dict(it, options=list(it["options"])) for it in _BILLET_BLOOM_ITEMS]
