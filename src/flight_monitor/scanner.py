"""背景掃描迴圈：每輪挑一個最久沒查的 combo，查 Google、判定低價、必要時查證並推送 Telegram。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from telegram.ext import Application

from . import analyzer, google, messages, watches
from .settings import Settings

log = logging.getLogger("flight_monitor")

MAX_CONSECUTIVE_FAILURES = 5
PAUSE_SECONDS = 1800
IDLE_SECONDS = 60


@dataclass
class ScanState:
    """scanner 與 bot 指令（/status）共用的行程內狀態。"""

    consecutive_failures: int = 0
    paused_until: datetime | None = None
    was_paused: bool = False
    last_error: str = ""
    last_sky_status: str | None = None


async def _send(app: Application, s: Settings, text: str) -> None:
    await app.bot.send_message(
        s.telegram_chat_id, text, parse_mode="HTML", disable_web_page_preview=True
    )


async def scanner_loop(app: Application) -> None:
    s: Settings = app.bot_data["settings"]
    state: ScanState = app.bot_data["state"]
    log.info("scanner started")
    last_day: date | None = None

    while True:
        try:
            today = date.today()
            if today != last_day:
                for wid in await asyncio.to_thread(watches.cleanup_expired, s.fm_db, today):
                    await _send(app, s, f"監控 #{wid} 的日期已全部過去，已自動停止")
                last_day = today

            nxt = await asyncio.to_thread(watches.next_combo, s.fm_db)
            if nxt is None:
                await asyncio.sleep(IDLE_SECONDS)
                continue
            combo, origin = nxt

            try:
                g = await asyncio.to_thread(google.search, origin, combo.destination, combo.depart, combo.ret)
            except Exception as e:  # 網路、解析錯誤：不標記已查，計入連續失敗
                state.consecutive_failures += 1
                state.last_error = f"{type(e).__name__}: {e}"
                log.error("error %s: %s", combo.key, state.last_error)
                if state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    state.was_paused = True
                    state.paused_until = datetime.now() + timedelta(seconds=PAUSE_SECONDS)
                    await _send(app, s, messages.format_failure_alert(state.last_error))
                    await asyncio.sleep(PAUSE_SECONDS)
                    state.consecutive_failures = 0
                    state.paused_until = None
                else:
                    await asyncio.sleep(s.fm_scan_delay_seconds)
                continue

            await asyncio.to_thread(
                watches.save_quote, s.fm_db, combo.key, g.price, g.airlines, g.stops, g.url
            )
            await asyncio.to_thread(watches.mark_checked, s.fm_db, combo.key)
            state.consecutive_failures = 0
            if state.was_paused:
                state.was_paused = False
                await _send(app, s, messages.RECOVERED)
            log.info("checked %s price=%s", combo.key, g.price)

            if g.price is not None:
                mean = await asyncio.to_thread(analyzer.baseline, s.fm_db, combo)
                if analyzer.is_candidate(g.price, mean):
                    log.info("candidate %s mean=%.0f", combo.key, mean)
                    res = await asyncio.to_thread(analyzer.evaluate_candidate, s, combo, origin, g, mean)
                    if res is not None:
                        deal, sky = res
                        state.last_sky_status = sky.status
                        await _send(app, s, messages.format_deal(deal))
                        await asyncio.to_thread(
                            analyzer.record_notification, s.fm_db, combo, g.price, deal.main_price
                        )
                        log.info(
                            "notified %s final=%s serp=%s sky=%s",
                            combo.key, deal.main_price, deal.serp_status, sky.status,
                        )

            await asyncio.sleep(s.fm_scan_delay_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("error loop: %s: %s", type(e).__name__, e)
            await asyncio.sleep(s.fm_scan_delay_seconds)
