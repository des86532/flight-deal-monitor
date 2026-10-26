"""官網直售價查證（SerpApi google_flights，每次查證 3 次呼叫）。

1. 搜尋 → price_insights.price_level + 最低價航班的 departure_token
2. departure_token → 最低價回程的 booking_token
3. booking_token → booking_options；取 airline=true 且非 separate_tickets 的最低價
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from . import db
from .settings import Settings

ENDPOINT = "https://serpapi.com/search.json"


@dataclass(frozen=True)
class Verification:
    status: str  # ok | no_direct | skipped | error
    official_price: int | None = None
    seller: str | None = None
    price_level: str | None = None
    url: str | None = None
    reason: str | None = None


class _SerpError(Exception):
    pass


class _QuotaReached(Exception):
    pass


def _cheapest(data: dict[str, Any], token_key: str) -> dict[str, Any] | None:
    items = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    items = [i for i in items if i.get("price") is not None and i.get(token_key)]
    return min(items, key=lambda i: i["price"]) if items else None


def verify(
    s: Settings,
    origin: str,
    dest: str,
    depart: date,
    ret: date,
    *,
    ignore_daily_limit: bool = False,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> Verification:
    today = (now or datetime.now()).date().isoformat()
    path = s.fm_db

    _, verifs = db.usage_today(path, today)
    if not ignore_daily_limit and verifs >= s.fm_serpapi_daily_verify:
        return Verification("skipped", reason="今日查證額度已用完")
    if db.month_calls(path, today) >= s.fm_serpapi_monthly_limit:
        return Verification("skipped", reason="本月 SerpApi 額度已用完")

    base = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": depart.isoformat(),
        "return_date": ret.isoformat(),
        "type": 1,
        "currency": "TWD",
        "hl": "zh-TW",
        "gl": "tw",
        "api_key": s.serpapi_api_key,
    }
    own = client is None
    http = client or httpx.Client(timeout=30)

    def call(extra: dict[str, Any]) -> dict[str, Any]:
        if db.month_calls(path, today) >= s.fm_serpapi_monthly_limit:
            raise _QuotaReached
        db.add_serp_call(path, today)  # 真正送出請求就計一次
        try:
            r = http.get(ENDPOINT, params={**base, **extra}, timeout=30)
        except httpx.HTTPError as e:
            raise _SerpError(f"SerpApi 連線失敗：{e}") from e
        if not r.is_success:
            raise _SerpError(f"SerpApi HTTP {r.status_code}")
        data = r.json()
        if data.get("error"):
            raise _SerpError(str(data["error"]))
        return data

    db.add_serp_verification(path, today)
    try:
        d1 = call({})
        level = (d1.get("price_insights") or {}).get("price_level")
        out = _cheapest(d1, "departure_token")
        if not out:
            raise _SerpError("第 1 步缺少 departure_token")

        d2 = call({"departure_token": out["departure_token"]})
        back = _cheapest(d2, "booking_token")
        if not back:
            raise _SerpError("第 2 步缺少 booking_token")

        d3 = call({"booking_token": back["booking_token"]})
        direct = []
        for opt in d3.get("booking_options") or []:
            t = opt.get("together") or {}
            if not opt.get("separate_tickets") and t.get("airline") is True and t.get("price") is not None:
                direct.append(t)
        url = (d3.get("search_metadata") or {}).get("google_flights_url")
        if not direct:
            return Verification("no_direct", price_level=level, url=url)
        best = min(direct, key=lambda t: t["price"])
        return Verification(
            "ok", official_price=int(best["price"]), seller=best.get("book_with"), price_level=level, url=url
        )
    except _QuotaReached:
        return Verification("skipped", reason="本月 SerpApi 額度已用完")
    except _SerpError as e:
        return Verification("error", reason=str(e))
    finally:
        if own:
            http.close()
