#!/usr/bin/env bash
# restart_mac.sh — 重启 ECHO（macOS）；供面板「重启服务」调用
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

"$DIR/mac/stop_mac.sh" || true
sleep 2
"$DIR/mac/start_mac.sh"
