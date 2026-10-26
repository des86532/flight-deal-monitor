"""Telegram HTML 訊息；所有動態文字都經過 html.escape。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

_WEEK = "一二三四五六日"
_LEVEL = {"low": "偏低", "typical": "一般", "high": "偏高"}


def money(p: int) -> str:
    return f"NT$ {p:,}"


def _wd(d: date) -> str:
    return _WEEK[d.weekday()]


@dataclass(frozen=True)
class Deal:
    origin: str
    destination: str
    depart: date
    ret: date
    google_price: int
    airlines: str | None
    stops: int | None
    url: str
    serp_status: str  # ok | no_direct | skipped | error
    serp_reason: str | None = None
    official_price: int | None = None
    seller: str | None = None
    price_level: str | None = None
    sky_status: str = "error"  # ok | blocked | no_data | error
    sky_price: int | None = None
    mean: int | None = None  # None = 無基準價（probe）
    watch_id: int | None = None

    @property
    def days(self) -> int:
        return (self.ret - self.depart).days + 1

    @property
    def main_price(self) -> int:
        if self.serp_status == "ok" and self.official_price is not None:
            return self.official_price
        return self.google_price


def format_deal(d: Deal) -> str:
    head = "✈️ <b>低於行情機票</b>"
    if d.watch_id is not None:
        head += f"｜監控 #{d.watch_id}"
    lines = [
        head,
        f"{escape(d.origin)} ⇄ {escape(d.destination)}｜{d.depart:%Y/%m/%d}（{_wd(d.depart)}）"
        f"– {d.ret:%m/%d}（{_wd(d.ret)}）共 {d.days} 天",
    ]

    carrier = []
    if d.airlines:
        carrier.append(escape(d.airlines))
    if d.stops is not None:
        carrier.append("直飛" if d.stops == 0 else f"轉機 {d.stops} 次")
    if carrier:
        lines.append("｜".join(carrier))

    if d.serp_status == "ok" and d.official_price is not None:
        lines.append(f"🏷 官網價（{escape(d.seller or '航空公司')}）{money(d.official_price)}")
    elif d.serp_status == "no_direct":
        lines.append(f"🏷 {money(d.google_price)}（此行程無航空公司官網直售）")
    else:
        lines.append(f"🏷 {money(d.google_price)}（官網價未確認：{escape(d.serp_reason or '查證失敗')}）")

    sky = {
        "ok": f"Skyscanner {money(d.sky_price)}" if d.sky_price else "Skyscanner 無價格",
        "blocked": "Skyscanner 被擋，未比價",
        "no_data": "Skyscanner 查無資料",
    }.get(d.sky_status, "Skyscanner 查詢失敗")
    lines.append(f"🔎 Google 最低 {money(d.google_price)}｜{sky}")

    if d.mean:
        pct = round((1 - d.main_price / d.mean) * 100)
        stat = [f"同監控 {d.days} 天行程均價 {money(d.mean)}（低 {pct}%）"]
        if d.price_level:
            stat.append(f"Google 判定：{_LEVEL.get(d.price_level, escape(d.price_level))}")
        lines.append("📊 " + "｜".join(stat))

    lines.append(f'<a href="{escape(d.url, quote=True)}">在 Google Flights 查看</a>')
    return "\n".join(lines)


def format_failure_alert(last_error: str) -> str:
    return f"⚠️ Google Flights 連續 5 次查詢失敗，暫停 30 分鐘\n{escape(last_error)}"


RECOVERED = "✅ Google Flights 查詢已恢復"
