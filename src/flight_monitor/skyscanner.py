"""Skyscanner 候選比價（Playwright headless Chromium；Skyscanner API 不對個人開放）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

# 攔截此子字串的 JSON 回應（未實測；對不上時用 headed + DevTools 找含 itineraries 的 XHR 後修改）
API_SUBSTR = "web-unified-search"
TIMEOUT_MS = 60_000
XHR_WAIT_MS = 25_000
_PRICE_RE = re.compile(r"NT\$\s?([\d,]+)")


@dataclass(frozen=True)
class SkyResult:
    status: str  # ok | blocked | no_data | error
    price: int | None
    url: str


def build_url(origin: str, dest: str, depart: date, ret: date) -> str:
    return (
        f"https://www.skyscanner.com.tw/transport/flights/{origin.lower()}/{dest.lower()}/"
        f"{depart:%y%m%d}/{ret:%y%m%d}/?adults=1&cabinclass=economy&rtn=1"
    )


def _raw_prices(node: Any) -> list[float]:
    """遞迴找出所有 price.raw 數值。"""
    found: list[float] = []
    if isinstance(node, dict):
        p = node.get("price")
        if isinstance(p, dict) and isinstance(p.get("raw"), (int, float)):
            found.append(float(p["raw"]))
        for v in node.values():
            found.extend(_raw_prices(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_raw_prices(v))
    return found


def _text_prices(text: str) -> list[int]:
    return [int(m.replace(",", "")) for m in _PRICE_RE.findall(text)]


def compare(origin: str, dest: str, depart: date, ret: date) -> SkyResult:
    url = build_url(origin, dest, depart, ret)
    try:
        from playwright.sync_api import sync_playwright

        api_prices: list[float] = []

        def on_response(resp) -> None:
            if API_SUBSTR in resp.url:
                try:
                    api_prices.extend(_raw_prices(resp.json()))
                except Exception:
                    pass

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(locale="zh-TW")
                page = ctx.new_page()
                page.set_default_timeout(TIMEOUT_MS)
                page.on("response", on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

                waited = 0
                while waited < XHR_WAIT_MS and not api_prices:
                    if "captcha" in page.url.lower():
                        break
                    page.wait_for_timeout(1000)
                    waited += 1000

                html = page.content()
                title = page.title()
                if "captcha" in page.url.lower() or "px-captcha" in html or "robot" in title.lower():
                    return SkyResult("blocked", None, url)
                if api_prices:
                    return SkyResult("ok", int(min(api_prices)), url)
                texts = _text_prices(page.inner_text("body"))
                if texts:
                    return SkyResult("ok", min(texts), url)
                return SkyResult("no_data", None, url)
            finally:
                browser.close()
    except Exception:
        return SkyResult("error", None, url)
