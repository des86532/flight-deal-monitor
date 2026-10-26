#!/usr/bin/env bash
# launchd 的 StandardOutPath 由 xpcproxy 建檔，其 sandbox 禁止寫入 /Volumes 外接碟，
# 因此由本腳本把 stdout/stderr 附加到 G2 上的 log，再 exec 目標程式（PID 不變，KeepAlive 照常追蹤）。
# 用法：run-logged.sh <log 檔> <程式> [參數...]
set -euo pipefail
log="$1"
shift
exec "$@" >>"$log" 2>&1
