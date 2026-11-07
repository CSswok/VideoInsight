#!/usr/bin/env bash
# 一次性安装本 skill 的依赖。
# 幂等：重复执行安全。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$HERE")"

echo "==> 检查系统依赖"

missing=0
for cmd in ffmpeg ffprobe; do
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "  ✓ $cmd"
  else
    echo "  ✗ $cmd 未安装"
    missing=1
  fi
done

if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "  ✗ yt-dlp 未安装"
  missing=1
else
  echo "  ✓ yt-dlp $(yt-dlp --version 2>/dev/null | head -1)"
fi

if [ "$missing" -eq 1 ]; then
  echo ""
  echo "请先安装缺失工具："
  echo "  brew install ffmpeg yt-dlp"
  echo ""
  echo "继续将跳过这些检查，但运行时可能失败。"
fi

echo ""
echo "==> 编译 OCR 工具（macOS 内嵌字幕识别用）"
if [ "$(uname)" = "Darwin" ] && command -v swiftc >/dev/null 2>&1; then
  if swiftc -O "$HERE/ocr.swift" -o "$HERE/ocrbin" 2>/dev/null; then
    echo "  ✓ ocrbin 编译完成"
  else
    echo "  ! 编译失败，将跳过内嵌字幕 OCR（不影响其他功能）"
  fi
else
  echo "  - 非 macOS 或无 swiftc，跳过 OCR（仅支持现成字幕/语音转写）"
fi

echo ""
echo "==> 检查 Playwright（抖音通道必需）"
if python3 -c "import playwright" 2>/dev/null; then
  echo "  ✓ playwright 已安装"
  if python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
print('ok')
" 2>/dev/null; then
    echo "  ✓ chromium 可用"
  else
    echo "  ! chromium 未就绪，抖音通道可能失败"
    echo "    修复：python3 -m playwright install chromium"
  fi
else
  echo "  ! playwright 未安装（仅影响抖音，其他平台不受影响）"
  echo "    修复：pip install playwright && python3 -m playwright install chromium"
fi

echo ""
echo "==> 安装语音转写环境（mlx-whisper）"
if [ "$(uname)" != "Darwin" ]; then
  echo "  - 非 macOS，跳过（mlx 仅支持 Apple Silicon）"
else
  VENV="$HOME/.workbuddy/cache/video-deep-reading-venv"
  if [ ! -d "$VENV" ]; then
    mkdir -p "$(dirname "$VENV")"
    echo "  创建 venv: $VENV"
    python3 -m venv "$VENV"
  fi
  if "$VENV/bin/python" -c "import mlx_whisper" 2>/dev/null; then
    echo "  ✓ mlx-whisper 已就绪"
  else
    echo "  安装 mlx-whisper（首次约 3-5 分钟，走腾讯镜像）"
    "$VENV/bin/pip" install -q --disable-pip-version-check \
      -i https://mirrors.cloud.tencent.com/pypi/simple/ mlx-whisper
    echo "  ✓ 安装完成"
  fi
fi

echo ""
echo "==> 平台连通性（用于选择代理策略）"
python3 - <<'PY'
import os, socket, urllib.request

proxies = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")}
active = next((v for v in proxies.values() if v), None)
print(f"  系统代理: {active or '未设置（将直连）'}")

def probe(url, label):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            print(f"  ✓ {label}: HTTP {r.status}")
    except Exception as e:
        print(f"  ✗ {label}: {type(e).__name__}")

probe("https://www.bilibili.com", "Bilibili")
probe("https://www.douyin.com", "抖音")
try:
    probe("https://www.youtube.com", "YouTube")
except Exception:
    pass
PY

echo ""
echo "==> 自检"
python3 - <<'PY'
import json, os, shutil, subprocess, sys

skill = os.environ.get("SKILL_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True
for c in ("ffmpeg", "ffprobe", "yt-dlp"):
    if shutil.which(c):
        print(f"  ✓ {c}")
    else:
        print(f"  ✗ {c}")
        ok = False
bin_ = os.path.join(skill, "scripts", "ocrbin")
mark = "✓" if os.path.exists(bin_) else "-"
print(f"  {mark} ocrbin")
print("")
print("准备就绪。" if ok else "有缺失项，见上方提示。")
PY

echo ""
echo "完成。用法："
echo "  python3 $HERE/fetch_video.py \"<URL>\" \"<WORKDIR>\""
echo "  python3 $HERE/segment.py \"<WORKDIR>/transcript.json\" --mode s"
echo "  python3 $HERE/render.py \"<WORKDIR>/segments.json\" \"<WORKDIR>/contents.json\" \"<OUT.html>\""
