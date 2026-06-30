"""HTTP request handler — routes only. Business logic lives in storage/auth/users/shares."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import auth as auth_mod
from . import storage
from . import users as users_mod
from . import shares as shares_mod
from . import challenge as challenge_mod
from . import ratelimit as ratelimit_mod


class DocsHandler(BaseHTTPRequestHandler):
    # ── class-level config, set by server.serve() ──
    data_root: str = "."
    index_html: str = ""
    login_html: str = ""
    share_html: str = ""
    secret: bytes = b""
    secure_cookie: bool = True
    db = None  # sqlite3.Connection
    used_challenge_nonces: dict[str, int] = {}

    # ── auth helpers ──
    @staticmethod
    def _is_public(parsed) -> bool:
        if parsed.path.startswith("/share/"):
            return True
        return parsed.path in (
            "/api/auth/login", "/api/auth/logout", "/api/auth/me",
            "/api/auth/signup", "/api/auth/challenge",
        )

    def _resolve_user(self):
        """Return the current user dict (from DB) or None if unauthenticated."""
        cookies = auth_mod.parse_cookies(self.headers.get("Cookie"))
        payload = auth_mod.verify_token(cookies.get(auth_mod.COOKIE_NAME), self.secret)
        if not payload:
            return None
        return users_mod.find_by_id(self.db, payload["uid"])

    def _gate(self, parsed) -> bool:
        """Return True if request was handled by the gate (i.e. blocked / login shown)."""
        if self._is_public(parsed):
            return False
        if self._resolve_user() is not None:
            return False
        accept = self.headers.get("Accept", "")
        if self.command == "GET" and "text/html" in accept:
            self._send(200, "text/html; charset=utf-8", self.login_html.encode())
        else:
            self._send(401, "application/json", b'{"error":"Unauthorized"}')
        return True

    def _user_dir(self, username: str) -> Path:
        return Path(self.data_root) / "users" / username

    @staticmethod
    def _paths_arg(body) -> list:
        """Normalise a request body into a list of doc paths. Accepts either
        `paths` (array) or a single `path` string."""
        raw = body.get("paths")
        if isinstance(raw, list):
            return [p for p in raw if isinstance(p, str) and p]
        p = body.get("path", "")
        return [p] if p else []

    def _client_ip(self) -> str:
        xff = self.headers.get("X-Forwarded-For", "").strip()
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "0.0.0.0"

    # ── GET ──
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        # Public: auth-state probe (always responds, regardless of cookie)
        if parsed.path == "/api/auth/me":
            user = self._resolve_user()
            self._send_json(200, {
                "authenticated": user is not None,
                "user": users_mod.public(user),
            })
            return

        if parsed.path == "/api/auth/challenge":
            self._send_json(200, challenge_mod.issue(self.secret))
            return

        if parsed.path.startswith("/share/"):
            self._handle_share_get(parsed)
            return

        if self._gate(parsed):
            return

        user = self._resolve_user()
        user_dir = self._user_dir(user["username"])
        user_dir.mkdir(parents=True, exist_ok=True)

        if parsed.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.index_html.encode())
            return

        if parsed.path == "/api/files":
            files = storage.get_md_files(user_dir)
            cats = storage.list_categories(user_dir)
            received = shares_mod.list_received(self.db, user["id"])
            self._send_json(200, {
                "tree": storage.build_tree(files, extra_categories=cats),
                "flat": list(files.keys()),
                "outdated": sorted(storage.get_outdated_set(user_dir)),
                "categories": cats,
                "shared_with_me": [
                    {"owner": r["owner"], "owner_id": r["owner_id"],
                     "doc_path": r["doc_path"], "name": r["name"]}
                    for r in received
                ],
            })
            return

        if parsed.path == "/api/content":
            rel = unquote(qs.get("path", [""])[0])
            owner_param = unquote(qs.get("owner", [""])[0]).strip()
            self._handle_content(user, rel, owner_param)
            return

        if parsed.path == "/api/search-content":
            q = unquote(qs.get("q", [""])[0]).strip()
            if not q:
                self._send_json(200, [])
                return
            self._send_json(200, storage.search_content(user_dir, q))
            return

        if parsed.path == "/api/categories":
            self._send_json(200, storage.list_categories(user_dir))
            return

        if parsed.path == "/api/users/search":
            q = unquote(qs.get("q", [""])[0]).strip()
            results = users_mod.prefix_search(self.db, q, exclude_id=user["id"])
            self._send_json(200, [{"id": r["id"], "username": r["username"]} for r in results])
            return

        if parsed.path == "/api/shares":
            doc = unquote(qs.get("doc_path", [""])[0])
            granted = shares_mod.list_granted(self.db, user["id"], doc or None)
            received = shares_mod.list_received(self.db, user["id"])
            self._send_json(200, {
                "granted": [{"doc_path": r["doc_path"], "username": r["username"],
                             "user_id": r["user_id"], "created_at": r["created_at"]} for r in granted],
                "received": [{"doc_path": r["doc_path"], "owner": r["owner"],
                              "owner_id": r["owner_id"], "name": r["name"],
                              "created_at": r["created_at"]} for r in received],
            })
            return

        if parsed.path == "/api/share-links":
            doc = unquote(qs.get("doc_path", [""])[0])
            links = shares_mod.list_links(self.db, user["id"], doc or None)
            self._send_json(200, [
                {"token": l["token"], "doc_path": l["doc_path"], "created_at": l["created_at"],
                 "expires_at": l["expires_at"], "revoked": bool(l["revoked"])}
                for l in links
            ])
            return

        self._send(404, "text/plain", b"Not found")

    # ── POST ──
    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b""
            body = json.loads(raw) if raw else {}
        except Exception:
            self._send(400, "application/json", b'{"error":"Invalid JSON"}')
            return

        # Public auth endpoints
        if parsed.path == "/api/auth/login":
            self._handle_login(body)
            return
        if parsed.path == "/api/auth/signup":
            self._handle_signup(body)
            return
        if parsed.path == "/api/auth/logout":
            self._send(200, "application/json", b'{"ok":true}', extra_headers=[
                ("Set-Cookie", auth_mod.build_clear_cookie(secure=self.secure_cookie))
            ])
            return

        if self._gate(parsed):
            return

        if parsed.path == "/api/auth/change-password":
            self._handle_change_password(body)
            return

        user = self._resolve_user()
        user_dir = self._user_dir(user["username"])
        user_dir.mkdir(parents=True, exist_ok=True)

        if parsed.path == "/api/delete":
            paths = self._paths_arg(body)
            results = [storage.soft_delete(user_dir, p) for p in paths]
            done = sum(1 for ok, _ in results if ok)
            if done:
                self._send_json(200, {"ok": True, "deleted": done,
                                      "failed": len(results) - done})
            else:
                msg = results[0][1] if results else "No paths given"
                self._send_json(400, {"error": msg})

        elif parsed.path == "/api/mark-outdated":
            outdated = storage.get_outdated_set(user_dir)
            outdated.add(body.get("path", ""))
            storage.save_outdated_set(user_dir, outdated)
            self._send(200, "application/json", b'{"ok":true}')

        elif parsed.path == "/api/unmark-outdated":
            outdated = storage.get_outdated_set(user_dir)
            outdated.discard(body.get("path", ""))
            storage.save_outdated_set(user_dir, outdated)
            self._send(200, "application/json", b'{"ok":true}')

        elif parsed.path == "/api/upload":
            ok, msg = storage.upload_file(
                user_dir,
                body.get("name", ""),
                body.get("category", ""),
                body.get("content", "") if isinstance(body.get("content"), str) else "",
                force=bool(body.get("force")),
            )
            self._send_json(200 if ok else 409, {"ok": ok, "path": msg} if ok else {"error": msg})

        elif parsed.path == "/api/categories":
            ok, msg = storage.create_category(user_dir, body.get("name", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "name": msg} if ok else {"error": msg})

        elif parsed.path == "/api/categories/rename":
            ok, msg = storage.rename_category(user_dir, body.get("from", ""), body.get("to", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "name": msg} if ok else {"error": msg})

        elif parsed.path == "/api/categories/delete":
            ok, msg = storage.delete_category(
                user_dir, body.get("name", ""),
                mode=body.get("mode", "empty"),
                target=body.get("target", ""),
            )
            self._send_json(200 if ok else 400, {"ok": ok, "name": msg} if ok else {"error": msg})

        elif parsed.path == "/api/move":
            paths = self._paths_arg(body)
            category = body.get("category", "")
            results = [storage.move_file(user_dir, p, category) for p in paths]
            done = [m for ok, m in results if ok]
            if done:
                self._send_json(200, {"ok": True, "moved": len(done),
                                      "failed": len(results) - len(done),
                                      "path": done[0]})
            else:
                msg = results[0][1] if results else "No paths given"
                self._send_json(400, {"error": msg})

        elif parsed.path == "/api/shares":
            self._handle_create_share(user, body)
        elif parsed.path == "/api/shares/revoke":
            self._handle_revoke_share(user, body)

        elif parsed.path == "/api/share-links":
            self._handle_create_link(user, body)
        elif parsed.path == "/api/share-links/revoke":
            self._handle_revoke_link(user, body)

        else:
            self._send(404, "text/plain", b"Not found")

    # ── auth handlers ──
    def _handle_login(self, body):
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        user = users_mod.find_by_username(self.db, username) if username else None
        if not user or not users_mod.verify_password(user, password):
            time.sleep(0.2)
            self._send(401, "application/json", b'{"error":"Invalid credentials"}')
            return
        token = auth_mod.sign_token(self.secret, user["id"], user["username"])
        self._send_json(200, {"ok": True, "user": users_mod.public(user)}, extra_headers=[
            ("Set-Cookie", auth_mod.build_set_cookie(token, secure=self.secure_cookie))
        ])

    def _handle_signup(self, body):
        # Honeypot — bots auto-fill all visible inputs; ours has display:none.
        if (body.get("website") or "").strip():
            time.sleep(0.3)
            self._send(400, "application/json", b'{"error":"Invalid request"}')
            return

        ip = self._client_ip()
        if not ratelimit_mod.record_and_check(self.db, ip):
            self._send(429, "application/json", b'{"error":"Too many attempts. Try again later."}')
            return

        token = body.get("challenge_token") or ""
        answer = body.get("challenge_answer") or ""
        if not challenge_mod.verify(self.secret, token, answer, self.used_challenge_nonces):
            self._send_json(400, {"error": "Verification failed. Try again.", "code": "challenge"})
            return

        try:
            user = users_mod.create_user(
                self.db, body.get("username", ""), body.get("password", ""), is_admin=False
            )
        except users_mod.UserError as e:
            self._send_json(400, {"error": e.message, "code": e.code})
            return

        # Auto-claim a share link if signup originated from one (best-effort).
        claim_token = (body.get("claim_link") or "").strip()
        if claim_token:
            link = shares_mod.get_link(self.db, claim_token)
            if shares_mod.link_is_active(link):
                shares_mod.create_share(self.db, link["owner_id"], link["doc_path"], user["id"])

        token = auth_mod.sign_token(self.secret, user["id"], user["username"])
        self._send_json(200, {"ok": True, "user": users_mod.public(user)}, extra_headers=[
            ("Set-Cookie", auth_mod.build_set_cookie(token, secure=self.secure_cookie))
        ])

    def _handle_change_password(self, body):
        user = self._resolve_user()
        if not user:
            self._send(401, "application/json", b'{"error":"Unauthorized"}')
            return
        current = body.get("current_password") or ""
        new = body.get("new_password") or ""
        if not users_mod.verify_password(user, current):
            time.sleep(0.2)
            self._send_json(400, {"error": "Current password is incorrect.", "code": "invalid_current"})
            return
        try:
            users_mod.set_password(self.db, user["id"], new)
        except users_mod.UserError as e:
            self._send_json(400, {"error": e.message, "code": e.code})
            return
        # Rotate the session token so old copies elsewhere stop working at next /me check.
        token = auth_mod.sign_token(self.secret, user["id"], user["username"])
        self._send_json(200, {"ok": True}, extra_headers=[
            ("Set-Cookie", auth_mod.build_set_cookie(token, secure=self.secure_cookie))
        ])

    # ── content with optional owner (for shared docs) ──
    def _handle_content(self, viewer: dict, rel: str, owner_param: str):
        if not rel:
            self._send(400, "text/plain", b"Missing path")
            return
        if not owner_param or owner_param == viewer["username"]:
            full = storage.safe_join(self._user_dir(viewer["username"]), rel)
            if full is None or not full.exists() or full.suffix != ".md":
                self._send(404, "text/plain", b"Not found")
                return
            self._send(200, "text/plain; charset=utf-8", full.read_bytes())
            return
        owner = users_mod.find_by_username(self.db, owner_param)
        if not owner:
            self._send(404, "text/plain", b"Not found")
            return
        if not shares_mod.is_shared_with(self.db, owner["id"], rel, viewer["id"]):
            self._send(403, "text/plain", b"Forbidden")
            return
        full = storage.safe_join(self._user_dir(owner["username"]), rel)
        if full is None or not full.exists() or full.suffix != ".md":
            self._send(404, "text/plain", b"Not found")
            return
        self._send(200, "text/plain; charset=utf-8", full.read_bytes())

    # ── shares ──
    def _handle_create_share(self, user, body):
        doc_path = (body.get("doc_path") or "").strip()
        target_username = (body.get("username") or "").strip()
        if not doc_path or not target_username:
            self._send_json(400, {"error": "doc_path and username required"})
            return
        # The doc must actually belong to the caller
        full = storage.safe_join(self._user_dir(user["username"]), doc_path)
        if full is None or not full.exists() or full.suffix != ".md":
            self._send_json(404, {"error": "Document not found"})
            return
        target = users_mod.find_by_username(self.db, target_username)
        if not target:
            self._send_json(404, {"error": "User not found"})
            return
        if target["id"] == user["id"]:
            self._send_json(400, {"error": "Cannot share with yourself"})
            return
        ok = shares_mod.create_share(self.db, user["id"], doc_path, target["id"])
        self._send_json(200, {"ok": ok, "username": target["username"]})

    def _handle_revoke_share(self, user, body):
        doc_path = (body.get("doc_path") or "").strip()
        target_username = (body.get("username") or "").strip()
        target = users_mod.find_by_username(self.db, target_username)
        if not target:
            self._send_json(404, {"error": "User not found"})
            return
        ok = shares_mod.revoke_share(self.db, user["id"], doc_path, target["id"])
        self._send_json(200, {"ok": ok})

    def _handle_create_link(self, user, body):
        doc_path = (body.get("doc_path") or "").strip()
        full = storage.safe_join(self._user_dir(user["username"]), doc_path)
        if full is None or not full.exists() or full.suffix != ".md":
            self._send_json(404, {"error": "Document not found"})
            return
        link = shares_mod.create_link(self.db, user["id"], doc_path)
        self._send_json(200, link)

    def _handle_revoke_link(self, user, body):
        token = (body.get("token") or "").strip()
        ok = shares_mod.revoke_link(self.db, user["id"], token)
        self._send_json(200, {"ok": ok})

    # ── share token routes ──
    def _handle_share_get(self, parsed):
        # /share/<token>            → HTML
        # /share/<token>/content    → raw markdown
        parts = [p for p in parsed.path.split("/") if p]   # ['share', '<token>', ...]
        if len(parts) < 2:
            self._send(404, "text/plain", b"Not found")
            return
        token = parts[1]
        link = shares_mod.get_link(self.db, token)
        active = shares_mod.link_is_active(link)
        is_content = len(parts) >= 3 and parts[2] == "content"

        if is_content:
            if not active or not link.get("allow_guest"):
                self._send(404, "text/plain", b"Not found")
                return
            full = storage.safe_join(self._user_dir(link["owner"]), link["doc_path"])
            if full is None or not full.exists() or full.suffix != ".md":
                self._send(404, "text/plain", b"Not found")
                return
            self._send(200, "text/plain; charset=utf-8", full.read_bytes())
            return

        # HTML page (rendered even for expired/revoked, with a friendly message)
        if not link:
            html = self.share_html.replace("__STATE__", "missing")
        elif not active:
            state = "revoked" if link["revoked"] else "expired"
            html = self.share_html.replace("__STATE__", state)
        else:
            html = self.share_html.replace("__STATE__", "ok")

        ctx = {
            "token": token,
            "owner": (link or {}).get("owner", ""),
            "doc_path": (link or {}).get("doc_path", ""),
            "doc_name": ((link or {}).get("doc_path", "").split("/")[-1]) or "",
            "expires_at": (link or {}).get("expires_at", 0),
        }
        html = html.replace("__CTX__", json.dumps(ctx))
        self._send(200, "text/html; charset=utf-8", html.encode())

    # ── send helpers ──
    def _send(self, code, ct, body, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code, payload, extra_headers=None):
        body = json.dumps(payload).encode()
        self._send(code, "application/json", body, extra_headers=extra_headers)

    def log_message(self, fmt, *args):
        pass  # quiet
