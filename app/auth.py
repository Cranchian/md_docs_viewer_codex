"""Single-user auth: scrypt password hash + HMAC-signed session cookie.

Stdlib-only. Mirrors the postulates app's auth scheme but with a 10-day TTL
and a distinct cookie name (`pdocs_session`).
"""
from __future__ import annotations

import os
import json
import time
import hmac
import base64
import hashlib
import secrets
from pathlib import Path
from urllib.parse import unquote


COOKIE_NAME = "pdocs_session"
SESSION_TTL_SEC = 60 * 60 * 24 * 10  # 10 days

AUTH_FILE = ".auth.json"
SESSION_SECRET_FILE = ".session_secret"


# ── base64url helpers ────────────────────────────────────────────────────────

def _b64url(buf: bytes) -> str:
    return base64.urlsafe_b64encode(buf).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── secret + password storage ────────────────────────────────────────────────

def ensure_session_secret(root_path: str | Path) -> bytes:
    """Read or create a 32-byte session secret in <root>/.session_secret."""
    p = Path(root_path) / SESSION_SECRET_FILE
    if p.exists():
        return base64.b64decode(p.read_text().strip())
    secret = secrets.token_bytes(32)
    p.write_text(base64.b64encode(secret).decode("ascii"))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return secret


def scrypt_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=64)
    return f"scrypt${base64.b64encode(salt).decode('ascii')}${base64.b64encode(dk).decode('ascii')}"


def scrypt_verify(stored: str, password: str) -> bool:
    try:
        scheme, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=len(expected)
        )
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


def load_credentials(root_path: str | Path) -> dict | None:
    p = Path(root_path) / AUTH_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def write_credentials(root_path: str | Path, username: str, password: str) -> None:
    p = Path(root_path) / AUTH_FILE
    p.write_text(json.dumps({"username": username, "passwordHash": scrypt_hash(password)}, indent=2))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


# ── tokens ───────────────────────────────────────────────────────────────────

def sign_token(secret: bytes, username: str, ttl_sec: int = SESSION_TTL_SEC) -> str:
    now = int(time.time())
    payload = {"u": username, "iat": now, "exp": now + ttl_sec}
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url(sig)}"


def verify_token(token: str | None, secret: bytes) -> dict | None:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        actual = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# ── cookies ──────────────────────────────────────────────────────────────────

def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not header:
        return out
    for part in header.split(";"):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if not k:
            continue
        try:
            out[k] = unquote(v.strip())
        except Exception:
            out[k] = v.strip()
    return out


def build_set_cookie(token: str, *, secure: bool = True, ttl_sec: int = SESSION_TTL_SEC) -> str:
    parts = [
        f"{COOKIE_NAME}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={ttl_sec}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie(*, secure: bool = True) -> str:
    parts = [f"{COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
