"""Persist request counts without retaining media, credentials or transcripts."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class RequestBudget:
    """Optional local caps. daily_limit=None leaves the gateway's own limits in charge."""

    def __init__(self, path, daily_limit=None, *, run_id=None, run_limit=25):
        self.path = Path(path)
        self.daily_limit, self.run_limit = daily_limit, run_limit
        self.run_id = run_id
        limits = [daily_limit] if daily_limit is not None else []
        if run_id:
            limits.append(run_limit)
        if any(type(limit) is not int or limit not in range(1, 10001) for limit in limits):
            raise ValueError("Invalid Psyche request budget")

    @property
    def limited(self):
        return self.daily_limit is not None or bool(self.run_id)

    def reserve(self):
        keys = []
        if self.daily_limit is not None:
            keys.append(("day:" + datetime.now(timezone.utc).date().isoformat(), self.daily_limit))
        if self.run_id:
            keys.append(("run:" + self.run_id, self.run_limit))
        if not keys:
            return True
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
