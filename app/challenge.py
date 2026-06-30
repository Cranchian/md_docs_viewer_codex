"""Server-issued, signed math challenge for signup anti-bot defense.

Stateless: the challenge token is base64url(payload) . base64url(hmac_sha256(secret, payload)).
Payload carries the expected answer, a random nonce, and an expiry. We also
track used nonces in a small in-memory set to defeat replay within the window.
"""
from __future__ import annotations

import json
import time
import hmac
import base64
import hashlib
import secrets
import random


CHALLENGE_TTL_SEC = 300  # 5 min


def _b64url(buf: bytes) -> str:
    return base64.urlsafe_b64encode(buf).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _make_question() -> tuple[str, int]:
    a = random.randint(2, 12)
    b = random.randint(2, 12)
    op = random.choice(("+", "-", "+"))   # bias toward +, avoid * for simpler answers
    if op == "+":
        return f"{a} + {b} = ?", a + b
    return f"{max(a, b)} - {min(a, b)} = ?", abs(a - b)


def issue(secret: bytes) -> dict:
    """Return {question, token}. Token bakes in the answer + nonce + expiry."""
    question, answer = _make_question()
    payload = {
        "a": answer,
        "n": secrets.token_hex(8),
        "exp": int(time.time()) + CHALLENGE_TTL_SEC,
    }
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return {"question": question, "token": f"{payload_b64}.{_b64url(sig)}"}


def verify(secret: bytes, token: str, answer: str, used_nonces: dict[str, int]) -> bool:
    """Return True if token signature + expected answer + expiry + nonce-freshness all check out."""
    if not token or "." not in token or not answer:
        return False
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return False
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return False

    now = int(time.time())
    if payload.get("exp", 0) < now:
        return False
    nonce = payload.get("n")
    if not nonce or not isinstance(nonce, str):
        return False
    # prune nonces while we're here
    expired = [k for k, v in used_nonces.items() if v < now]
    for k in expired:
        used_nonces.pop(k, None)
    if nonce in used_nonces:
        return False

    try:
        if int(str(answer).strip()) != int(payload.get("a")):
            return False
    except (ValueError, TypeError):
        return False

    used_nonces[nonce] = payload["exp"]
    return True
