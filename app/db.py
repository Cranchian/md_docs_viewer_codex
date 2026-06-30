"""SQLite layer — single connection per process, schema applied on first boot.

Stdlib only. The DB file lives at <data_root>/db.sqlite3 and stores users,
shares, share links and signup rate-limit attempts. Documents themselves stay
on the filesystem under <data_root>/users/<username>/.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DB_FILENAME = "db.sqlite3"

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_version (v INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shares (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       INTEGER NOT NULL,
    doc_path       TEXT NOT NULL,
    shared_with_id INTEGER NOT NULL,
    created_at     INTEGER NOT NULL,
    UNIQUE(owner_id, doc_path, shared_with_id),
    FOREIGN KEY (owner_id)       REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (shared_with_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS share_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT UNIQUE NOT NULL,
    owner_id    INTEGER NOT NULL,
    doc_path    TEXT NOT NULL,
    allow_guest INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS signup_attempts (
    ip TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signup_ip_at ON signup_attempts(ip, at);
CREATE INDEX IF NOT EXISTS idx_shares_with  ON shares(shared_with_id);
CREATE INDEX IF NOT EXISTS idx_share_links_owner ON share_links(owner_id);
"""


def _db_path(data_root: str | Path) -> Path:
    return Path(data_root) / DB_FILENAME


def connect(data_root: str | Path) -> sqlite3.Connection:
    """Open a sqlite connection with foreign keys + WAL, applying schema if new."""
    path = _db_path(data_root)
    is_new = not path.exists()
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (v) VALUES (1)")
    if is_new:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    return conn
