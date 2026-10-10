#!/usr/bin/env python3
"""Smoke: daily site-stock input lists generated from منبع اصلی (name = «کلید واژه»).

Self-contained temp DB + synthetic منبع اصلی; never touches data/bot.db.
Covers: شرود (1581 / any shroud) in NO list, other سطح ریخته گری rows stay in their section
list (no separate casting list, no cast_* buttons/tabs), section rule (usage + شرکت/پیمانکار for codes with both sides), priority 0
and 1800 excluded, nozzle codes one line per code in bloom + billet, merged-cell
keyword forward-fill, empty-keyword fallback to شرح کالا, duplicate keyword → code
suffix, bot + web show identical lists, saving works, lists follow source changes,
synthetic lines stay out of the warehouse catalog, منبع اصلی is not modified.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import _smoke_isolation  # noqa: E402,F401
import pandas as pd  # noqa: E402

CO = "3781"   # company id prefix (chars 5-8 ≠ 0000)
CT = "37870000"  # contractor id prefix (chars 5-8 == 0000)


def _row(code, iid, name, kw, loc, prio=1, **rates):
    base = dict(category_code=code, id=iid, product_name=name, keyword=kw, usage_location=loc,
                quantity=10, priority=prio, unit="Kg",
                contractor_or_company="پیمانکار" if iid.startswith(CT) else "شرکت")
    for c in ("billet_renovation", "billet_patching", "bloom_renovation", "bloom_patching",
              "slab_renovation", "slab_patching"):
        base[c] = rates.get(c, 0)
    return base


def source_frame() -> pd.DataFrame:
    return pd.DataFrame([
        # code with BOTH sides: contractor → bloom, company → billet only
        _row("1637", CT + "30021A", "سنگ دیواره پیمانکار", "سنگ دیواره بلوم", "بلوم"),
        _row("1637", CO + "54231A", "سنگ دیواره شرکت", "سنگ دیواره بلومی", "بیلت"),
        # BOTH sides, company rows: «اسلب» → slab; without → no slab line
        _row("1203", CT + "90021G", "بتن پلی", "بتن پلی 85", "اسلب"),
        _row("1203", CO + "21641R", "بتن رایان", "بتن 85bt", "بیلت"),
        _row("1203", CO + "21649S", "بتن اسلب شرکت", "بتن اسلب شرکتی", "اسلب"),
        # company-only, «بلوم/اسلب» → bloom + slab, 3 ids → one line
        _row("1643", CO + "50242T", "اسپینل الف", "اسپینل کف 66*66", "بلوم/اسلب"),
        _row("1643", CO + "50252S", "اسپینل ب", "اسپینل کف 66*66", "بلوم/اسلب", prio=2),
        _row("1643", CO + "50253H", "اسپینل ج", None, "بلوم/اسلب", prio=3),  # merged cell → ffill
        # nozzles: one line per code, in bloom AND billet
        _row("1710", CO + "82801L", "ZETTRAL 16.5", "نازل 16.5", "بلوم / بیلت"),
        _row("1710", CO + "82881Q", "VESUVIUS 16.5", "نازل 16.5", "بلوم / بیلت", prio=2),
        _row("1712", CO + "82912S", "ATOR 17", "نازل 17", "بلوم / بیلت"),
        _row("1712", CO + "82931X", "IFGL 17 unused", "نازل 17", "بلوم / بیلت", prio=0),
        # priority 0 only → no line; 1800 → never
        _row("1605", CO + "53132U", "آجر NSB", "آجر NSB", "اسلب", prio=0),
        _row("1800", CO + "07241I", "شرود مازاد", "شرود", "سطح ریخته گری اسلب", prio=1),
        # keyword empty at code start → fallback to شرح کالا
        _row("1678", CO + "62101A", "SOL/FP/S-10 , SOLAR", "", "اسلب"),
        # duplicate keyword on two codes in billet → disambiguated by code
        _row("1274", CO + "30432T", "آجر کف شرکت", "آجر کف", "بیلت"),
        _row("1275", CO + "30433T", "آجر کف دیگر", "آجر کف", "بیلت"),
        # no section word → section from rates
        _row("1590", CO + "59001A", "ماده بی‌محل", "ماده بی‌محل", "", slab_renovation=3),
        # شرود → never in any daily-stock list (code 1581 or shroud name on another code)
        _row("1581", CO + "07741J", "LADLE SHROUD SOLAR", "شرود", "سطح ریخته گری اسلب"),
        _row("1581", CO + "07742I", "LADLE SHROUD OCL", "شرود", "سطح ریخته‌گری اسلب", prio=2),
        _row("1585", CO + "07999Z", "LADLE SHROUD BLOOM X", "شرود بلوم", "بلوم"),
        # other سطح ریخته گری rows stay in their section list
        _row("1658", CT + "70021E", "جرم ریختنی 60", "بتن پلی 60", "سطح ریخته گری اسلب/بلوم"),
        _row("1451", CO + "34102X", "RAYA GUN 80", "بتن 80 گان", "سطح ریخته گری بیلت"),
    ])


def main() -> int:
    from db.models import Database
    from services import site_stock_lists as ssl
    from services.main_source import load_primary_frame, persist_primary_frame

    tmp = Path(tempfile.mkdtemp(prefix="smoke_ssl_"))
    try:
        db = Database(tmp / "ssl.db")
        db.upsert_user("701", role="owner", display_name="مالک")
        db.upsert_user("702", role="technician", display_name="تکنسین")
        # legacy per-id WO assignment must NOT show in the generated lists
        db.upsert_catalog_item("LEGACY1", "LEGACY1 - قدیمی", category_code="1637")
        db.assign_item_to_group("LEGACY1", "bloom", assigned_by="system:work_order")
        path = persist_primary_frame(db, source_frame(), bale_user_id="701")
        src_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()

        res = ssl.build_site_stock_lists(load_primary_frame(db))
        ssl.items_for_group(db, "slab")  # sync writes catalog rows only
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == src_hash  # منبع اصلی untouched
        names = {g: [ln["name_desc"] for ln in res.lines[g]] for g in ssl.SECTION_ORDER}
        assert names["bloom"] == ["سنگ دیواره بلوم", "اسپینل کف 66*66", "بتن پلی 60", "نازل 16.5",
                                  "نازل 17"], names["bloom"]
        assert names["billet"] == ["بتن 85bt", "آجر کف (کد 1274)", "آجر کف (کد 1275)", "بتن 80 گان",
                                   "سنگ دیواره بلومی", "نازل 16.5", "نازل 17"], names["billet"]
        assert names["slab"] == ["بتن اسلب شرکتی", "بتن پلی 85", "ماده بی‌محل", "اسپینل کف 66*66",
                                 "بتن پلی 60", "SOL/FP/S-10 , SOLAR"], names["slab"]
        assert set(ssl.SECTION_ORDER) == {"slab", "bloom", "billet"}
        spinel = next(ln for ln in res.lines["bloom"] if ln["category_code"] == "1643")
        assert len(spinel["member_ids"]) == 3  # ffilled keyword merges the 3rd id
        noz17 = next(ln for ln in res.lines["billet"] if ln["category_code"] == "1712")
        assert noz17["member_ids"] == [CO + "82912S"]  # priority 0 excluded
        assert [f["id"] for f in res.fallbacks] == [CO + "62101A"], res.fallbacks
        assert res.duplicates == [{"section": "billet", "keyword": "آجر کف", "codes": ["1274", "1275"]}]
        assert [r["id"] for r in res.rate_fallback] == [CO + "59001A"] and not res.unplaced
        all_codes = {ln["category_code"] for g in res.lines.values() for ln in g}
        assert "1800" not in all_codes and "1605" not in all_codes
        assert not {"1581", "1585"} & all_codes, all_codes  # شرود in no list
        assert not any("شرود" in n for g in names.values() for n in g)
        assert {r["id"] for r in res.shroud_excluded} == {CO + "07741J", CO + "07742I", CO + "07999Z"}
        assert ssl.location_groups("سطح ریخته گری اسلب/بلوم") == ["slab", "bloom"]
        assert ssl.location_groups("بلوم / بیلت") == ["bloom", "billet"]
        assert ssl.location_groups("سطح ریخته‌گری بیلت") == ["billet"]
        print("build OK", res.counts())

        # --- bot: «موجودی مواد بلوم» shows the generated list
        import bot.handlers as handlers_mod

        class FakeClient:
            def __init__(self) -> None:
                self.msgs: list[str] = []

            def send_message(self, chat_id, text, reply_markup=None, **_kw):
                self.msgs.append(text)
                return {"ok": True, "message_id": len(self.msgs)}

            def __getattr__(self, name):
                return lambda *a, **k: {"ok": True}

        app = handlers_mod.BotApp(FakeClient(), db)
        msg = {"chat": {"id": 702}, "from": {"id": 702, "first_name": "ت"}, "text": ""}
        app.on_site_stock_group(msg, "bloom")
        pend = app._site_stock_pending["702"]
        bot_names = [it["name_desc"] for it in pend["items"]]
        assert bot_names == names["bloom"], bot_names
        assert "LEGACY1" not in {it["id"] for it in pend["items"]}
        from bot import keyboards as kb

        ik = kb.site_stock_inline_keyboard(pend["items"], {}, group_key="bloom")
        assert "نازل 16.5" in str(ik) and "SS:" not in str(ik)
        # no separate casting-floor buttons: only the three section groups
        assert set(kb.SITE_GROUP_BUTTONS.values()) == {"slab", "bloom", "billet"}
        menu_txt = {b["text"] for row in kb.site_stock_menu()["keyboard"] for b in row}
        assert not any("سطح ریخته" in t for t in menu_txt), menu_txt
        app.on_site_stock_group(msg, "slab")
        slab_bot = [it["name_desc"] for it in app._site_stock_pending["702"]["items"]]
        assert slab_bot == names["slab"] and "شرود" not in slab_bot

        # --- web: identical list + save
        from fastapi.testclient import TestClient

        import web.deps as deps
        from web.app import create_app

        deps._db = db
        wapp = create_app()
        wapp.dependency_overrides[deps.current_user] = lambda: db.get_user("702")
        wapp.dependency_overrides[deps.current_user_optional] = lambda: db.get_user("702")
        c = TestClient(wapp)
        r = c.get("/stock", params={"group": "billet"})
        assert r.status_code == 200
        pos = [r.text.find(n) for n in names["billet"]]
        assert all(p > 0 for p in pos) and pos == sorted(pos), pos
        web_ids = [it["id"] for it in ssl.items_for_group(db, "billet")]
        bot_ids = [ln["id"] for ln in res.lines["billet"]]
        assert web_ids == bot_ids
        noz_id = next(i for i in web_ids if ":1710:" in i)
        r = c.post("/stock/save", data={"group": "billet", "entry_date": "2026-10-10", f"qty_{noz_id}": "12"})
        assert r.status_code == 200 and "✅ 1 قلم" in r.text, r.text[-800:]
        ent = db.list_site_stock_entries(entry_date="2026-10-10", tundish_group="billet")
        assert ent and ent[0]["item_name_snapshot"] == "نازل 16.5" and float(ent[0]["quantity"]) == 12
        rows = db.site_stock_as_remaining_rows("2026-10-10")
        assert rows[0]["product_name"] == "نازل 16.5"
        # web: no casting-floor tab, shroud absent, concretes in their section tabs
        r = c.get("/stock", params={"group": "slab"})
        assert r.status_code == 200 and "سطح ریخته" not in r.text and "شرود" not in r.text
        assert "بتن پلی 60" in r.text
        r = c.get("/stock", params={"group": "cast_slab"})  # unknown group → falls back to slab
        assert r.status_code == 200 and "شرود" not in r.text
        assert ssl.items_for_group(db, "cast_slab") == []

        # synthetic lines stay out of warehouse catalog listings
        assert not any(ssl.is_site_line_id(it["id"]) for it in db.list_catalog_items())
        assert not any(ssl.is_site_line_id(it["id"]) for it in db.list_catalog_with_assignments())
        assert not any(ssl.is_site_line_id(it["id"]) for it in db.list_unassigned_catalog_items())

        # lists follow the source: new row appears, removed code disappears
        df2 = source_frame()
        df2 = df2[df2["category_code"] != "1678"]
        df2 = pd.concat([df2, pd.DataFrame([_row("1714", CO + "82002B", "ATOR 17.5", "نازل 17.5", "بلوم / بیلت")])])
        persist_primary_frame(db, df2.reset_index(drop=True), bale_user_id="701")
        slab = [it["name_desc"] for it in ssl.items_for_group(db, "slab")]
        billet = [it["name_desc"] for it in ssl.items_for_group(db, "billet")]
        assert "SOL/FP/S-10 , SOLAR" not in slab and "نازل 17.5" in billet, (slab, billet)
        stale = [it for it in db.list_catalog_items(active_only=False, include_site_lines=True)
                 if ":1678:" in it["id"]]
        assert stale and not stale[0]["active"]
        # unchanged source → no rewrite (signature)
        sig = db.get_setting(ssl.SIG_SETTING_KEY)
        out = ssl.sync_site_stock_lists(db, ssl.build_site_stock_lists(load_primary_frame(db)))
        assert out["changed"] is False and db.get_setting(ssl.SIG_SETTING_KEY) == sig
        # old DB (CHECK only slab/bloom/billet) is migrated in place, rows kept
        import sqlite3

        old = tmp / "old.db"
        con = sqlite3.connect(old)
        con.executescript("""
            CREATE TABLE catalog_items (id TEXT PRIMARY KEY, name_desc TEXT NOT NULL, category_code TEXT,
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE catalog_group_assignments (item_id TEXT NOT NULL PRIMARY KEY,
                tundish_group TEXT NOT NULL CHECK(tundish_group IN ('slab','bloom','billet')),
                assigned_by TEXT, updated_at TEXT NOT NULL, FOREIGN KEY(item_id) REFERENCES catalog_items(id));
            CREATE INDEX idx_catalog_assign_group ON catalog_group_assignments(tundish_group);
            INSERT INTO catalog_items VALUES ('A1','a',NULL,1,'t','t');
            INSERT INTO catalog_group_assignments VALUES ('A1','slab','x','t');
        """)
        con.close()
        odb = Database(old)
        odb.upsert_catalog_item("B1", "b")
        assert odb.get_item_assignment("A1")["tundish_group"] == "slab"
        try:  # app layer only offers slab/bloom/billet …
            odb.assign_item_to_group("B1", "cast_billet")
            raise AssertionError("cast_billet must be rejected by the app")
        except ValueError:
            pass
        with odb.connect() as cc:  # … but the DB CHECK still accepts legacy cast_* rows
            cc.execute("INSERT INTO catalog_group_assignments VALUES ('B1','cast_billet','x','t')")
        assert odb.get_item_assignment("B1")["tundish_group"] == "cast_billet"
        with odb.connect() as cc:
            assert cc.execute("SELECT 1 FROM sqlite_master WHERE name='idx_catalog_assign_group'").fetchone()
        Database(old)  # idempotent
        print("bot + web lists identical, shroud excluded, casting concretes in sections, save, "
              "sync-follow, catalog isolation, legacy-schema migration OK")
        print("SMOKE_SITE_STOCK_LISTS_OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
