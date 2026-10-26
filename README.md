# Flight Deal Monitor

常駐背景掃描機票、發現低於行情的特價就主動推送 Telegram；監控項目可隨時用 Telegram 指令調整。

- **Google Flights**：fast-flights 免費爬取，負責全面掃描（每 20 秒一組，每組 6 小時內不重查）。
- **航空公司官網價**：候選特價才用 SerpApi 讀取 Google Flights「訂位選項」中航空公司直售價（每次查證 3 次呼叫）。
- **Skyscanner**：候選特價才用 Playwright 比價（Skyscanner API 不對個人開放）；被擋時通知仍會送出。
- 低於行情 = 同監控、同目的地、同天數、30 天內其他日期的平均價再低 15% 以上（至少 5 筆樣本）。不需設定價格門檻。

## 設定

1. 在 @BotFather 建立 bot，取得 token。
2. 傳一則訊息給 bot，再開啟 `https://api.telegram.org/bot<token>/getUpdates`，從回應的 `message.chat.id` 取得 chat_id。
3. 註冊 [SerpApi](https://serpapi.com) 免費帳號，取得 API key（250 次/月）。
4. 填寫 `/Volumes/G2/flight-deal-monitor/secrets.env`：

   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   SERPAPI_API_KEY=...
   # 可選：FM_ORIGIN=TPE  FM_SCAN_DELAY_SECONDS=20  FM_SERPAPI_DAILY_VERIFY=2  FM_SERPAPI_MONTHLY_LIMIT=240
   ```
5. `bash deploy/macmini/deploy.sh`（同步到 `/Volumes/G2/flight-deal-monitor/app`、安裝依賴與 Chromium、載入 launchd，等到 log 出現 `scanner started`）。

狀態：`bash deploy/macmini/status.sh`。

## Telegram 指令

| 指令 | 說明 |
|---|---|
| `/watch NRT,KIX 2026-12-01 2027-02-28 4-6` | 新增監控：目的地（逗號分隔）、起訖日、旅行天數（`5` 或 `4-6`）。整趟行程都落在日期範圍內；active 組合總數上限 1000 |
| `/list` | 目前的監控與最低價 |
| `/top <id>` | 該監控最便宜的 5 組 |
| `/stop <id>` | 停止監控 |
| `/check <id>` | 該監控所有組合排到最前面立即重查 |
| `/status` | 待查組合、Google 失敗次數、SerpApi 額度、Skyscanner 狀態 |
| `/help` | 說明與常用機場代碼 |

只有 `TELEGRAM_CHAT_ID` 的對話會被處理，其他人的訊息一律忽略。

## 額度

- Google 掃描免費。
- 官網查證每天最多 2 次（`FM_SERPAPI_DAILY_VERIFY`），每月 SerpApi 呼叫上限 240（`FM_SERPAPI_MONTHLY_LIMIT`，保留 10 次給 probe）。額度用完時仍會通知，只是註明「官網價未確認」。
- Skyscanner 只在出現候選特價時才查。

## 開發與排查

```
uv sync && uv run pytest
FM_SECRETS=secrets.env uv run flight-monitor probe NRT 2026-12-12 2026-12-16 [--send]
FM_SECRETS=secrets.env FM_DB=/tmp/fm-test.db uv run flight-monitor bot
```

`probe` 會依序跑 Google、SerpApi（消耗 3 次額度，略過每日上限但受每月上限限制）、Skyscanner 並印出結果；`--send` 以特價通知格式發到 Telegram。

若 Google 常封鎖：把 `FM_SCAN_DELAY_SECONDS` 調成 40，並把 `settings.MAX_ACTIVE_COMBOS` 降到 500。
Skyscanner：實測 headless 與 headed Chromium 都會被 PerimeterX captcha 擋下（`status=blocked`）。此時通知照常送出，只附註「Skyscanner 被擋，未比價」；不使用 proxy 或破解驗證碼。XHR 子字串（`skyscanner.API_SUBSTR`）因此尚未實測。

Google 解析：fast-flights 3.1 的 parser 只讀「其他航班」群組、漏掉頁面最上方的「最佳」航班（常含最低價），所以 `google.parse_html` 自行解析兩個群組。無效機場代碼（Google 回空 payload）會被視為「無航班」，`/list` 會一直沒有價格。
