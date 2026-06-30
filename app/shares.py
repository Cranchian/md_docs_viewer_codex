"""Document sharing — direct user-to-user shares and tokenised share links."""
from __future__ import annotations

import time
import sqlite3
import secrets
import base64


LINK_DEFAULT_TTL_SEC = 60 * 60 * 24 * 7  # 7 days
LINK_TOKEN_BYTES = 24


# ── helpers ──────────────────────────────────────────────────────────────────

def _b64url(buf: bytes) -> str:
    return base64.urlsafe_b64encode(buf).decode("ascii").rstrip("=")


def _new_token() -> str:
    return _b64url(secrets.token_bytes(LINK_TOKEN_BYTES))


# ── user-to-user shares ──────────────────────────────────────────────────────

def create_share(conn: sqlite3.Connection, owner_id: int, doc_path: str, shared_with_id: int) -> bool:
    if owner_id == shared_with_id:
        return False
    now = int(time.time())
    try:
        conn.execute(
            "INSERT INTO shares (owner_id, doc_path, shared_with_id, created_at) VALUES (?, ?, ?, ?)",
            (owner_id, doc_path, shared_with_id, now),
        )
        return True
    except sqlite3.IntegrityError:
        return False  # already shared


def revoke_share(conn: sqlite3.Connection, owner_id: int, doc_path: str, shared_with_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM shares WHERE owner_id = ? AND doc_path = ? AND shared_with_id = ?",
        (owner_id, doc_path, shared_with_id),
    )
    return cur.rowcount > 0


def is_shared_with(conn: sqlite3.Connection, owner_id: int, doc_path: str, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM shares WHERE owner_id = ? AND doc_path = ? AND shared_with_id = ?",
        (owner_id, doc_path, user_id),
    ).fetchone()
    return row is not None


def list_granted(conn: sqlite3.Connection, owner_id: int, doc_path: str | None = None) -> list[dict]:
    """Shares this user has granted. Optionally filter by doc_path."""
    sql = (
        "SELECT s.doc_path, s.created_at, u.id AS user_id, u.username "
        "FROM shares s JOIN users u ON u.id = s.shared_with_id WHERE s.owner_id = ?"
    )
    params: list = [owner_id]
    if doc_path is not None:
        sql += " AND s.doc_path = ?"
        params.append(doc_path)
    sql += " ORDER BY s.created_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_received(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Shares granted TO this user. Returns rows of {owner, owner_id, doc_path, name, created_at}."""
    sql = (
        "SELECT s.doc_path, s.created_at, u.id AS owner_id, u.username AS owner "
        "FROM shares s JOIN users u ON u.id = s.owner_id WHERE s.shared_with_id = ? "
        "ORDER BY s.created_at DESC"
    )
    out = []
    for r in conn.execute(sql, (user_id,)).fetchall():
        d = dict(r)
        d["name"] = d["doc_path"].split("/")[-1]
        out.append(d)
    return out


# ── token-based share links ──────────────────────────────────────────────────

def create_link(conn: sqlite3.Connection, owner_id: int, doc_path: str,
                ttl_sec: int = LINK_DEFAULT_TTL_SEC) -> dict:
    now = int(time.time())
    token = _new_token()
    conn.execute(
        "INSERT INTO share_links (token, owner_id, doc_path, allow_guest, created_at, expires_at, revoked) "
        "VALUES (?, ?, ?, 1, ?, ?, 0)",
        (token, owner_id, doc_path, now, now + ttl_sec),
    )
    return {
        "token": token,
        "doc_path": doc_path,
        "created_at": now,
        "expires_at": now + ttl_sec,
        "revoked": False,
    }


def revoke_link(conn: sqlite3.Connection, owner_id: int, token: str) -> bool:
    cur = conn.execute(
        "UPDATE share_links SET revoked = 1 WHERE token = ? AND owner_id = ?",
        (token, owner_id),
    )
    return cur.rowcount > 0


def get_link(conn: sqlite3.Connection, token: str) -> dict | None:
    sql = (
        "SELECT l.*, u.username AS owner FROM share_links l "
        "JOIN users u ON u.id = l.owner_id WHERE l.token = ?"
    )
    row = conn.execute(sql, (token,)).fetchone()
    if not row:
        return None
    return dict(row)


def link_is_active(link: dict | None) -> bool:
    if not link:
        return False
    if link["revoked"]:
        return False
    if link["expires_at"] < int(time.time()):
        return False
    return True


def list_links(conn: sqlite3.Connection, owner_id: int, doc_path: str | None = None) -> list[dict]:
    sql = "SELECT * FROM share_links WHERE owner_id = ?"
    params: list = [owner_id]
    if doc_path is not None:
        sql += " AND doc_path = ?"
        params.append(doc_path)
    sql += " ORDER BY created_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def prune_expired_links(conn: sqlite3.Connection, older_than_sec: int = 60 * 60 * 24 * 30) -> int:
    """Delete share_links whose expires_at is older than `older_than_sec` ago. Cleanup hygiene."""
    cutoff = int(time.time()) - older_than_sec
    cur = conn.execute("DELETE FROM share_links WHERE expires_at < ?", (cutoff,))
    return cur.rowcount
