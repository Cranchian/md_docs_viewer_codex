"""Filesystem operations scoped to a single user directory.

Every public function takes `user_dir` (an absolute, already-resolved Path or
str) as its first argument. `safe_join(user_dir, rel)` enforces that resolved
paths stay under `user_dir` — so even though the underlying data root holds
many users' content, no operation can cross from one to another.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".env", "migrations", "staticfiles", "media", ".tox", "dist",
    "build", ".pytest_cache", "htmlcov", ".mypy_cache", ".ruff_cache",
    "site-packages", ".idea", ".vscode", "deleted",
}

OUTDATED_FILE = ".docs_outdated.json"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


# ── path safety ──────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """Return a safe `.md` filename (strips separators, sketchy chars)."""
    name = name.strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME_RE.sub("-", name)
    name = re.sub(r"-+", "-", name).strip("-. ")
    if not name:
        return ""
    if not name.lower().endswith(".md"):
        name = name + ".md"
    return name


def sanitize_category(name: str) -> str:
    """Return a safe folder name (no extension)."""
    name = name.strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME_RE.sub("-", name)
    name = re.sub(r"-+", "-", name).strip("-. ")
    return name


def safe_join(user_dir: str | Path, rel: str) -> Path | None:
    """Resolve `rel` under `user_dir` or return None if it would escape."""
    base = Path(user_dir).resolve()
    full = (base / rel).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        return None
    return full


def ensure_user_dir(user_dir: str | Path) -> Path:
    p = Path(user_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── outdated tracking ────────────────────────────────────────────────────────

def get_outdated_set(user_dir: str | Path) -> set[str]:
    p = Path(user_dir) / OUTDATED_FILE
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception:
            return set()
    return set()


def save_outdated_set(user_dir: str | Path, outdated: set[str]) -> None:
    p = Path(user_dir) / OUTDATED_FILE
    p.write_text(json.dumps(sorted(outdated), indent=2))


# ── discovery ────────────────────────────────────────────────────────────────

def get_md_files(user_dir: str | Path) -> dict[str, dict]:
    base = Path(user_dir).resolve()
    if not base.exists():
        return {}
    files: dict[str, dict] = {}
    for path in sorted(base.rglob("*.md")):
        parts = path.relative_to(base).parts
        if any(p in SKIP_DIRS or (p.startswith(".") and p != ".") for p in parts):
            continue
        rel = str(path.relative_to(base))
        files[rel] = {"path": rel, "name": path.name, "size": path.stat().st_size}
    return files


def build_tree(files: dict[str, dict], extra_categories: list[str] | None = None) -> dict:
    """Build a nested tree from a file map. `extra_categories` ensures empty
    top-level categories still show up as `{_type: 'dir', _children: {}}` nodes."""
    tree: dict = {}
    for rel_path in sorted(files.keys()):
        parts = Path(rel_path).parts
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {"_type": "dir", "_children": {}}
            node = node[part]["_children"]
        node[parts[-1]] = {"_type": "file", "_path": rel_path, "_name": parts[-1]}
    if extra_categories:
        for cat in extra_categories:
            if cat not in tree:
                tree[cat] = {"_type": "dir", "_children": {}}
    return tree


def list_categories(user_dir: str | Path) -> list[str]:
    base = Path(user_dir).resolve()
    if not base.exists():
        return []
    out: list[str] = []
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        if p.name in SKIP_DIRS or p.name.startswith("."):
            continue
        out.append(p.name)
    return out


# ── search ───────────────────────────────────────────────────────────────────

def search_content(user_dir: str | Path, query: str) -> list[dict]:
    """Search file names AND content. Returns name+snippet results sorted by relevance."""
    base = Path(user_dir).resolve()
    files = get_md_files(base)
    results: list[dict] = []
    ql = query.lower()
    for rel, info in files.items():
        full = base / rel
        name_match = ql in rel.lower()
        snippets: list[dict] = []
        try:
            content = full.read_text(errors="replace")
            for i, line in enumerate(content.split("\n")):
                if ql in line.lower():
                    snippets.append({"line": i + 1, "text": line.strip()[:150]})
                    if len(snippets) >= 3:
                        break
        except Exception:
            pass
        if name_match or snippets:
            results.append({
                "path": rel,
                "name": info["name"],
                "name_match": name_match,
                "snippets": snippets,
            })
    results.sort(key=lambda r: (not r["name_match"], -len(r["snippets"])))
    return results


# ── mutations ────────────────────────────────────────────────────────────────

def create_category(user_dir: str | Path, name: str) -> tuple[bool, str]:
    safe = sanitize_category(name)
    if not safe:
        return False, "Invalid name"
    target = safe_join(user_dir, safe)
    if target is None:
        return False, "Invalid name"
    target.mkdir(exist_ok=True)
    return True, safe


def rename_category(user_dir: str | Path, old: str, new: str) -> tuple[bool, str]:
    o = sanitize_category(old)
    n = sanitize_category(new)
    if not o or not n:
        return False, "Invalid name"
    op = safe_join(user_dir, o)
    np = safe_join(user_dir, n)
    if op is None or np is None or not op.exists() or not op.is_dir():
        return False, "Not found"
    if np.exists():
        return False, "Already exists"
    op.rename(np)
    outdated = get_outdated_set(user_dir)
    updated: set[str] = set()
    for entry in outdated:
        if entry.startswith(o + "/"):
            updated.add(n + "/" + entry[len(o) + 1:])
        else:
            updated.add(entry)
    save_outdated_set(user_dir, updated)
    return True, n


def _trash_dir(base: Path) -> Path:
    d = base / ".trash"
    d.mkdir(exist_ok=True, parents=True)
    return d


def _remap_outdated(user_dir: str | Path, mapping: dict[str, str], drop_prefix: str | None = None) -> None:
    """Apply `old_rel -> new_rel` renames to the outdated set. Entries under
    `drop_prefix` (e.g. "cat/") with no mapping are removed."""
    outdated = get_outdated_set(user_dir)
    updated: set[str] = set()
    for entry in outdated:
        if entry in mapping:
            updated.add(mapping[entry])
        elif drop_prefix and (entry == drop_prefix.rstrip("/") or entry.startswith(drop_prefix)):
            continue  # dropped along with its category
        else:
            updated.add(entry)
    save_outdated_set(user_dir, updated)


def delete_category(user_dir: str | Path, name: str,
                    mode: str = "empty", target: str = "") -> tuple[bool, str]:
    """Delete a top-level category.

    mode="empty"    — refuse unless the category is empty (default).
    mode="purge"    — move the whole category (and its contents) into `.trash/`.
    mode="reassign" — move every `.md` inside into `target` (a category, or root
                      when blank), then remove the now-emptied category.
    """
    safe = sanitize_category(name)
    if not safe:
        return False, "Invalid name"
    base = Path(user_dir).resolve()
    cat_dir = safe_join(base, safe)
    if cat_dir is None or not cat_dir.exists() or not cat_dir.is_dir():
        return False, "Not found"

    if mode == "empty":
        try:
            if any(cat_dir.iterdir()):
                return False, "Category is not empty"
            cat_dir.rmdir()
            return True, safe
        except OSError as exc:
            return False, str(exc)

    if mode == "purge":
        dest = _trash_dir(base) / safe
        counter = 1
        while dest.exists():
            dest = _trash_dir(base) / f"{safe}_{counter}"
            counter += 1
        shutil.move(str(cat_dir), str(dest))
        _remap_outdated(user_dir, {}, drop_prefix=safe + "/")
        return True, safe

    if mode == "reassign":
        tgt = sanitize_category(target) if target else ""
        if tgt == safe:
            return False, "Invalid target"
        if tgt:
            dst_dir = safe_join(base, tgt)
            if dst_dir is None:
                return False, "Invalid target"
            dst_dir.mkdir(exist_ok=True)
        else:
            dst_dir = base
        mapping: dict[str, str] = {}
        for md in sorted(cat_dir.rglob("*.md")):
            old_rel = str(md.relative_to(base))
            dst = dst_dir / md.name
            c = 1
            while dst.exists():
                dst = dst_dir / f"{md.stem}_{c}{md.suffix}"
                c += 1
            shutil.move(str(md), str(dst))
            mapping[old_rel] = str(dst.relative_to(base))
        # Whatever is left (empty subdirs, stray non-.md) goes with the folder.
        shutil.rmtree(cat_dir, ignore_errors=True)
        _remap_outdated(user_dir, mapping, drop_prefix=safe + "/")
        return True, safe

    return False, "Invalid mode"


def upload_file(user_dir: str | Path, name: str, category: str, content: str,
                force: bool = False) -> tuple[bool, str]:
    safe_name = sanitize_filename(name)
    if not safe_name:
        return False, "Invalid filename"
    cat = sanitize_category(category) if category else ""
    base = ensure_user_dir(user_dir)
    target_dir = base
    if cat:
        cdir = safe_join(base, cat)
        if cdir is None:
            return False, "Invalid category"
        cdir.mkdir(exist_ok=True)
        target_dir = cdir
    target = target_dir / safe_name
    if target.exists() and not force:
        return False, "File exists"
    if safe_join(base, str(target.relative_to(base))) is None:
        return False, "Invalid path"
    target.write_text(content)
    return True, str(target.relative_to(base))


def move_file(user_dir: str | Path, rel: str, category: str) -> tuple[bool, str]:
    cat = sanitize_category(category) if category else ""
    base = Path(user_dir).resolve()
    src = safe_join(base, rel)
    if src is None or not src.exists() or src.suffix != ".md":
        return False, "Invalid source"
    if cat:
        dst_dir = safe_join(base, cat)
        if dst_dir is None:
            return False, "Invalid category"
        dst_dir.mkdir(exist_ok=True)
    else:
        dst_dir = base
    dst = dst_dir / src.name
    if dst.resolve() == src.resolve():
        return True, str(src.relative_to(base))
    counter = 1
    while dst.exists():
        dst = dst_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    shutil.move(str(src), str(dst))
    new_rel = str(dst.relative_to(base))
    outdated = get_outdated_set(user_dir)
    if rel in outdated:
        outdated.discard(rel)
        outdated.add(new_rel)
        save_outdated_set(user_dir, outdated)
    return True, new_rel


def soft_delete(user_dir: str | Path, rel: str, *, trash_dir: str | Path | None = None) -> tuple[bool, str]:
    """Soft delete a file by moving it into a trash directory. Trash defaults to
    `<user_dir>/.trash/` to keep users' deleted content namespaced."""
    base = Path(user_dir).resolve()
    src = safe_join(base, rel)
    if src is None or not src.exists():
        return False, "Invalid path"
    deleted_dir = Path(trash_dir).resolve() if trash_dir else (base / ".trash")
    deleted_dir.mkdir(exist_ok=True, parents=True)
    dest = deleted_dir / src.name
    counter = 1
    while dest.exists():
        dest = deleted_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    shutil.move(str(src), str(dest))
    outdated = get_outdated_set(user_dir)
    outdated.discard(rel)
    save_outdated_set(user_dir, outdated)
    try:
        return True, str(dest.relative_to(base))
    except ValueError:
        return True, str(dest)


def read_doc(user_dir: str | Path, rel: str) -> bytes | None:
    """Read a doc's raw bytes or return None if missing / not a .md / escapes."""
    full = safe_join(user_dir, rel)
    if full is None or not full.exists() or full.suffix != ".md":
        return None
    return full.read_bytes()
