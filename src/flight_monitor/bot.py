"""Telegram 指令介面（python-telegram-bot Application）。"""

from __future__ import annotations

import asyncio
from datetime import date
from html import escape

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from . import db, messages, watches
from .scanner import ScanState, scanner_loop
from .settings import Settings

HELP = """✈️ <b>機票特價降價監控小幫手</b>

<b>💡 快速上手範例（點擊指令可直接複製）：</b>
• 東京玩 5 天：
<code>/watch NRT 2026-11-01 2026-11-30 5</code>
• 東京或大阪彈性 4～6 天：
<code>/watch NRT,KIX 2026-12-01 2027-01-15 4-6</code>
• 沖繩度假 4 天：
<code>/watch OKA 2026-11-10 2026-11-25 4</code>

<b>📖 指令與使用教學：</b>
• <b>/watch &lt;機場&gt; &lt;起始日&gt; &lt;結束日&gt; &lt;天數&gt;</b> — 新增監控
  └ 機場代碼可填單一或多個（逗號分隔，例如 <code>NRT,KIX</code>）
  └ 系統會展開符合區間的所有出發與回程組合，自動定時爬取
  └ 一旦偵測到顯著降價或歷史低價，會立即發送通知與訂票連結
• <b>/list</b> — 查看目前所有生效的監控項目與最新最低價
• <b>/top &lt;編號&gt;</b> — 查看該監控目前最便宜的 5 組日期與航班連結
• <b>/check &lt;編號&gt;</b> — 立即將該監控插隊優先重查
• <b>/stop &lt;編號&gt;</b> — 停止並刪除該監控
• <b>/status</b> — 查看背景爬蟲狀態、待查組合數與 API 額度
• <b>/help</b> — 再次顯示本教學與範例

<b>🏷 常用機場代碼：</b>
• <b>日本</b>：東京成田 <code>NRT</code>、羽田 <code>HND</code>、大阪關西 <code>KIX</code>、福岡 <code>FUK</code>、沖繩 <code>OKA</code>、札幌 <code>CTS</code>、名古屋 <code>NGO</code>
• <b>韓國</b>：首爾仁川 <code>ICN</code>、釜山 <code>PUS</code>
• <b>東南亞</b>：曼谷 <code>BKK</code>、新加坡 <code>SIN</code>、峴港 <code>DAD</code>"""


def _days_text(w: dict) -> str:
    return str(w["days_min"]) if w["days_min"] == w["days_max"] else f"{w['days_min']}–{w['days_max']}"


def _md(s: str) -> str:
    return date.fromisoformat(s).strftime("%m/%d")


def _watch_id(args: list[str]) -> int | None:
    a = args[0].lstrip("#") if len(args) == 1 else ""
    return int(a) if a.isdigit() else None


def build_app(s: Settings) -> Application:
    async def post_init(app: Application) -> None:
        app.bot_data["scanner_task"] = asyncio.create_task(scanner_loop(app))

    async def post_shutdown(app: Application) -> None:
        task: asyncio.Task = app.bot_data["scanner_task"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    app = Application.builder().token(s.telegram_bot_token).post_init(post_init).post_shutdown(post_shutdown).build()
    app.bot_data["settings"] = s
    app.bot_data["state"] = ScanState()
    path = s.fm_db
    only_owner = filters.Chat(chat_id=s.telegram_chat_id)

    async def reply(update: Update, text: str, html: bool = False) -> None:
        await update.effective_message.reply_text(
            text, parse_mode="HTML" if html else None, disable_web_page_preview=True
        )

    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, HELP, html=True)

    async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        today = date.today()
        try:
            spec = watches.parse_watch_args(ctx.args or [], today, s.fm_origin)
            wid, n = watches.add_watch(path, spec, today)
        except (watches.WatchArgError, watches.WatchLimitError) as e:
            await reply(update, str(e))
            return
        days = f"{spec.days_min}" if spec.days_min == spec.days_max else f"{spec.days_min}–{spec.days_max}"
        mins = n * s.fm_scan_delay_seconds / 60
        await reply(
            update,
            f"已新增監控 #{wid}：{spec.origin} → {','.join(spec.destinations)}，"
            f"{spec.date_from}～{spec.date_to}，{days} 天，共 {n} 組，約 {mins:.0f} 分鐘掃完第一輪",
        )

    async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        items = watches.list_active_watches(path)
        if not items:
            await reply(update, "目前沒有監控，用 /watch 新增")
            return
        lines = []
        for it in items:
            w = it["watch"]
            line = (
                f"#{w['id']} {w['destinations']} {w['date_from']}～{w['date_to']} {_days_text(w)}天"
                f"｜{it['total']} 組，已查 {it['checked']}"
            )
            c = it["cheapest"]
            if c:
                line += (
                    f"｜最低 {messages.money(c['price'])}"
                    f"（{c['destination']} {_md(c['depart'])}–{_md(c['ret'])} {c['airlines'] or ''}）"
                )
            lines.append(line)
        await reply(update, "\n".join(lines))

    async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        wid = _watch_id(ctx.args or [])
        if wid is None:
            await reply(update, "用法：/top <id>")
            return
        rows = watches.top_quotes(path, wid, 5)
        if rows is None:
            await reply(update, f"找不到監控 #{wid}")
        elif not rows:
            await reply(update, f"監控 #{wid} 還沒有報價")
        else:
            lines = [
                f"{escape(r['destination'])} {_md(r['depart'])}–{_md(r['ret'])} <b>{messages.money(r['price'])}</b> "
                f"{escape(r['airlines'] or '')} <a href=\"{escape(r['url'], quote=True)}\">Google Flights</a>"
                for r in rows
            ]
            await reply(update, f"監控 #{wid} 最便宜 {len(rows)} 組\n" + "\n".join(lines), html=True)

    async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        wid = _watch_id(ctx.args or [])
        if wid is None:
            await reply(update, "用法：/stop <id>")
        elif watches.stop_watch(path, wid):
            await reply(update, f"已停止監控 #{wid}")
        else:
            await reply(update, f"找不到監控 #{wid}")

    async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        wid = _watch_id(ctx.args or [])
        if wid is None:
            await reply(update, "用法：/check <id>")
            return
        n = watches.recheck_watch(path, wid)
        await reply(update, f"找不到監控 #{wid}" if n is None else f"監控 #{wid} 的 {n} 組已排到最前面重查")

    async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        st: ScanState = app.bot_data["state"]
        today = date.today().isoformat()
        _, verifs = db.usage_today(path, today)
        paused = f"是（至 {st.paused_until:%H:%M}）" if st.paused_until else "否"
        await reply(
            update,
            f"待查組合（超過 6 小時未查）：{watches.pending_count(path)}\n"
            f"Google 連續失敗：{st.consecutive_failures}｜暫停中：{paused}\n"
            f"SerpApi 今日查證 {verifs}/{s.fm_serpapi_daily_verify}｜"
            f"本月呼叫 {db.month_calls(path, today)}/{s.fm_serpapi_monthly_limit}\n"
            f"最近一次 Skyscanner：{st.last_sky_status or '尚未比價'}",
        )

    for name, fn in [
        ("start", cmd_help), ("help", cmd_help), ("watch", cmd_watch), ("list", cmd_list),
        ("top", cmd_top), ("stop", cmd_stop), ("check", cmd_check), ("status", cmd_status),
    ]:
        app.add_handler(CommandHandler(name, fn, filters=only_owner))
    return app
