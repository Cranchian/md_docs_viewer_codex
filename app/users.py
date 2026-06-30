"""User account CRUD on top of the SQLite users table."""
from __future__ import annotations

import re
import time
import sqlite3

from . import auth as auth_mod


USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
MIN_PASSWORD_LEN = 8


class UserError(Exception):
    """Raised on validation/uniqueness errors. .code is a stable string."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _row_to_user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
        "is_admin": bool(row["is_admin"]),
    }


def validate_username(username: str) -> str:
    u = (username or "").strip()
    if not USERNAME_RE.match(u):
        raise UserError("invalid_username", "Username must be 3–20 chars, letters/digits/_/- only.")
    return u


def validate_password(password: str) -> str:
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise UserError("invalid_password", f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    return password


def find_by_id(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


def find_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    return _row_to_user(row)


def prefix_search(conn: sqlite3.Connection, q: str, exclude_id: int | None = None, limit: int = 8) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    sql = "SELECT * FROM users WHERE username LIKE ? COLLATE NOCASE"
    params: list = [q + "%"]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    sql += " ORDER BY username COLLATE NOCASE LIMIT ?"
    params.append(limit)
    return [_row_to_user(r) for r in conn.execute(sql, params).fetchall()]


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(conn: sqlite3.Connection, username: str, password: str, is_admin: bool = False) -> dict:
    u = validate_username(username)
    p = validate_password(password)
    if find_by_username(conn, u):
        raise UserError("exists", "Username already taken.")
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?)",
        (u, auth_mod.scrypt_hash(p), now, 1 if is_admin else 0),
    )
    return _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    p = validate_password(password)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth_mod.scrypt_hash(p), user_id))


def verify_password(user: dict, password: str) -> bool:
    return auth_mod.scrypt_verify(user.get("password_hash", ""), password)


def public(user: dict | None) -> dict | None:
    """Strip the password hash before returning a user dict to a client."""
    if not user:
        return None
    return {"id": user["id"], "username": user["username"], "is_admin": user["is_admin"]}
