# shellcheck shell=bash
# 由其他腳本 `source`；不可直接執行。
[ -d /Volumes/G2 ] || { echo "G2 未掛載" >&2; exit 1; }
export FM_ROOT=/Volumes/G2/flight-deal-monitor
export PATH=/opt/homebrew/bin:/usr/bin:/bin
export UV_LINK_MODE=copy
export PLAYWRIGHT_BROWSERS_PATH=$FM_ROOT/ms-playwright
