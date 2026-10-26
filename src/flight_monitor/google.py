"""Google Flights 全面掃描（fast-flights 取 HTML，免費）。

fast-flights 3.1 的 parser 只讀 payload[3][0]（「其他航班」），漏掉 payload[2][0]（頁面最上方「最佳」航班，
常含最低價），所以這裡自己解析兩個群組。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query
from fast_flights.fetcher import fetch_flights_html
from selectolax.lexbor import LexborHTMLParser


@dataclass(frozen=True)
class GoogleResult:
    price: int | None  # None = 當天無航班
    airlines: str | None
    stops: int | None
    url: str


def parse_html(html: str) -> list[tuple[int, str, int]]:
    """回傳 [(來回總價, 航空公司, 轉機次數)]；頁面明確表示無航班時拋 FlightsNotFound，結構不符則拋例外。"""
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ValueError("Google Flights 回應缺少 script.ds:1（可能被擋或頁面改版）")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")
    payload = json.loads(data)
    if not payload:  # 無效機場代碼等：Google 回傳空 payload
        raise FlightsNotFound("empty payload")

    out: list[tuple[int, str, int]] = []
    for group in (payload[2], payload[3]):  # 最佳航班、其他航班
        if not group or not group[0]:
            continue
        for k in group[0]:
            flight = k[0]
            price = k[1][0][1]
            if price:
                out.append((int(price), ", ".join(flight[1]), max(len(flight[2]) - 1, 0)))
    return out


def search(origin: str, dest: str, depart: date, ret: date) -> GoogleResult:
    """來回最低總價。無航班回傳 price=None；其他例外（網路、解析）往外拋，由 scanner 計入連續失敗。"""
    q = create_query(
        flights=[
            FlightQuery(date=depart.isoformat(), from_airport=origin, to_airport=dest),
            FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin),
        ],
        seat="economy",
        trip="round-trip",
        passengers=Passengers(adults=1),
        language="zh-TW",
        currency="TWD",
    )
    url = q.url()
    try:
        flights = parse_html(fetch_flights_html(q))
    except FlightsNotFound:
        return GoogleResult(None, None, None, url)
    if not flights:
        return GoogleResult(None, None, None, url)
    price, airlines, stops = min(flights, key=lambda f: f[0])
    return GoogleResult(price, airlines, stops, url)
