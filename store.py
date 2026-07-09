"""
store.py - tiny SQLite database remembering which listings the bot has seen
and alerted on, so you don't get pinged twice for the same item.

notify-side: the WhatsApp sender lives here too to keep the file count down.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

import requests

DB_FILE = "seen_items.db"


def _conn():
    c = sqlite3.connect(DB_FILE)
    c.execute(
        """CREATE TABLE IF NOT EXISTS items (
               item_id TEXT PRIMARY KEY,
               source TEXT,
               title TEXT,
               first_seen TEXT,
               last_seen TEXT,
               last_price INTEGER,
               alerted_price INTEGER
           )"""
    )
    return c


def upsert_seen(item_id: str, source: str, title: str, price: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            """INSERT INTO items (item_id, source, title, first_seen, last_seen, last_price)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET last_seen=?, last_price=?""",
            (item_id, source, title, now, now, price, now, price),
        )


def prune_stale(days: int = 90) -> int:
    """Forget listings not seen for `days` days (long sold/removed) so the
    database doesn't grow forever. Timestamps are UTC ISO, so plain string
    comparison against SQLite's datetime('now') works."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM items WHERE last_seen < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        return cur.rowcount


def alerted_price(item_id: str) -> Optional[int]:
    with _conn() as c:
        row = c.execute("SELECT alerted_price FROM items WHERE item_id=?", (item_id,)).fetchone()
    return row[0] if row else None


def mark_alerted(item_id: str, price: int) -> None:
    with _conn() as c:
        c.execute("UPDATE items SET alerted_price=? WHERE item_id=?", (price, item_id))


def should_alert(item_id: str, price: int, realert_drop_pct: float) -> bool:
    prev = alerted_price(item_id)
    if prev is None:
        return True
    return price <= prev * (1 - realert_drop_pct / 100.0)


# ----------------------------------------------------------------------------
# WhatsApp (via CallMeBot's free personal-use API - see README step C)
# ----------------------------------------------------------------------------

_last_send = [0.0]     # CallMeBot is rate-limited; space messages politely


def whatsapp_send(cfg: dict, text: str) -> bool:
    w = cfg.get("whatsapp", {})
    if not w.get("enabled"):
        return False
    phone, apikey = str(w.get("phone", "")), str(w.get("apikey", ""))
    if "PASTE" in phone or "PASTE" in apikey or not phone or not apikey:
        print("  [whatsapp] not configured - fill whatsapp section of config.yaml")
        return False
    gap = time.time() - _last_send[0]
    if gap < 3:
        time.sleep(3 - gap)
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": text, "apikey": apikey},
            timeout=30,
        )
        _last_send[0] = time.time()
        ok = r.status_code == 200 and "ERROR" not in r.text.upper()[:400]
        if not ok:
            print(f"  [whatsapp] send failed (HTTP {r.status_code}): {r.text[:200]}")
        return bool(ok)
    except Exception as e:
        print(f"  [whatsapp] send failed: {e}")
        return False
