#!/usr/bin/env bash
# 把 repo 同步到 /Volumes/G2/flight-deal-monitor/app 並執行 remote-install.sh。
set -euo pipefail

APP_DIR=/Volumes/G2/flight-deal-monitor/app
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
EXCLUDE="$REPO_ROOT/deploy/macmini/rsync-exclude.txt"

[ -d /Volumes/G2 ] || { echo "G2 未掛載" >&2; exit 1; }
mkdir -p "$APP_DIR"
if [ "$REPO_ROOT" != "$(cd "$APP_DIR" && pwd -P)" ]; then
  rsync -az --delete --exclude-from="$EXCLUDE" "$REPO_ROOT/" "$APP_DIR/"
fi
bash "$APP_DIR/deploy/macmini/remote-install.sh"
