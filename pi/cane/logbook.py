"""Structured event log: one JSON object per line, one file per day.

The diagnostics page lists and serves these files. Never log secrets.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path


class Logbook:
    def __init__(self, log_dir: str | Path, keep_recent: int = 200) -> None:
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.recent: deque[dict] = deque(maxlen=keep_recent)
        self._lock = threading.Lock()

    def path_for(self, ts: float) -> Path:
        return self.dir / time.strftime("cane-%Y%m%d.jsonl", time.localtime(ts))

    def write(self, kind: str, **data) -> None:
        ts = time.time()
        rec = {"ts": round(ts, 3), "kind": kind, **data}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self.recent.append(rec)
            with self.path_for(ts).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def files(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl"), reverse=True) + sorted(self.dir.glob("*.log"), reverse=True)
