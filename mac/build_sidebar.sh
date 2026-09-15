#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$DIR/mac/sidebar/build/ECHO Sidebar.app"
if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "构建浮动框需要 Apple Command Line Tools，请运行 xcode-select --install"
  exit 1
fi
mkdir -p "$APP/Contents/MacOS" "$DIR/mac/sidebar/build/module-cache"
xcrun swiftc -O -framework AppKit -framework WebKit \
  -module-cache-path "$DIR/mac/sidebar/build/module-cache" \
  "$DIR/mac/sidebar/Sidebar.swift" -o "$APP/Contents/MacOS/echo-sidebar"
cp "$DIR/mac/sidebar/Info.plist" "$APP/Contents/Info.plist"
codesign --force --sign - "$APP"
echo "已构建：$APP"
