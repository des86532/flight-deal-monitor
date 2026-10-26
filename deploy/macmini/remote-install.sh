#!/usr/bin/env bash
# 在 Mac mini 本機執行：安裝依賴與 Chromium、安裝/重啟 launchd，並確認 scanner 已啟動。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$HERE/env.sh"
APP="$FM_ROOT/app"
UID_NUM="$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL=com.neilyang.flightmonitor
LOG="$FM_ROOT/logs/bot.log"

if [ ! -f "$FM_ROOT/secrets.env" ]; then
  echo "缺少 $FM_ROOT/secrets.env（需 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID、SERPAPI_API_KEY）" >&2
  exit 1
fi

mkdir -p "$FM_ROOT"/{data,logs} "$AGENTS_DIR"
touch "$LOG"

cd "$APP"
uv sync --frozen --no-dev
uv run --no-sync playwright install chromium

# 只看重啟之後新增的 log，避免被舊的 "scanner started" 誤判
start_line=$(( $(wc -l < "$LOG") + 1 ))

dest="$AGENTS_DIR/$LABEL.plist"
rendered="$(mktemp)"
sed -e "s#__APP__#$APP#g" -e "s#__ROOT__#$FM_ROOT#g" "$APP/deploy/macmini/launchd/$LABEL.plist" > "$rendered"
if cmp -s "$rendered" "$dest" && launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  rm -f "$rendered"
  launchctl kickstart -k "gui/$UID_NUM/$LABEL"
else
  mv "$rendered" "$dest"
  chmod 644 "$dest"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  for _ in $(seq 1 60); do
    launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || break
    sleep 1
  done
  launchctl bootstrap "gui/$UID_NUM" "$dest"
fi

for _ in $(seq 1 30); do
  if tail -n +"$start_line" "$LOG" | grep -q "scanner started"; then
    echo "flight-monitor 已啟動（scanner started）"
    exit 0
  fi
  sleep 1
done
echo "30 秒內未看到 scanner started，見 $LOG" >&2
exit 1
