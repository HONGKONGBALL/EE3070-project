#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 Python 3。请安装 Python 3.9 或更新版本后重新打开。"
  read -r -p "按 Return 退出…"
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "此工具需要 Python 3.9 或更新版本。"
  read -r -p "按 Return 退出…"
  exit 1
fi
python3 distance_mac_server.py
