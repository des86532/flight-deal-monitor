#!/usr/bin/env bash
# 列出 launchd 服務狀態與最近 30 行 log；不會改變任何狀態。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$HERE/env.sh"
launchctl print "gui/$(id -u)/com.neilyang.flightmonitor" | grep -E "state|pid|last exit"
echo
tail -n 30 "$FM_ROOT/logs/bot.log"
