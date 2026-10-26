"""監控項目：指令解析、組合展開、以及 watches/combos 的資料存取。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import db
from .settings import FRESH_HOURS, MAX_ACTIVE_COMBOS

EXAMPLE = "/watch NRT,KIX 2026-12-01 2027-02-28 4-6"
_IATA = re.compile(r"^[A-Z]{3}$")
_DAYS = re.compile(r"^(\d+)(?:-(\d+))?$")


class WatchArgError(ValueError):
    """訊息直接給使用者看。"""


class WatchLimitError(ValueError):
    """組合數不合法；訊息直接給使用者看。"""


@dataclass(frozen=True)
class WatchSpec:
    origin: str
    destinations: tuple[str, ...]
    date_from: date
    date_to: date
    days_min: int
    days_max: int


@dataclass(frozen=True)
class Combo:
    key: str
    watch_id: int
    destination: str
    depart: date
    ret: date
    days: int


def _fail(msg: str) -> WatchArgError:
    return WatchArgError(f"{msg}\n範例：{EXAMPLE}")


def _parse_date(s: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise _fail(f"日期格式錯誤：{s}（只接受 YYYY-MM-DD）")
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise _fail(f"日期不存在：{s}") from None


def parse_watch_args(args: list[str], today: date, origin: str = "TPE") -> WatchSpec:
    if len(args) != 4:
        raise _fail("參數數量不對，需要：目的地 起始日 結束日 天數")
    dest_raw, from_raw, to_raw, days_raw = args

    dests = tuple(dict.fromkeys(d.strip().upper() for d in dest_raw.split(",")))
    for d in dests:
        if not _IATA.match(d):
            raise _fail(f"目的地需為 3 碼機場代碼，收到：{d or '(空)'}")

    date_from, date_to = _parse_date(from_raw), _parse_date(to_raw)
    if date_from > date_to:
        raise _fail("起始日不可晚於結束日")
    if date_to <= today:
        raise _fail("結束日必須晚於今天")

    m = _DAYS.match(days_raw)
    if not m:
        raise _fail(f"天數格式錯誤：{days_raw}（例如 5 或 4-6）")
    days_min = int(m.group(1))
    days_max = int(m.group(2) or m.group(1))
    if days_min < 2:
        raise _fail("天數至少 2 天")
    if days_min > days_max:
        raise _fail("天數範圍的最小值不可大於最大值")

    return WatchSpec(origin, dests, date_from, date_to, days_min, days_max)


def combo_key(watch_id: int, dest: str, depart: date, ret: date) -> str:
    return f"{watch_id}:{dest}:{depart.isoformat()}:{ret.isoformat()}"


def expand_combos(spec: WatchSpec, watch_id: int, today: date) -> list[Combo]:
    """整趟行程（出發到回程）都要落在 [date_from, date_to] 內，且出發日晚於今天。"""
    first = max(spec.date_from, today + timedelta(days=1))
    out: list[Combo] = []
    for dest in spec.destinations:
        for n in range(spec.days_min, spec.days_max + 1):
            d = first
            while d + timedelta(days=n - 1) <= spec.date_to:
                ret = d + timedelta(days=n - 1)
                out.append(Combo(combo_key(watch_id, dest, d, ret), watch_id, dest, d, ret, n))
                d += timedelta(days=1)
    return out


# --- 資料存取 ---------------------------------------------------------------


def active_combo_count(path: str) -> int:
    with db.connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM combos c JOIN watches w ON w.id=c.watch_id WHERE w.active=1"
        ).fetchone()
    return row["n"]


def add_watch(path: str, spec: WatchSpec, today: date, now: datetime | None = None) -> tuple[int, int]:
    """建立 watch 與其 combos，回傳 (watch_id, combo 數)。超出上限或沒有組合時拋 WatchLimitError。"""
    with db.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur_n = conn.execute(
            "SELECT COUNT(*) AS n FROM combos c JOIN watches w ON w.id=c.watch_id WHERE w.active=1"
        ).fetchone()["n"]
        # 先用暫時 id 0 算數量，確認不超限再寫入
        new_n = len(expand_combos(spec, 0, today))
        if new_n == 0:
            raise WatchLimitError("這個範圍沒有可查的日期組合")
        if cur_n + new_n > MAX_ACTIVE_COMBOS:
            raise WatchLimitError(
                f"組合數 {new_n}，加上現有 {cur_n} 超過上限 {MAX_ACTIVE_COMBOS}，請縮小日期或天數範圍"
            )
        cur = conn.execute(
            "INSERT INTO watches(origin, destinations, date_from, date_to, days_min, days_max, active, created_at)"
            " VALUES(?,?,?,?,?,?,1,?)",
            (
                spec.origin,
                ",".join(spec.destinations),
                spec.date_from.isoformat(),
                spec.date_to.isoformat(),
                spec.days_min,
                spec.days_max,
                db.now_iso(now),
            ),
        )
        wid = cur.lastrowid
        conn.executemany(
            "INSERT INTO combos(key, watch_id, destination, depart, ret, days, last_checked_at) VALUES(?,?,?,?,?,?,NULL)",
            [
                (c.key, c.watch_id, c.destination, c.depart.isoformat(), c.ret.isoformat(), c.days)
                for c in expand_combos(spec, wid, today)
            ],
        )
    return wid, new_n


def stop_watch(path: str, watch_id: int) -> bool:
    with db.connect(path) as conn:
        cur = conn.execute("UPDATE watches SET active=0 WHERE id=?", (watch_id,))
    return cur.rowcount > 0


def recheck_watch(path: str, watch_id: int) -> int | None:
    """把該 watch 所有 combo 設為立即重查；watch 不存在回傳 None。"""
    with db.connect(path) as conn:
        if not conn.execute("SELECT 1 FROM watches WHERE id=?", (watch_id,)).fetchone():
            return None
        cur = conn.execute("UPDATE combos SET last_checked_at=NULL WHERE watch_id=?", (watch_id,))
    return cur.rowcount


def cleanup_expired(path: str, today: date) -> list[int]:
    """刪除 depart <= today 的 combos；沒有任何 combo 的 active watch 設為 inactive，回傳這些 watch id。"""
    with db.connect(path) as conn:
        conn.execute("DELETE FROM combos WHERE depart <= ?", (today.isoformat(),))
        rows = conn.execute(
            "SELECT id FROM watches w WHERE active=1 AND NOT EXISTS (SELECT 1 FROM combos c WHERE c.watch_id=w.id)"
        ).fetchall()
        ids = [r["id"] for r in rows]
        conn.executemany("UPDATE watches SET active=0 WHERE id=?", [(i,) for i in ids])
    return ids


def _combo_from_row(r) -> Combo:
    return Combo(
        r["key"], r["watch_id"], r["destination"],
        date.fromisoformat(r["depart"]), date.fromisoformat(r["ret"]), r["days"],
    )


def next_combo(path: str, now: datetime | None = None) -> tuple[Combo, str] | None:
    """下一個要查的 combo（含該 watch 的 origin）；沒有就回傳 None。"""
    now = now or datetime.now()
    cutoff = db.now_iso(now - timedelta(hours=FRESH_HOURS))
    with db.connect(path) as conn:
        r = conn.execute(
            "SELECT c.*, w.origin AS origin FROM combos c JOIN watches w ON w.id=c.watch_id "
            "WHERE w.active=1 AND (c.last_checked_at IS NULL OR c.last_checked_at < ?) "
            "ORDER BY c.last_checked_at IS NOT NULL, c.last_checked_at, c.depart LIMIT 1",
            (cutoff,),
        ).fetchone()
    return (_combo_from_row(r), r["origin"]) if r else None


def mark_checked(path: str, key: str, now: datetime | None = None) -> None:
    with db.connect(path) as conn:
        conn.execute("UPDATE combos SET last_checked_at=? WHERE key=?", (db.now_iso(now), key))


def pending_count(path: str, now: datetime | None = None) -> int:
    cutoff = db.now_iso((now or datetime.now()) - timedelta(hours=FRESH_HOURS))
    with db.connect(path) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM combos c JOIN watches w ON w.id=c.watch_id "
            "WHERE w.active=1 AND (c.last_checked_at IS NULL OR c.last_checked_at < ?)",
            (cutoff,),
        ).fetchone()["n"]


def save_quote(
    path: str, key: str, price: int | None, airlines: str | None, stops: int | None, url: str,
    now: datetime | None = None,
) -> None:
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO quotes(combo_key, price, airlines, stops, url, checked_at) VALUES(?,?,?,?,?,?)",
            (key, price, airlines, stops, url, db.now_iso(now)),
        )


def list_active_watches(path: str) -> list[dict]:
    """每個 active watch 的摘要：combo 數、已查數、目前最低價那一組。"""
    with db.connect(path) as conn:
        out = []
        for w in conn.execute("SELECT * FROM watches WHERE active=1 ORDER BY id").fetchall():
            total = conn.execute("SELECT COUNT(*) AS n FROM combos WHERE watch_id=?", (w["id"],)).fetchone()["n"]
            checked = conn.execute(
                "SELECT COUNT(*) AS n FROM combos WHERE watch_id=? AND last_checked_at IS NOT NULL", (w["id"],)
            ).fetchone()["n"]
            out.append({"watch": dict(w), "total": total, "checked": checked, "cheapest": _cheapest(conn, w["id"], 1)})
    for o in out:
        o["cheapest"] = o["cheapest"][0] if o["cheapest"] else None
    return out


def _cheapest(conn, watch_id: int, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT c.key, c.destination, c.depart, c.ret, q.price, q.airlines, q.stops, q.url FROM combos c "
        "JOIN quotes q ON q.combo_key=c.key AND q.id=(SELECT MAX(id) FROM quotes WHERE combo_key=c.key) "
        "WHERE c.watch_id=? AND q.price IS NOT NULL ORDER BY q.price, c.depart LIMIT ?",
        (watch_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def top_quotes(path: str, watch_id: int, limit: int = 5) -> list[dict] | None:
    """watch 不存在回傳 None；否則回傳最新報價最便宜的幾組。"""
    with db.connect(path) as conn:
        if not conn.execute("SELECT 1 FROM watches WHERE id=?", (watch_id,)).fetchone():
            return None
        return _cheapest(conn, watch_id, limit)
