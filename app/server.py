"""Server bootstrap — DB init, one-shot data migration, then start HTTP server."""
from __future__ import annotations

import json
import sys
import time
import shutil
from http.server import HTTPServer
from pathlib import Path

from . import auth as auth_mod
from . import db as db_mod
from . import users as users_mod
from .handler import DocsHandler


TEMPLATES = Path(__file__).parent / "templates"
DEFAULT_ADMIN_USERNAME = "cranchian"


def _load_template(name: str, title: str) -> str:
    raw = (TEMPLATES / name).read_text()
    return raw.replace("{{PROJECT_TITLE}}", title)


def _resolve_title(root: Path, override: str | None) -> str:
    if override:
        return override
    name = root.name
    if name.lower() in ("data", "."):
        name = root.parent.name
    return name.replace("-", " ").replace("_", " ").title() or "Docs"


# ── one-shot data migration ──────────────────────────────────────────────────

_LEGACY_RESERVED = {
    "users", "deleted", "db.sqlite3",
    auth_mod.SESSION_SECRET_FILE,
}


def _migrate_legacy_auth(data_root: Path, conn) -> str | None:
    """If <data_root>/.auth.json exists and DB has no users, migrate the
    single-user creds into the users table and rename the JSON to .migrated."""
    legacy = data_root / ".auth.json"
    if not legacy.exists():
        return None
    if users_mod.count_users(conn) > 0:
        return None
    try:
        creds = json.loads(legacy.read_text())
    except Exception:
        print(f"  ⚠  Could not parse {legacy}; skipping legacy auth migration.")
        return None
    username = (creds.get("username") or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
    password_hash = creds.get("passwordHash") or ""
    if not password_hash:
        print("  ⚠  Legacy .auth.json missing passwordHash; skipping.")
        return None
    now = int(time.time())
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?, ?, ?, 1)",
        (username, password_hash, now),
    )
    legacy.rename(data_root / ".auth.json.migrated")
    print(f"  ✓  Migrated legacy auth → users('{username}', is_admin=1).")
    return username


def _migrate_legacy_content(data_root: Path, admin_username: str) -> int:
    """Move existing data/*.md, category dirs, and .docs_outdated.json into
    data/users/<admin_username>/. Idempotent — only runs if the admin dir is
    missing or empty."""
    users_root = data_root / "users"
    users_root.mkdir(exist_ok=True)
    admin_dir = users_root / admin_username
    admin_existed = admin_dir.exists() and any(admin_dir.iterdir())
    if admin_existed:
        return 0
    admin_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in list(data_root.iterdir()):
        if entry.name in _LEGACY_RESERVED:
            continue
        if entry.name == ".auth.json.migrated":
            continue
        if entry.name == ".docs_outdated.json":
            entry.rename(admin_dir / entry.name)
            moved += 1
            continue
        if entry.name.startswith("."):
            continue
        if entry.is_dir() or entry.suffix == ".md":
            shutil.move(str(entry), str(admin_dir / entry.name))
            moved += 1
    if moved:
        print(f"  ✓  Moved {moved} legacy file/dir entries → users/{admin_username}/.")
    return moved


def _run_migrations(data_root: Path, conn) -> None:
    """Idempotent: legacy auth → users row, legacy content → admin user dir."""
    migrated_user = _migrate_legacy_auth(data_root, conn)
    if migrated_user:
        _migrate_legacy_content(data_root, migrated_user)
    else:
        # If users exist but the default admin still has no dir, create empty one.
        admin = users_mod.find_by_username(conn, DEFAULT_ADMIN_USERNAME)
        if admin is not None:
            _migrate_legacy_content(data_root, admin["username"])


# ── public entry ─────────────────────────────────────────────────────────────

def serve(*, root: str | Path, port: int, title: str | None,
          insecure_cookie: bool = False, create_root: bool = False) -> None:
    root = Path(root).resolve()
    if not root.exists():
        if create_root:
            root.mkdir(parents=True, exist_ok=True)
        else:
            print(f"✗  Root not found: {root}")
            sys.exit(1)

    conn = db_mod.connect(root)
    _run_migrations(root, conn)

    if users_mod.count_users(conn) == 0:
        print()
        print("  No users yet. Create the admin account with:")
        print(f'    python3 {sys.argv[0]} create-user --root "{root}" --username cranchian --admin')
        print()
        sys.exit(1)

    secret = auth_mod.ensure_session_secret(str(root))
    title_resolved = _resolve_title(root, title)

    DocsHandler.data_root = str(root)
    DocsHandler.index_html = _load_template("index.html", title_resolved)
    DocsHandler.login_html = _load_template("login.html", title_resolved)
    DocsHandler.share_html = _load_template("share.html", title_resolved)
    DocsHandler.secret = secret
    DocsHandler.secure_cookie = not insecure_cookie
    DocsHandler.db = conn
    DocsHandler.used_challenge_nonces = {}

    server = HTTPServer(("0.0.0.0", port), DocsHandler)
    user_count = users_mod.count_users(conn)

    print()
    print(f"  📚  {title_resolved} — CCODEX")
    print(f"  ────────────────────────────────")
    print(f"  URL    → http://localhost:{port}")
    print(f"  Root   → {root}")
    print(f"  Users  → {user_count}")
    print(f"  Auth   → 10-day sessions, signup gated by honeypot + math + IP rate-limit")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        conn.close()


def create_user_interactive(root: str | Path, username: str, password: str | None,
                            is_admin: bool = False) -> None:
    import getpass

    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    conn = db_mod.connect(root)
    _run_migrations(root, conn)

    if not password:
        try:
            password = getpass.getpass(f"Password for {username}: ")
            confirm = getpass.getpass("Confirm: ")
        except EOFError:
            print("No password provided.")
            sys.exit(2)
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(2)

    existing = users_mod.find_by_username(conn, username)
    if existing:
        try:
            users_mod.set_password(conn, existing["id"], password)
        except users_mod.UserError as e:
            print(f"✗  {e.message}")
            sys.exit(2)
        print(f"✓  Updated password for existing user '{username}'.")
        if is_admin and not existing["is_admin"]:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing["id"],))
            print(f"✓  Promoted '{username}' to admin.")
        return

    try:
        user = users_mod.create_user(conn, username, password, is_admin=is_admin)
    except users_mod.UserError as e:
        print(f"✗  {e.message}")
        sys.exit(2)
    print(f"✓  Created user '{user['username']}' (id={user['id']}, admin={user['is_admin']}).")
