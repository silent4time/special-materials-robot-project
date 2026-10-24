"""Smoke (v1.3.0, temp DB only): no duplicate reply per button.

• every reply-keyboard button reachable from the owner's main menu (2 levels) sends at
  most ONE message carrying a reply keyboard (no repeated menus);
• «📝 ثبت تعداد تاندیش ماهانه»: each step (prompt, number, invalid, cancel, back) = 1 message,
  number prompts carry only the compact cancel keyboard, back never shows «سال None»;
• a re-delivered update (same update_id) is ignored; a queued replay is not."""
from __future__ import annotations

import _smoke_env  # noqa: F401,E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from smoke_phase1 import FakeClient

    from bot import keyboards as kb
    from bot.handlers import BotApp
    from db.models import Database
    from services import permissions as perm

    db = Database()
    perm.bind(db)
    db.upsert_user("9911", "owner", display_name="مالک آزمون")
    c = FakeClient()
    app = BotApp(c, db)
    upd = iter(range(5000, 99999))

    def send(text: str, uid: int | None = None) -> list:
        n = len(c.sent)
        app.handle_update({"update_id": uid if uid is not None else next(upd),
                           "message": {"from": {"id": 9911, "first_name": "x"},
                                       "chat": {"id": 9911}, "text": text}})
        app.bg.wait_idle(60) if hasattr(app.bg, "wait_idle") else None
        return c.sent[n:]

    def menus(out: list) -> int:
        return sum(1 for x in out if kb.keyboard_texts(x[2]))

    # 1) critical counts flow
    send(kb.BTN_HOME)
    send(kb.BTN_CRITICAL_ITEMS)
    for t in (kb.BTN_CRITICAL_COUNTS, "x", "70", kb.BTN_BACK_PREV, "70", "34", "0"):
        out = send(t)
        assert len(out) == 1, (t, [o[1] for o in out])
        assert "None" not in out[0][1], out[0][1]
    for t in (kb.BTN_CRITICAL_COUNTS, kb.BTN_CANCEL, kb.BTN_CRITICAL_COUNTS):
        out = send(t)
        assert len(out) == 1, (t, [o[1] for o in out])
    prompt = c.sent[-1]
    assert kb.keyboard_texts(prompt[2]) == kb.keyboard_texts(kb.critical_counts_entry_menu()), prompt
    send(kb.BTN_HOME)

    # 2) update_id de-duplication
    out1 = send(kb.BTN_HOME, uid=42)
    out2 = send(kb.BTN_HOME, uid=42)
    assert len(out1) >= 1 and out2 == [], (out1, out2)
    n = len(c.sent)
    app._replay_update({"update_id": 42, "message": {"from": {"id": 9911, "first_name": "x"},
                                                      "chat": {"id": 9911}, "text": kb.BTN_HOME}})
    assert len(c.sent) > n, "queued replay must not be dropped"

    # 3) every button (2 levels) → ≤1 reply-keyboard message
    root = kb.keyboard_texts(kb.main_menu(db.get_user("9911") if hasattr(db, "get_user") else True))
    skip = {kb.BTN_HOME, kb.BTN_CANCEL, kb.BTN_BACK_PREV}
    checked = 0
    for b1 in root:
        if b1 in skip:
            continue
        send(kb.BTN_HOME)
        out = send(b1)
        assert menus(out) <= 1, (b1, [o[1][:60] for o in out])
        checked += 1
        subs = [t for o in out for t in kb.keyboard_texts(o[2])]
        for b2 in subs:
            if b2 in skip or b2 in root:
                continue
            send(kb.BTN_HOME)
            send(b1)
            out = send(b2)
            assert menus(out) <= 1, (b1, b2, [o[1][:60] for o in out])
            checked += 1
    send(kb.BTN_HOME)
    print(f"smoke_no_duplicate_reply: OK ({checked} buttons)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
