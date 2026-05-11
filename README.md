# Codex — a single-user Markdown hub

A self-hosted docs viewer for your own `.md` files. Drop notes from your
desktop, sort them into categories with drag-and-drop, search names and
content, render Mermaid diagrams and syntax-highlighted code. Single
password, signed cookie, 10-day sessions. No database, no framework, no
build step.

**Stack:** Python 3.10+ stdlib (`http.server`) · `marked.js`, `highlight.js`,
`mermaid.js` from CDN · zero pip dependencies.

---

## Features

- **Markdown hub** — recursive `.md` discovery, GFM tables/lists, fenced
  code with `highlight.js`, Mermaid diagrams via fenced ` ```mermaid `
  blocks, scroll-spy TOC, anchored headings, copy buttons on code blocks.
- **Categories** — single-level folders. Create from the sidebar, drop
  files between them, delete when empty.
- **Drag-and-drop**
  - Internal: drag a file row onto a category header (or the empty area
    of the tree, which is the "root" target).
  - External: drag `.md` files from your desktop straight into the
    sidebar — supports multiple files at once.
- **Upload modal** — file picker + filename field + category dropdown
  with inline "+ New category".
- **Search**
  - Top-bar quick search (filenames, `⌘K` / `Ctrl-K`).
  - "Deep Search" panel (filenames + full content with line snippets and
    highlights).
- **Outdated flag** — mark a doc as out-of-date; banner + sidebar badge,
  persisted in `data/.docs_outdated.json`.
- **PDF export** — clean A4 print-style PDF per doc, preserving Mermaid
  SVGs.
- **Auth** — single user, scrypt-hashed password, HMAC-signed cookie,
  10-day session. Stdlib only.
- **Mobile** — sidebar collapses into a left drawer ≤768px, TOC hides
  ≤1024px, tap targets sized for thumbs.
- **Subpath-friendly** — all UI `fetch()`es are relative, so reverse-
  proxying at `/docs/`, `/notes/`, etc. just works.

---

## Quick start

```bash
git clone https://github.com/<you>/codex.git
cd codex
mkdir -p data

# Set the single-user password (interactive)
python3 docs_viewer.py set-password --root ./data --username yourname

