#!/usr/bin/env python3
"""CCODEX — multi-user markdown hub with auth, sharing, categories, search.

Run:    python3 docs_viewer.py --root ./data --port 7331
Setup:  python3 docs_viewer.py create-user --root ./data --username cranchian --admin
"""
from __future__ import annotations

import argparse

from app.server import serve, create_user_interactive


DEFAULT_PORT = 7331


def _cmd_create_user(args: argparse.Namespace) -> None:
    create_user_interactive(args.root, args.username, args.password, is_admin=args.admin)


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
        description="CCODEX — multi-user markdown hub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=".", help="Data root (default: .)")
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
    cu = sub.add_parser("create-user", help="Create or update a user account (idempotent).")
    cu.add_argument("--root", default=".", help="Data root (where db.sqlite3 lives).")
    cu.add_argument("--username", required=True, help="Username.")
    cu.add_argument("--password", default=None, help="Password (omit for interactive prompt).")
    cu.add_argument("--admin", action="store_true", help="Mark this user as admin.")

    args = parser.parse_args()

    if args.cmd == "create-user":
        _cmd_create_user(args)
        return

    _cmd_serve(args)


if __name__ == "__main__":
    main()
