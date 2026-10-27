#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# How to run: python3 distance_mac_server.py
# No packages required. Optional: uv run distance_mac_server.py --no-open
"""Serve the three-sensor viewer on loopback without serial access."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parent
FILES: Final[frozenset[str]] = frozenset({
    "index.html", "styles.css", "app.js", "chart.js", "protocol.js", "serial.js",
    "distance_pico_stream.py", "README.md",
})


class ViewerHandler(SimpleHTTPRequestHandler):
    """Expose only viewer assets, with no directory listing or caching."""

    def do_GET(self) -> None:
        """Serve allowlisted paths from the viewer folder."""
        path = self.path.partition("?")[0]
        name = "index.html" if path == "/" else path.removeprefix("/")
        if name not in FILES:
            self.send_error(404)
            return
        self.path = "/" + name
        super().do_GET()

    def do_HEAD(self) -> None:
        """Use the same allowlist for HEAD requests."""
        path = self.path.partition("?")[0]
        name = "index.html" if path == "/" else path.removeprefix("/")
        if name not in FILES:
            self.send_error(404)
            return
        self.path = "/" + name
        super().do_HEAD()

    def end_headers(self) -> None:
        """Keep local edits visible and permit only local scripts/resources."""
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()


def main() -> None:
    """Start a local server and optionally show the viewer in Chrome."""
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("文件不完整：" + ", ".join(missing))
    handler = partial(ViewerHandler, directory=str(ROOT))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"TFmini + 双路超声波测距：{url}", flush=True)
        print("保持此终端开启；结束时按 Control-C。", flush=True)
        if "--no-open" not in sys.argv:
            result = subprocess.run(["open", "-a", "Google Chrome", url], check=False)
            if result.returncode:
                print("请在 Google Chrome 中打开上面的地址。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n测距网页服务已停止。", flush=True)


if __name__ == "__main__":
    main()
