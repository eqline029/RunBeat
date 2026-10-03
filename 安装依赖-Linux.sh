#!/usr/bin/env bash
# RunBeat 依赖一键安装 —— macOS / Linux
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3，请先安装 Python 3.10+"
  exit 1
fi
echo "使用 python3 安装 RunBeat 依赖..."
python3 -m pip install -r requirements.txt
echo "安装完成，可以运行「启动-Linux.sh」了。"
