#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# ─── How to run ───
# navigation_24h/.venv/bin/python navigation_24h/tools/configure_secrets.py
# ──────────────────
"""Collect a local MCP key through the macOS secure input dialog."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
MAX_KEY_LENGTH: Final = 128


def main() -> int:
    """Persist the entered credential locally with owner-only permissions."""
    script = """tell application "System Events"
activate
set entry to display dialog "输入高德 Web服务 Key，保存到本地 secrets.env" ¬
default answer "" with hidden answer buttons {"取消", "保存"} ¬
default button "保存" with title "导航模块 · 高德 MCP 配置"
return text returned of entry
end tell"""
    result = subprocess.run(  # noqa: S603 - Fixed native dialog script.
        ["/usr/bin/osascript", "-e", script], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print("输入窗口已关闭")
        return 2
    key = result.stdout.strip()
    if not key or not key.isalnum() or len(key) > MAX_KEY_LENGTH:
        print("请填写高德控制台显示的 Key")
        return 2
    path = ROOT / "config/secrets.env"
    lines = path.read_text().splitlines() if path.is_file() else []
    content = [line for line in lines if not line.startswith("AMAP_MAPS_API_KEY=")]
    content.insert(0, "AMAP_MAPS_API_KEY=" + key)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as target:
        _ = target.write("\n".join(content) + "\n")
    path.chmod(0o600)
    print("高德 MCP Key 已保存到本地配置")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
