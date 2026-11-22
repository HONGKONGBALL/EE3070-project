"""Persist request counts without retaining media, credentials or transcripts."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class RequestBudget:
    def __init__(self, path, daily_limit, *, run_id=None, run_limit=25):
        self.path = Path(path)
        self.limits = (daily_limit, run_limit)
        self.run_id = run_id
        if any(limit not in range(1, 10001) for limit in self.limits):
            raise ValueError("Invalid Psyche request budget")

    def reserve(self):
        day = datetime.now(timezone.utc).date().isoformat()
        keys = [("day:" + day, self.limits[0])]
        if self.run_id:
            keys.append(("run:" + self.run_id, self.limits[1]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=1)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS counts (key TEXT PRIMARY KEY, attempts INTEGER NOT NULL)")
            connection.execute("BEGIN IMMEDIATE")
            for key, limit in keys:
                row = connection.execute("SELECT attempts FROM counts WHERE key = ?", (key,)).fetchone()
                if row and row[0] >= limit:
                    return False
            connection.executemany(
                "INSERT INTO counts VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET attempts = attempts + 1",
                [(key,) for key, _ in keys],
            )
            return True
