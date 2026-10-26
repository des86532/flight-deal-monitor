"""低於行情判定、候選查證與通知節流。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from . import db, serp, skyscanner
from .google import GoogleResult
from .messages import Deal
from .settings import Settings
from .watches import Combo

log = logging.getLogger("flight_monitor")

DISCOUNT = 0.15
MIN_SAMPLES = 5
BASELINE_DAYS = 30
RENOTIFY_DROP = 500
MAX_NOTIFY_PER_WATCH_24H = 3


def baseline(path: str, combo: Combo, now: datetime | None = None) -> float | None:
    """同 watch、同目的地、同天數、30 天內、每個 combo 最新一筆有價格的報價平均；樣本不足回傳 None。"""
    since = db.now_iso((now or datetime.now()) - timedelta(days=BASELINE_DAYS))
    with db.connect(path) as conn:
        rows = conn.execute(
            "SELECT q.price FROM quotes q JOIN combos c ON c.key=q.combo_key "
            "WHERE c.watch_id=? AND c.destination=? AND c.days=? AND c.key!=? "
            "AND q.price IS NOT NULL AND q.checked_at>=? "
            "AND q.id=(SELECT MAX(id) FROM quotes q2 WHERE q2.combo_key=q.combo_key "
            "          AND q2.price IS NOT NULL AND q2.checked_at>=?)",
            (combo.watch_id, combo.destination, combo.days, combo.key, since, since),
        ).fetchall()
    if len(rows) < MIN_SAMPLES:
        return None
    return sum(r["price"] for r in rows) / len(rows)


def is_candidate(price: int, mean: float | None) -> bool:
    return mean is not None and price <= mean * (1 - DISCOUNT)


def cooldown_ok(path: str, key: str, google_price: int) -> bool:
    """同一 combo 通知過的話，這次 Google 價必須比上次低至少 RENOTIFY_DROP。"""
    with db.connect(path) as conn:
        row = conn.execute(
            "SELECT google_price FROM notifications WHERE combo_key=? ORDER BY id DESC LIMIT 1", (key,)
        ).fetchone()
    return row is None or google_price <= row["google_price"] - RENOTIFY_DROP


def throttle_ok(path: str, watch_id: int, now: datetime | None = None) -> bool:
    since = db.now_iso((now or datetime.now()) - timedelta(hours=24))
    with db.connect(path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE watch_id=? AND sent_at>=?", (watch_id, since)
        ).fetchone()["n"]
    return n < MAX_NOTIFY_PER_WATCH_24H


def record_notification(
    path: str, combo: Combo, google_price: int, final_price: int, now: datetime | None = None
) -> None:
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO notifications(combo_key, watch_id, google_price, final_price, sent_at) VALUES(?,?,?,?,?)",
            (combo.key, combo.watch_id, google_price, final_price, db.now_iso(now)),
        )


def evaluate_candidate(
    s: Settings,
    combo: Combo,
    origin: str,
    g: GoogleResult,
    mean: float,
    *,
    now: datetime | None = None,
    verify: Callable[..., serp.Verification] = serp.verify,
    compare: Callable[..., skyscanner.SkyResult] = skyscanner.compare,
) -> tuple[Deal, skyscanner.SkyResult] | None:
    """依序檢查冷卻、節流，再查證與比價；需要通知時回傳 Deal（呼叫端負責發送並 record_notification）。"""
    assert g.price is not None
    path = s.fm_db
    if not cooldown_ok(path, combo.key, g.price):
        return None
    if not throttle_ok(path, combo.watch_id, now):
        return None

    v = verify(s, origin, combo.destination, combo.depart, combo.ret, now=now)
    sky = compare(origin, combo.destination, combo.depart, combo.ret)

    if v.status == "ok":
        assert v.official_price is not None
        if not (v.official_price <= mean * (1 - DISCOUNT) or v.price_level == "low"):
            log.info("official not cheap %s", combo.key)
            return None

    deal = Deal(
        origin=origin,
        destination=combo.destination,
        depart=combo.depart,
        ret=combo.ret,
        google_price=g.price,
        airlines=g.airlines,
        stops=g.stops,
        url=v.url if v.status == "ok" and v.url else g.url,
        serp_status=v.status,
        serp_reason=v.reason,
        official_price=v.official_price,
        seller=v.seller,
        price_level=v.price_level,
        sky_status=sky.status,
        sky_price=sky.price,
        mean=round(mean),
        watch_id=combo.watch_id,
    )
    return deal, sky
