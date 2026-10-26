"""SQLite（stdlib）。scanner 執行緒與 bot event loop 同時存取，所以每次操作開新連線。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  origin TEXT NOT NULL, destinations TEXT NOT NULL,
  date_from TEXT NOT NULL, date_to TEXT NOT NULL,
  days_min INTEGER NOT NULL, days_max INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS combos (
  key TEXT PRIMARY KEY,
  watch_id INTEGER NOT NULL, destination TEXT NOT NULL,
  depart TEXT NOT NULL, ret TEXT NOT NULL, days INTEGER NOT NULL,
  last_checked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_combos_watch ON combos(watch_id);
CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, combo_key TEXT NOT NULL,
  price INTEGER, airlines TEXT, stops INTEGER, url TEXT NOT NULL,
  checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotes_combo ON quotes(combo_key, id);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, combo_key TEXT NOT NULL, watch_id INTEGER NOT NULL,
  google_price INTEGER NOT NULL, final_price INTEGER NOT NULL, sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS serpapi_usage (
  day TEXT PRIMARY KEY, calls INTEGER NOT NULL, verifications INTEGER NOT NULL
);
"""


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


@contextmanager
def connect(path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: str) -> None:
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


# --- SerpApi 用量 -----------------------------------------------------------


def usage_today(path: str, today: str) -> tuple[int, int]:
    """回傳今日 (calls, verifications)。"""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT calls, verifications FROM serpapi_usage WHERE day=?", (today,)
        ).fetchone()
    return (row["calls"], row["verifications"]) if row else (0, 0)


def month_calls(path: str, today: str) -> int:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT SUM(calls) AS n FROM serpapi_usage WHERE day LIKE ?", (today[:7] + "-%",)
        ).fetchone()
    return row["n"] or 0


def _bump(path: str, today: str, calls: int, verifications: int) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO serpapi_usage(day, calls, verifications) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET calls=calls+excluded.calls, "
            "verifications=verifications+excluded.verifications",
            (today, calls, verifications),
        )


def add_serp_call(path: str, today: str) -> None:
    _bump(path, today, 1, 0)


def add_serp_verification(path: str, today: str) -> None:
    _bump(path, today, 0, 1)
