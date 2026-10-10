"""Record a short clip from the USB sound card's mic input with `arecord`."""

from __future__ import annotations

import subprocess
from pathlib import Path


def record_wav(path: str | Path, seconds: float, device: str = "default", rate: int = 16000) -> Path:
    """Blocking. 16 kHz mono S16_LE, which is what Vosk expects."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["arecord", "-q", "-D", device, "-f", "S16_LE", "-c", "1", "-r", str(rate),
           "-d", str(max(1, round(seconds))), str(path)]
    subprocess.run(cmd, check=True, timeout=seconds + 5)
    return path