# Run
python3 docs_viewer.py --root ./data --port 7331
# → http://localhost:7331
```

The first time you point a browser at it, you'll get the login page.
After signing in, drop `.md` files into the sidebar to upload, or use
the **Upload** button in the top bar.

> If you're running on plain HTTP locally (no TLS), pass `--insecure-cookie`
> so the session cookie doesn't insist on `Secure`. Don't use it in
> production.

---

## Architecture

Originally a single-file dropper script; refactored into a tiny package
that's easier to extend.

```
codex/
├── docs_viewer.py             # thin CLI entry (~60 LOC)
├── app/
│   ├── auth.py                # scrypt password hash, HMAC cookies
│   ├── storage.py             # discovery, search, upload, move, categories
│   ├── handler.py             # DocsHandler — HTTP routes + auth gate
│   ├── server.py              # serve() / set_password_interactive()
│   └── templates/
│       ├── index.html         # main app shell (CSS+JS inlined)
│       └── login.html         # styled sign-in page
└── data/                      # your docs live here (gitignored)
    ├── .auth.json             # {username, scrypt$salt$hash}   mode 600
    ├── .session_secret        # 32 raw bytes                   mode 600
    ├── .docs_outdated.json    # outdated tracking
    └── <category>/*.md        # your markdown
```

Dotfiles in `data/` are excluded from listing and search by the
skip-rule in `storage.get_md_files`, so `.auth.json` etc. live happily
alongside your docs.

---

## CLI

```bash
# Serve
python3 docs_viewer.py [--root PATH] [--port N] [--title STR] [--insecure-cookie] [--create-root]

# Set / change credentials
python3 docs_viewer.py set-password [--root PATH] [--username NAME] [--password PASS]
```

| Flag | Default | What it does |
| --- | --- | --- |
| `--root` | `.` | Directory of `.md` files (and `.auth.json`, etc). |
| `--port` | `7331` | HTTP port. |
| `--title` | inferred | Project name shown in the top bar. Inferred from the parent of `data/` if you point `--root` at a `data/` subdir. |
| `--insecure-cookie` | off | Drops `Secure` from the session cookie. Local-HTTP dev only. |
| `--create-root` | off | `mkdir -p` the `--root` if missing. |

The `set-password` subcommand writes `data/.auth.json` (mode 600). The
running server re-reads it on every login attempt, so password resets
take effect **without** a restart.

---

## HTTP API

All routes accept JSON request bodies and return JSON unless noted.

| Route | Method | Notes |
| --- | --- | --- |
| `/` | GET | App shell (HTML). Returns login HTML if unauthenticated. |
| `/api/auth/me` | GET | `{authenticated, user?}`. Public. |
| `/api/auth/login` | POST | `{username, password}`. Sets cookie. |
| `/api/auth/logout` | POST | Clears cookie. |
| `/api/files` | GET | `{tree, flat, outdated, categories}`. |
| `/api/content?path=…` | GET | Raw markdown text. |
| `/api/search-content?q=…` | GET | Name + content search; returns `[{path, name, name_match, snippets}]`. |
| `/api/upload` | POST | `{name, category?, content, force?}`. Sanitises filename, enforces `.md`. |
| `/api/categories` | GET / POST | List / create. POST body `{name}`. |
| `/api/categories/rename` | POST | `{from, to}`. Rewrites outdated entries. |
| `/api/categories/delete` | POST | `{name}`. Refuses non-empty. |
| `/api/move` | POST | `{path, category}` — category `""` moves to root. |
| `/api/delete` | POST | Soft delete → `data/deleted/`. |
| `/api/mark-outdated`, `/api/unmark-outdated` | POST | `{path}`. |

Anything outside `/api/auth/*` requires the session cookie. Browser GETs
get the login HTML on 401; other requests get JSON `{"error":"Unauthorized"}`.

---

## Auth model

- Cookie: `pdocs_session`, `Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=10d`.
- Token: `base64url(payload).base64url(hmac_sha256(secret, payload))` —
  payload is `{u, iat, exp}`. Verification is constant-time.
- Password: `scrypt(n=2^14, r=8, p=1, dklen=64)`, stored as
  `scrypt$<salt-b64>$<hash-b64>`.
- Session secret: 32 random bytes in `data/.session_secret`, generated
  on first run, mode 600.

The cookie name is intentionally specific (`pdocs_session`) so multiple
self-hosted apps on the same domain don't fight for the same cookie.

---

## Deploying behind a reverse proxy

The app is **subpath-aware by convention, not by configuration** —
every `fetch()` in the UI uses a relative URL, so the same build works
at `/`, `/docs/`, `/notes/`, anywhere. Strip the prefix at the proxy.

### systemd

```ini
# /etc/systemd/system/codex.service
[Unit]
Description=Codex docs viewer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=you
WorkingDirectory=/srv/codex
ExecStart=/usr/bin/python3 /srv/codex/docs_viewer.py --root /srv/codex/data --port 3031
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### nginx

```nginx
location = /docs        { return 302 /docs/; }
location /docs/ {
    proxy_pass http://127.0.0.1:3031/;     # trailing / strips /docs/
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 5m;
}
```

Caddy is even simpler:

```caddy
docs.example.com {
    handle_path /docs/* {
        reverse_proxy 127.0.0.1:3031
    }
}
```

---

## Updating without downtime

There's no build step. After pulling new code:

```bash
sudo systemctl restart codex
```

Templates and Python are re-read at boot. Credentials are re-read per
login attempt, so `set-password` is hot.

---

## Security notes

- Single user. There's no admin/user separation, no signup, no password
  reset over email. If you forget the password, run `set-password` from
  a shell on the host.
- The auth gate runs **before** every non-`/api/auth/*` route — including
  static HTML, file content, and the API.
- Path traversal is rejected at every entry point: `safe_join()` resolves
  the candidate path and checks `Path.relative_to(root)` — symlinks
  that escape are rejected.
- Filenames are sanitised on upload (`[^A-Za-z0-9._\- ]` collapsed to
  `-`), enforced to end in `.md`, and refused on overwrite unless
  `force:true`.
- Don't run with `--insecure-cookie` over public HTTP. The flag exists
  for local dev only.

---

## Why a single file's worth of dependencies?

The original brief was "drop this in your project root and read your
docs." It became this. Keeping the runtime deps to **Python stdlib +
three CDN scripts** means a fresh server boots the app in seconds and
the install is `cp -r` — no `pip`, no `node_modules`, no lockfile
trouble during a 3 a.m. recovery.

The CDN scripts are pinned to specific versions (`marked@9.1.6`,
`highlight.js@11.9.0`, `mermaid@10.9.1`). If you want fully offline
operation, drop those bundles into `app/templates/` and rewrite the
`<script src>` tags — there's no other plumbing to change.

---

## License

MIT. Do what you like.
