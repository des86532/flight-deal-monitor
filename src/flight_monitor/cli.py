"""flight-monitor bot | probe。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date

from . import db, google, messages, serp, skyscanner
from .settings import load_settings


def _setup_logging() -> None:
    # root 維持 WARNING：httpx 的 INFO log 會印出含 bot token 的 URL，fast-flights/primp 也很吵
    logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format="%(asctime)s %(message)s", force=True)
    logging.getLogger("flight_monitor").setLevel(logging.INFO)


def run_bot() -> None:
    from .bot import build_app

    s = load_settings()
    db.init_db(s.fm_db)
    build_app(s).run_polling(drop_pending_updates=True)


def run_probe(dest: str, depart: date, ret: date, send: bool) -> None:
    s = load_settings()
    db.init_db(s.fm_db)
    origin = s.fm_origin
    dest = dest.upper()

    g = google.search(origin, dest, depart, ret)
    print(f"google: price={g.price} airlines={g.airlines} stops={g.stops}\n  {g.url}")
    v = serp.verify(s, origin, dest, depart, ret, ignore_daily_limit=True)
    print(
        f"serp: status={v.status} official={v.official_price} seller={v.seller} "
        f"level={v.price_level} reason={v.reason}\n  {v.url}"
    )
    sky = skyscanner.compare(origin, dest, depart, ret)
    print(f"skyscanner: status={sky.status} price={sky.price}\n  {sky.url}")

    if send:
        if g.price is None:
            print("Google 無價格，不發送")
            return
        deal = messages.Deal(
            origin=origin, destination=dest, depart=depart, ret=ret,
            google_price=g.price, airlines=g.airlines, stops=g.stops,
            url=v.url if v.status == "ok" and v.url else g.url,
            serp_status=v.status, serp_reason=v.reason, official_price=v.official_price,
            seller=v.seller, price_level=v.price_level, sky_status=sky.status, sky_price=sky.price,
        )

        async def go() -> None:
            from telegram import Bot

            async with Bot(s.telegram_bot_token) as bot:
                await bot.send_message(
                    s.telegram_chat_id, messages.format_deal(deal),
                    parse_mode="HTML", disable_web_page_preview=True,
                )

        asyncio.run(go())
        print("已發送到 Telegram")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="flight-monitor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bot", help="啟動 Telegram bot 與背景掃描")
    p = sub.add_parser("probe", help="對單一行程跑一次 google/serp/skyscanner（serp 會消耗 3 次額度）")
    p.add_argument("dest")
    p.add_argument("depart", type=date.fromisoformat)
    p.add_argument("ret", type=date.fromisoformat)
    p.add_argument("--send", action="store_true", help="把結果以特價通知格式發送到 Telegram")
    a = ap.parse_args(argv)

    _setup_logging()
    if a.cmd == "bot":
        run_bot()
    else:
        run_probe(a.dest, a.depart, a.ret, a.send)
