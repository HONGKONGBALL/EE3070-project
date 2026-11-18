#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print '缺少项目运行环境，请按 MAC_README.md 安装。'
  exit 1
fi
open http://127.0.0.1:8765/
exec .venv/bin/python -m cane.mac.host --sim "$@"
