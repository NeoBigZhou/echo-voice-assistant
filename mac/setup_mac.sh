#!/usr/bin/env bash
# setup_mac.sh — ECHO macOS 环境安装（初始化一次即可）
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "==> 检查 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "未检测到 Homebrew，请先安装： https://brew.sh"
  exit 1
fi

echo "==> 安装 Python 3.11 与 PortAudio"
brew list python@3.11 >/dev/null 2>&1 || brew install python@3.11
brew list portaudio   >/dev/null 2>&1 || brew install portaudio

PY="$(brew --prefix python@3.11)/bin/python3.11"
if [ ! -x "$PY" ]; then
  echo "找不到 Python 3.11：$PY"
  exit 1
fi

echo "==> 创建虚拟环境 venv/"
"$PY" -m venv venv
./venv/bin/python -m pip install --upgrade pip

echo "==> 安装依赖（首次较慢，请耐心等待）"
./venv/bin/python -m pip install -r mac/requirements-mac.txt

mkdir -p data/logs

echo ""
echo "✅ 安装完成！"
echo "   启动：  mac/start_mac.sh"
echo "   面板：  http://127.0.0.1:8970"
