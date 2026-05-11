"""Filesystem operations: discovery, search, categories, upload, move, outdated tracking."""
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


def safe_join(root: str | Path, rel: str) -> Path | None:
    """Resolve `rel` under `root` or return None if it would escape."""
    root_resolved = Path(root).resolve()
    full = (root_resolved / rel).resolve()
    try:
        full.relative_to(root_resolved)
    except ValueError:
        return None
    return full


# ── outdated tracking ────────────────────────────────────────────────────────

def get_outdated_set(root_path: str | Path) -> set[str]:
    p = Path(root_path) / OUTDATED_FILE
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception:
            return set()
    return set()


def save_outdated_set(root_path: str | Path, outdated: set[str]) -> None:
    p = Path(root_path) / OUTDATED_FILE
    p.write_text(json.dumps(sorted(outdated), indent=2))


# ── discovery ────────────────────────────────────────────────────────────────

def get_md_files(root_path: str | Path) -> dict[str, dict]:
    root = Path(root_path).resolve()
    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        if any(p in SKIP_DIRS or (p.startswith(".") and p != ".") for p in parts):
            continue
        rel = str(path.relative_to(root))
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


def delete_category(root_path: str | Path, name: str) -> tuple[bool, str]:
    """Delete an empty top-level category. Refuses if it contains files/subdirs."""
    safe = sanitize_category(name)
    if not safe:
        return False, "Invalid name"
    target = safe_join(root_path, safe)
    if target is None or not target.exists() or not target.is_dir():
        return False, "Not found"
    try:
        if any(target.iterdir()):
            return False, "Category is not empty"
        target.rmdir()
        return True, safe
    except OSError as exc:
        return False, str(exc)


def list_categories(root_path: str | Path) -> list[str]:
    root = Path(root_path).resolve()
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name in SKIP_DIRS or p.name.startswith("."):
            continue
        out.append(p.name)
    return out


# ── search ───────────────────────────────────────────────────────────────────

def search_content(root_path: str | Path, query: str) -> list[dict]:
    """Search file names AND content. Returns name+snippet results sorted by relevance."""
    root = Path(root_path).resolve()
    files = get_md_files(root_path)
    results: list[dict] = []
    ql = query.lower()
    for rel, info in files.items():
        full = root / rel
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

def create_category(root_path: str | Path, name: str) -> tuple[bool, str]:
    safe = sanitize_category(name)
    if not safe:
        return False, "Invalid name"
    target = safe_join(root_path, safe)
    if target is None:
        return False, "Invalid name"
    target.mkdir(exist_ok=True)
    return True, safe


def rename_category(root_path: str | Path, old: str, new: str) -> tuple[bool, str]:
    o = sanitize_category(old)
    n = sanitize_category(new)
    if not o or not n:
        return False, "Invalid name"
    op = safe_join(root_path, o)
    np = safe_join(root_path, n)
    if op is None or np is None or not op.exists() or not op.is_dir():
        return False, "Not found"
    if np.exists():
        return False, "Already exists"
    op.rename(np)
    # rewrite outdated paths
    outdated = get_outdated_set(root_path)
    updated: set[str] = set()
    for entry in outdated:
        if entry.startswith(o + "/"):
            updated.add(n + "/" + entry[len(o) + 1:])
        else:
            updated.add(entry)
    save_outdated_set(root_path, updated)
    return True, n


def upload_file(root_path: str | Path, name: str, category: str, content: str,
                force: bool = False) -> tuple[bool, str]:
    safe_name = sanitize_filename(name)
    if not safe_name:
        return False, "Invalid filename"
    cat = sanitize_category(category) if category else ""
    root = Path(root_path).resolve()
    target_dir = root
    if cat:
        cdir = safe_join(root, cat)
        if cdir is None:
            return False, "Invalid category"
        cdir.mkdir(exist_ok=True)
        target_dir = cdir
    target = target_dir / safe_name
    if target.exists() and not force:
        return False, "File exists"
    # path-safety re-check
    if safe_join(root, str(target.relative_to(root))) is None:
        return False, "Invalid path"
    target.write_text(content)
    return True, str(target.relative_to(root))


def move_file(root_path: str | Path, rel: str, category: str) -> tuple[bool, str]:
    cat = sanitize_category(category) if category else ""
    root = Path(root_path).resolve()
    src = safe_join(root, rel)
    if src is None or not src.exists() or src.suffix != ".md":
        return False, "Invalid source"
    if cat:
        dst_dir = safe_join(root, cat)
        if dst_dir is None:
            return False, "Invalid category"
        dst_dir.mkdir(exist_ok=True)
    else:
        dst_dir = root
    dst = dst_dir / src.name
    if dst.resolve() == src.resolve():
        return True, str(src.relative_to(root))
    counter = 1
    while dst.exists():
        dst = dst_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    shutil.move(str(src), str(dst))
    new_rel = str(dst.relative_to(root))
    outdated = get_outdated_set(root_path)
    if rel in outdated:
        outdated.discard(rel)
        outdated.add(new_rel)
        save_outdated_set(root_path, outdated)
    return True, new_rel


def soft_delete(root_path: str | Path, rel: str) -> tuple[bool, str]:
    root = Path(root_path).resolve()
    src = safe_join(root, rel)
    if src is None or not src.exists():
        return False, "Invalid path"
    deleted_dir = root / "deleted"
    deleted_dir.mkdir(exist_ok=True)
    dest = deleted_dir / src.name
    counter = 1
    while dest.exists():
        dest = deleted_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    shutil.move(str(src), str(dest))
    outdated = get_outdated_set(root_path)
    outdated.discard(rel)
    save_outdated_set(root_path, outdated)
    return True, str(dest.relative_to(root))
