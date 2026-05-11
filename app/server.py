"""Server bootstrap — load templates, configure handler, start HTTP server."""
from __future__ import annotations

import sys
from http.server import HTTPServer
from pathlib import Path

from . import auth as auth_mod
from . import storage
from .handler import DocsHandler


TEMPLATES = Path(__file__).parent / "templates"


def _load_template(name: str, title: str) -> str:
    raw = (TEMPLATES / name).read_text()
    return raw.replace("{{PROJECT_TITLE}}", title)


def _resolve_title(root: Path, override: str | None) -> str:
    if override:
        return override
    # If --root points at a "data/" subdir, use the parent's name
    name = root.name
    if name.lower() in ("data", "."):
        name = root.parent.name
    return name.replace("-", " ").replace("_", " ").title() or "Docs"


def serve(*, root: str | Path, port: int, title: str | None,
          insecure_cookie: bool = False, create_root: bool = False) -> None:
    root = Path(root).resolve()
    if not root.exists():
        if create_root:
            root.mkdir(parents=True, exist_ok=True)
        else:
            print(f"✗  Root not found: {root}")
            sys.exit(1)

    creds = auth_mod.load_credentials(str(root))
    if not creds:
        print()
        print("  Auth not configured.")
        print(f'  Run:  python3 {sys.argv[0]} set-password --root "{root}"')
        print()
        sys.exit(1)

    files = storage.get_md_files(root)
    secret = auth_mod.ensure_session_secret(str(root))

    title_resolved = _resolve_title(root, title)

    DocsHandler.root_path = str(root)
    DocsHandler.index_html = _load_template("index.html", title_resolved)
    DocsHandler.login_html = _load_template("login.html", title_resolved)
    DocsHandler.secret = secret
    DocsHandler.credentials = creds
    DocsHandler.secure_cookie = not insecure_cookie

    server = HTTPServer(("0.0.0.0", port), DocsHandler)

    print()
    print(f"  📚  {title_resolved} — Docs Viewer")
    print(f"  ────────────────────────────────")
    print(f"  URL   → http://localhost:{port}")
    print(f"  Root  → {root}")
    print(f"  Files → {len(files)} markdown files indexed")
    print(f"  Auth  → user=\"{creds.get('username')}\"  (10-day sessions)")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


def set_password_interactive(root: str | Path, username: str, password: str | None) -> None:
    import getpass

    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not password:
        try:
            password = getpass.getpass(f"New password for {username}: ")
            confirm = getpass.getpass("Confirm password: ")
        except EOFError:
            print("No password provided.")
            sys.exit(2)
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(2)
    if not password or len(password) < 4:
        print("Password must be at least 4 characters.")
        sys.exit(2)
    auth_mod.write_credentials(str(root), username, password)
    print(f"✓  Wrote {root / auth_mod.AUTH_FILE}")
