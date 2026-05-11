"""HTTP request handler — routes only. Business logic lives in storage/auth."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import auth as auth_mod
from . import storage


class DocsHandler(BaseHTTPRequestHandler):
    # Class-level config — set by server.serve() before .serve_forever()
    root_path: str = "."
    index_html: str = ""
    login_html: str = ""
    secret: bytes = b""
    credentials: dict | None = None
    secure_cookie: bool = True

    # ── auth helpers ──
    @staticmethod
    def _is_public(parsed) -> bool:
        return parsed.path in ("/api/auth/login", "/api/auth/logout", "/api/auth/me")

    def _is_authenticated(self):
        cookies = auth_mod.parse_cookies(self.headers.get("Cookie"))
        payload = auth_mod.verify_token(cookies.get(auth_mod.COOKIE_NAME), self.secret)
        return payload is not None, payload

    def _gate(self, parsed) -> bool:
        """Return True if request was handled by the gate (i.e. blocked or redirected to login)."""
        if self._is_public(parsed):
            return False
        authed, _ = self._is_authenticated()
        if authed:
            return False
        accept = self.headers.get("Accept", "")
        if self.command == "GET" and "text/html" in accept:
            self._send(200, "text/html; charset=utf-8", self.login_html.encode())
        else:
            self._send(401, "application/json", b'{"error":"Unauthorized"}')
        return True

    # ── GET ──
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/api/auth/me":
            authed, payload = self._is_authenticated()
            self._send_json(200, {"authenticated": authed, "user": payload["u"] if authed else None})
            return

        if self._gate(parsed):
            return

        if parsed.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.index_html.encode())

        elif parsed.path == "/api/files":
            files = storage.get_md_files(self.root_path)
            cats = storage.list_categories(self.root_path)
            self._send_json(200, {
                "tree": storage.build_tree(files, extra_categories=cats),
                "flat": list(files.keys()),
                "outdated": sorted(storage.get_outdated_set(self.root_path)),
                "categories": cats,
            })

        elif parsed.path == "/api/content":
            rel = unquote(qs.get("path", [""])[0])
            full = storage.safe_join(self.root_path, rel)
            if full is None:
                self._send(403, "text/plain", b"Forbidden")
                return
            if full.exists() and full.suffix == ".md":
                self._send(200, "text/plain; charset=utf-8", full.read_bytes())
            else:
                self._send(404, "text/plain", b"Not found")

        elif parsed.path == "/api/search-content":
            q = unquote(qs.get("q", [""])[0]).strip()
            if not q:
                self._send_json(200, [])
                return
            self._send_json(200, storage.search_content(self.root_path, q))

        elif parsed.path == "/api/categories":
            self._send_json(200, storage.list_categories(self.root_path))

        else:
            self._send(404, "text/plain", b"Not found")

    # ── POST ──
    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len)) if content_len else {}
        except Exception:
            self._send(400, "application/json", b'{"error":"Invalid JSON"}')
            return

        # Public auth endpoints
        if parsed.path == "/api/auth/login":
            self._handle_login(body)
            return
        if parsed.path == "/api/auth/logout":
            self._send(200, "application/json", b'{"ok":true}', extra_headers=[
                ("Set-Cookie", auth_mod.build_clear_cookie(secure=self.secure_cookie))
            ])
            return

        if self._gate(parsed):
            return

        if parsed.path == "/api/delete":
            ok, msg = storage.soft_delete(self.root_path, body.get("path", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "moved_to": msg} if ok else {"error": msg})

        elif parsed.path == "/api/mark-outdated":
            outdated = storage.get_outdated_set(self.root_path)
            outdated.add(body.get("path", ""))
            storage.save_outdated_set(self.root_path, outdated)
            self._send(200, "application/json", b'{"ok":true}')

        elif parsed.path == "/api/unmark-outdated":
            outdated = storage.get_outdated_set(self.root_path)
            outdated.discard(body.get("path", ""))
            storage.save_outdated_set(self.root_path, outdated)
            self._send(200, "application/json", b'{"ok":true}')

        elif parsed.path == "/api/upload":
            ok, msg = storage.upload_file(
                self.root_path,
                body.get("name", ""),
                body.get("category", ""),
                body.get("content", "") if isinstance(body.get("content"), str) else "",
                force=bool(body.get("force")),
            )
            self._send_json(200 if ok else 409, {"ok": ok, "path": msg} if ok else {"error": msg})

        elif parsed.path == "/api/categories":
            ok, msg = storage.create_category(self.root_path, body.get("name", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "name": msg} if ok else {"error": msg})

        elif parsed.path == "/api/categories/rename":
            ok, msg = storage.rename_category(self.root_path, body.get("from", ""), body.get("to", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "name": msg} if ok else {"error": msg})

        elif parsed.path == "/api/categories/delete":
            ok, msg = storage.delete_category(self.root_path, body.get("name", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "name": msg} if ok else {"error": msg})

        elif parsed.path == "/api/move":
            ok, msg = storage.move_file(self.root_path, body.get("path", ""), body.get("category", ""))
            self._send_json(200 if ok else 400, {"ok": ok, "path": msg} if ok else {"error": msg})

        else:
            self._send(404, "text/plain", b"Not found")

    # ── login handler ──
    def _handle_login(self, body):
        # Always re-read .auth.json from disk so `set-password` takes effect
        # without a service restart.
        fresh = auth_mod.load_credentials(self.root_path)
        if fresh:
            self.__class__.credentials = fresh
        if not self.credentials:
            self._send(500, "application/json", b'{"error":"Auth not configured"}')
            return
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if username != self.credentials.get("username"):
            time.sleep(0.2)
            self._send(401, "application/json", b'{"error":"Invalid credentials"}')
            return
        if not auth_mod.scrypt_verify(self.credentials.get("passwordHash", ""), password):
            time.sleep(0.2)
            self._send(401, "application/json", b'{"error":"Invalid credentials"}')
            return
        token = auth_mod.sign_token(self.secret, username)
        self._send_json(200, {"ok": True, "user": username}, extra_headers=[
            ("Set-Cookie", auth_mod.build_set_cookie(token, secure=self.secure_cookie))
        ])

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
