#!/usr/bin/env python3
"""Docs Viewer — markdown hub with auth, upload, categories, search, mermaid.

Run:    python3 docs_viewer.py --root ./data --port 7331
Setup:  python3 docs_viewer.py set-password --root ./data --username cranchian
"""
from __future__ import annotations

import argparse

from app.server import serve, set_password_interactive


DEFAULT_PORT = 7331


def _cmd_set_password(args: argparse.Namespace) -> None:
    set_password_interactive(args.root, args.username, args.password)


def _cmd_serve(args: argparse.Namespace) -> None:
    serve(
        root=args.root,
        port=args.port,
        title=args.title,
        insecure_cookie=args.insecure_cookie,
        create_root=args.create_root,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Docs Viewer — markdown hub with auth, upload, categories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=".", help="Project root (default: .)")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--title", default=None, help="Project name shown in UI")
    parser.add_argument(
        "--insecure-cookie", action="store_true",
        help="Drop the Secure flag on session cookie (only for local HTTP dev).",
    )
    parser.add_argument(
        "--create-root", action="store_true",
        help="Create --root if it doesn't exist.",
    )

    sub = parser.add_subparsers(dest="cmd")
    sp = sub.add_parser("set-password", help="Set the single-user credentials.")
    sp.add_argument("--root", default=".", help="Data root (where .auth.json lives).")
    sp.add_argument("--username", default="cranchian", help="Username (default: cranchian).")
    sp.add_argument("--password", default=None, help="Password (omit for interactive prompt).")

    args = parser.parse_args()

    if args.cmd == "set-password":
        _cmd_set_password(args)
        return

    _cmd_serve(args)


if __name__ == "__main__":
    main()
