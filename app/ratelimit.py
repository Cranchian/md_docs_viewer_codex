"""Per-IP signup rate-limit using the signup_attempts SQLite table."""
from __future__ import annotations

import time
import sqlite3


def record_and_check(conn: sqlite3.Connection, ip: str, *, limit: int = 10, window_sec: int = 3600) -> bool:
    """Record one attempt for `ip`, prune old rows, and return True if we're under the limit."""
    now = int(time.time())
    cutoff = now - window_sec
    conn.execute("DELETE FROM signup_attempts WHERE at < ?", (cutoff,))
    conn.execute("INSERT INTO signup_attempts (ip, at) VALUES (?, ?)", (ip, now))
    count = conn.execute(
        "SELECT COUNT(*) FROM signup_attempts WHERE ip = ? AND at >= ?", (ip, cutoff)
    ).fetchone()[0]
    return count <= limit
