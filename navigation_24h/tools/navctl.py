#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# ─── How to run ───
# python3 navigation_24h/tools/navctl.py --help
# uv run --project navigation_24h navigation_24h/tools/cli.py --help
# ──────────────────
"""Launch the project command environment from the system Python."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def main() -> None:
    """Resolve the project-local uv runtime and execute the typed CLI."""
    root = Path(__file__).resolve().parents[1]
    bundled = root / ".toolchain/uv-aarch64-apple-darwin/uv"
    uv = str(bundled) if bundled.is_file() else shutil.which("uv")
    if uv is None:
        sys.exit("Prepare uv in navigation_24h/.toolchain to run navctl.")
    _ = os.environ.setdefault("UV_CACHE_DIR", str(root / ".toolchain/uv-cache"))
    _ = os.environ.setdefault("UV_PYTHON_INSTALL_DIR", str(root / ".toolchain/python"))
    os.execv(  # noqa: S606 - Project-resolved uv entry.
        uv,
        [uv, "run", "--project", str(root), "python", str(root / "tools/cli.py"), *sys.argv[1:]],
    )


if __name__ == "__main__":
    main()
