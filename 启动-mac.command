#!/usr/bin/env bash
# RunBeat 一键启动 —— macOS / Linux 通用
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "   RunBeat 跑步节拍训练音乐操作台"
echo "   首次运行会自动安装依赖，请耐心等待"
echo "============================================"

# 查找 python3
if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3。请先安装 Python 3.10+（macOS: brew install python）"
  exit 1
fi

# 检查依赖
if ! python3 -c "import numpy, scipy, librosa, edge_tts" >/dev/null 2>&1; then
  echo "首次运行，正在安装依赖（约 1-3 分钟）..."
  python3 -m pip install -r requirements.txt
fi

# 检查 ffmpeg（歌曲对齐需要）
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[提示] 未找到 ffmpeg：合成音乐与语音提示不受影响，但「我的歌曲」对齐功能不可用。"
  echo "       macOS: brew install ffmpeg    Ubuntu/Debian: sudo apt install ffmpeg"
fi

# 打开浏览器（macOS 用 open，Linux 用 xdg-open）
echo "正在启动操作台，浏览器将自动打开 http://127.0.0.1:8787"
echo "手机连同一 Wi-Fi 后，可用手机浏览器访问窗口里打印的局域网地址"
echo "按 Ctrl+C 停止服务。"
sleep 1
( open "http://127.0.0.1:8787" 2>/dev/null || xdg-open "http://127.0.0.1:8787" >/dev/null 2>&1 || true ) &
python3 server.py 8787
