# Ccodex — a multi-user Markdown hub

A self-hosted, single-binary-feeling Markdown reader. Each account is its
own private library — upload `.md` files, organise them into categories,
search by name or content, render Mermaid diagrams and syntax-highlighted
code. Then share docs with another user, or generate a 7-day public link.

**Stack:** Python 3.10+ stdlib (`http.server` + `sqlite3`) · `marked.js`,
`highlight.js`, `mermaid.js` from CDN · zero pip dependencies.

---

## Features

- **Per-user libraries** — every account has its own `users/<username>/`
  directory on disk. Path-traversal-safe at every boundary.
- **Self-service signup**, hardened against bot abuse with:
  - hidden honeypot field,
  - per-IP rate limit (10 attempts / hour),
  - server-issued HMAC-signed math challenge with one-shot nonces.
- **Sharing** (read-only):
  - **With a user** — typeahead search, prefix match. Recipient sees the
    doc under "Shared with me" in their sidebar.
  - **Via link** — token URL with a 7-day default expiry. Guests can view
    without signing in; signing up auto-claims the doc into their library.
- **Markdown** — GFM tables/lists, fenced code with `highlight.js`,
  Mermaid blocks (` ```mermaid `), scroll-spy TOC, anchored headings,
  copy buttons.
- **Categories** — single-level folders per user. Create from the
  sidebar, drag files between them, delete when empty.
- **Drag-and-drop** — internal file moves (row → category header), and
  external multi-file `.md` uploads dropped onto the sidebar.
- **Upload modal** — file picker + filename field + category dropdown
  with inline "+ New category".
- **Search**
  - Top-bar quick search (filenames, `⌘K` / `Ctrl-K`).
  - "Deep Search" panel (filenames + full content with line snippets and
    highlights).
- **Outdated flag** — mark a doc out-of-date; banner + sidebar badge,
  persisted in the user's `data/users/<u>/.docs_outdated.json`.
- **PDF export** — clean A4 print-style PDF per doc, preserving Mermaid
  SVGs.
- **Auth** — scrypt-hashed passwords, HMAC-signed cookie, 10-day session,
  user-id baked into the token. Stdlib only.
- **Mobile** — sidebar collapses into a left drawer ≤768px, TOC hides
  ≤1024px, tap targets sized for thumbs.
- **Subpath-friendly** — UI fetches are relative, share URLs are built
  from `location.pathname`. Reverse-proxying at `/docs/`, `/notes/`,
  anywhere — just works.

---

## Quick start

```bash
git clone https://github.com/<you>/ccodex.git
cd ccodex
mkdir -p data

# Create the admin account (interactive password prompt)
python3 docs_viewer.py create-user --root ./data --username cranchian --admin

# Run
python3 docs_viewer.py --root ./data --port 7331
# → http://localhost:7331
```

The first time you point a browser at it, you'll get the login page. Sign
in, drop `.md` files onto the sidebar (or use the Upload button) to start.

> Running on plain HTTP locally? Pass `--insecure-cookie` so the session
> cookie doesn't insist on `Secure`. **Never use it in production.**

---

## Architecture

```
ccodex/
├── docs_viewer.py             # thin CLI entry (~60 LOC)
├── app/
│   ├── auth.py                # scrypt password hash, HMAC cookies, sign/verify token
│   ├── db.py                  # SQLite connection + schema (applied on first boot)
│   ├── users.py               # account CRUD + prefix search
│   ├── shares.py              # user-to-user shares + token-based share links
│   ├── challenge.py           # signed math challenge for signup
│   ├── ratelimit.py           # per-IP signup attempt window
│   ├── storage.py             # per-user filesystem ops (every fn scoped to user_dir)
│   ├── handler.py             # DocsHandler — HTTP routes + auth gate
│   ├── server.py              # serve() / create_user_interactive() / migrations
│   └── templates/
│       ├── index.html         # main app shell (CSS+JS inlined)
│       ├── login.html         # styled sign-in / sign-up page
│       └── share.html         # public guest doc view
└── data/                      # runtime — gitignored
    ├── db.sqlite3             # users, shares, share_links, signup_attempts
    ├── .session_secret        # 32 raw bytes, mode 600
    └── users/
        └── <username>/
            ├── .docs_outdated.json
            ├── *.md
            └── <category>/*.md
```

**Storage split.** SQLite owns identity + relationships (users, shares,
link tokens, signup attempts). The filesystem owns the documents
themselves — one directory per user. `cp -r data/users/<u>/` is a complete
user backup.

**Per-user boundary.** Every `storage` function takes a `user_dir`
absolute path as its first argument; `safe_join(user_dir, rel)` enforces
that resolved paths stay under that user's directory. No operation can
cross from one user's library to another.

---

## Anti-abuse on signup

Three layers, none alone sufficient:

1. **Honeypot field** — the signup form has a hidden `<input
   name="website">` styled `display:none`. Bots that auto-fill all inputs
   trip it. The server rejects any signup where `website` is non-empty.

2. **Per-IP rate limit** — every attempt (success or fail) records a row
   in `signup_attempts`. The window prunes itself on each attempt. More
   than **10 attempts / hour / IP** → 429. Client IP comes from
   `X-Forwarded-For` first-hop when behind a reverse proxy, else the raw
   peer address.

3. **Signed challenge** — `GET /api/auth/challenge` returns
   `{question: "5 + 7 = ?", token: "<signed>"}`. Token =
   `base64url(payload) . base64url(hmac_sha256(secret, payload))` with
   payload `{a: answer, n: nonce, exp: now+300}`. Signup must echo
   `{challenge_token, challenge_answer}`. The server verifies HMAC, expiry,
   and the answer; used nonces are tracked in an in-memory dict and pruned
   on access so replay within the 5-minute window doesn't work.

Plus: username regex `^[A-Za-z0-9_-]{3,20}$`, password ≥ 8 chars, all
checked server-side.

---

## Sharing

### With a user

- **Type a username** in the share modal's first tab; results appear as
  you type via `/api/users/search?q=...` (prefix match, max 8 results).
- Click → share is created via `/api/shares`. Read-only.
- The recipient sees the doc in a **Shared with me** section in their
  sidebar. Owner badge, no edit controls.
- Revoke at any time from the same modal.

### Via link

- **Create link** in the share modal's second tab. Default expiry: 7 days.
- The link points to `/share/<token>` (or `/<subpath>/share/<token>` if
  proxied). Anyone with the link can view as guest, no account needed.
- The guest page has a clear "Sign up to keep this document" CTA.
  Signing up via that CTA auto-creates a `shares` row for the new user, so
  the doc shows up in their **Shared with me** after first login.
- Revoke at any time; expired/revoked links show a friendly state page.

---

## CLI

```bash
# Serve
python3 docs_viewer.py [--root PATH] [--port N] [--title STR] [--insecure-cookie] [--create-root]

# Create or update a user account (idempotent)
python3 docs_viewer.py create-user --root PATH --username NAME [--password PASS] [--admin]
```

| Flag | Default | What it does |
| --- | --- | --- |
| `--root` | `.` | Data directory (holds `db.sqlite3`, `.session_secret`, `users/`). |
| `--port` | `7331` | HTTP port. |
| `--title` | inferred | Project name shown in the top bar. Inferred from the parent of `data/` if you point `--root` at a `data/` subdir. |
| `--insecure-cookie` | off | Drops `Secure` from the session cookie. Local-HTTP dev only. |
| `--create-root` | off | `mkdir -p` the `--root` if missing. |
| `create-user --admin` | — | Marks the new user as admin (admin flag is exposed in `/api/auth/me` for future admin-only UI). |

The `create-user` subcommand is **idempotent**: passing an existing
username updates the password. The running server reads users from SQLite
on every login, so password resets take effect **without** a restart.

---

## HTTP API

All routes accept JSON request bodies and return JSON unless noted.
"Required" = needs a valid session cookie.

| Route | Method | Auth | Notes |
| --- | --- | --- | --- |
| `/` | GET | public-aware | App shell HTML when authed, login HTML otherwise. |
| `/api/auth/me` | GET | public | `{authenticated, user?: {id, username, is_admin}}`. |
| `/api/auth/challenge` | GET | public | `{question, token}` — math challenge for signup. |
| `/api/auth/login` | POST | public | `{username, password}`. Sets cookie. |
| `/api/auth/signup` | POST | public | `{username, password, website, challenge_token, challenge_answer, claim_link?}`. |
| `/api/auth/logout` | POST | public | Clears cookie. |
| `/api/users/search?q=` | GET | required | Prefix typeahead (≤8 results, excludes self). |
| `/api/files` | GET | required | `{tree, flat, outdated, categories, shared_with_me}`. |
| `/api/content?path=&owner=` | GET | required | Raw markdown. `owner` omitted = self; otherwise requires an active share row. |
| `/api/search-content?q=` | GET | required | Name + content search over caller's own dir. |
| `/api/upload` | POST | required | `{name, category?, content, force?}`. Sanitises filename, enforces `.md`. |
| `/api/categories` | GET / POST | required | List / create. POST body `{name}`. |
| `/api/categories/rename` | POST | required | `{from, to}`. Rewrites outdated entries. |
| `/api/categories/delete` | POST | required | `{name}`. Refuses non-empty. |
| `/api/move` | POST | required | `{path, category}` — empty category = move to root. |
| `/api/delete` | POST | required | Soft delete → `data/users/<u>/.trash/`. |
| `/api/mark-outdated`, `/api/unmark-outdated` | POST | required | `{path}`. |
| `/api/shares` | GET / POST | required | List / create user-to-user shares. |
| `/api/shares/revoke` | POST | required | `{doc_path, username}`. |
| `/api/share-links` | GET / POST | required | List / create link tokens. POST body `{doc_path, expires_at?}`. |
| `/api/share-links/revoke` | POST | required | `{token}`. |
| `/share/<token>` | GET | public | Guest doc page (or expired/revoked notice). |
| `/share/<token>/content` | GET | public | Raw markdown for the guest page. |

Anything outside `/api/auth/*` and `/share/*` requires the session cookie.
Browser GETs get the login HTML on 401; other requests get JSON
`{"error":"Unauthorized"}`.

---

## Auth model

- **Cookie:** `pdocs_session`, `Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=10d`.
- **Token:** `base64url(payload).base64url(hmac_sha256(secret, payload))`
  with payload `{uid, u, iat, exp}`. Constant-time signature compare.
  Tokens without `uid` are rejected (pre-multi-user format).
- **Password:** `scrypt(n=2^14, r=8, p=1, dklen=64)`, stored as
  `scrypt$<salt-b64>$<hash-b64>` in `users.password_hash`.
- **Session secret:** 32 random bytes in `data/.session_secret`, generated
  on first run, mode 600.

The cookie name is intentionally specific so multiple self-hosted apps on
the same domain don't collide.

---

## Deploying behind a reverse proxy

The app is **subpath-aware by convention** — every UI fetch is relative
and share URLs are built from `location.pathname`. The same build runs at
`/`, `/docs/`, `/notes/`, anywhere. Strip the prefix at the proxy.

### systemd

```ini
# /etc/systemd/system/ccodex.service
[Unit]
Description=Ccodex docs hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=you
WorkingDirectory=/srv/ccodex
ExecStart=/usr/bin/python3 /srv/ccodex/docs_viewer.py --root /srv/ccodex/data --port 3031
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

### Caddy

```caddy
docs.example.com {
    handle_path /docs/* {
        reverse_proxy 127.0.0.1:3031
    }
}
```

---

## Upgrading from the single-user release

The first boot after upgrade auto-migrates legacy state:

1. `data/.auth.json` → `users` row (marked admin) → renamed to
   `data/.auth.json.migrated` (kept for audit).
2. Legacy top-level `*.md` files and category dirs → moved into
   `data/users/<that-admin>/`.
3. `data/.docs_outdated.json` → moved into the admin's user dir.

Migration is idempotent; a second boot is a no-op. Any old sessions are
invalidated (tokens don't carry `uid`) — sign in once and you're set.

---

## Security notes

- **Path traversal** is rejected at every entry point: `safe_join()`
  resolves the candidate path and checks `Path.relative_to(user_dir)` —
  symlinks that escape are rejected. The boundary is the user's directory,
  not the data root.
- **Filenames** are sanitised on upload (`[^A-Za-z0-9._\- ]` → `-`),
  enforced to end in `.md`, refused on overwrite unless `force:true`.
- **Don't run with `--insecure-cookie` over public HTTP**. The flag exists
  for local dev only.
- **Single-instance scope.** Admin status is informational for now; there
  is no user impersonation or admin-only API. Each account only sees its
  own docs plus what's been explicitly shared with them.
- Guest share links **do not set cookies**. Auto-claim only happens when a
  guest follows the "Create account" CTA, which posts to `/api/auth/signup`
  with `claim_link: <token>` in the body.

---

## Why a single file's worth of dependencies?

The original brief was "drop this in your project root and read your
docs." It grew into a multi-user platform — but the runtime deps still
fit on one line: **Python stdlib + three CDN scripts**. A fresh server
boots the app in seconds and the install is `cp -r` — no `pip`, no
`node_modules`, no lockfile trouble at 3 a.m.

The CDN scripts are pinned to specific versions (`marked@9.1.6`,
`highlight.js@11.9.0`, `mermaid@10.9.1`). If you want fully offline
operation, drop those bundles into `app/templates/` and rewrite the
`<script src>` tags — there's no other plumbing to change.

---

## License

MIT. Do what you like.
