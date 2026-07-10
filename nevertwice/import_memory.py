#!/usr/bin/env python3
"""Bring the memory you already have. One-shot importers that turn what other tools
learned about you into typed Nevertwice notes, so a new agent starts with your history
instead of a blank slate.

    nevertwice-import --from claude              # Claude Code auto-memory (~/.claude/projects/*/memory)
    nevertwice-import --from chatgpt --path memories.txt   # a pasted ChatGPT "Manage memories" export
    nevertwice-import --from cursor [--path <repo>]        # .cursor/rules/*.mdc + .cursorrules
    nevertwice-import --from agents [--path AGENTS.md]     # bullets from any AGENTS.md

Everything lands through the same write path as `remember` (injection-shaped content is
rejected, secrets are redacted, one vault lock / one commit per batch, recallable at once
even with no embedder). Re-running is safe: a content-hash ledger (`<vault>/.imported.json`)
skips what was already brought in. `--dry-run` shows the plan and writes nothing.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api                      # noqa: E402
import memory_hook as m         # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_TITLE = 120
MAX_BODY = 1500
# our own AGENTS.md managed block - importing it back would be feeding the store its own output
_MANAGED_RE = re.compile(r"<!-- NEVERTWICE:START -->.*?<!-- NEVERTWICE:END -->", re.S)
_CLAUDE_TYPE = {"feedback": "pattern", "user": "pattern", "reference": "pattern",
                "project": "decision", "mistake": "mistake", "decision": "decision",
                "pattern": "pattern"}


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-ish frontmatter split: {key: value} pairs + the body. Nested keys
    (metadata:) are flattened one level. Never raises; unparseable header -> ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].splitlines():
        mm = re.match(r"^\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if mm:
            meta[mm.group(1).strip().lower()] = mm.group(2).strip().strip("'\"")
    return meta, parts[2].strip()


# ── source parsers: each yields lesson dicts {type,title,description,tags} ──────

def parse_claude(root: Path) -> list[dict]:
    """Claude Code auto-memory: <root>/<project-dir>/memory/*.md, one fact per file
    (MEMORY.md is the index - skipped). Frontmatter carries name/description/type."""
    out = []
    for f in sorted(root.glob("*/memory/*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            meta, body = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        title = meta.get("description") or meta.get("name") or f.stem
        desc = body or meta.get("description") or ""
        out.append({"type": _CLAUDE_TYPE.get((meta.get("type") or "").lower(), "pattern"),
                    "title": _clip(title, MAX_TITLE),
                    "description": _clip(desc, MAX_BODY),
                    "tags": ["imported", "claude"]})
    return out


def parse_chatgpt(path: Path) -> list[dict]:
    """A ChatGPT memory export is whatever the user copied out of Settings ->
    Personalization -> Manage memories: plain text, one memory per line or paragraph."""
    chunks = [c.strip() for c in
              re.split(r"\n\s*\n|\n(?=[-*•] )", path.read_text(encoding="utf-8", errors="replace"))]
    out = []
    for c in chunks:
        c = c.lstrip("-*• ").strip()
        if len(c) < 8:                   # skip headers/noise fragments
            continue
        out.append({"type": "pattern", "title": _clip(c, MAX_TITLE),
                    "description": _clip(c, MAX_BODY), "tags": ["imported", "chatgpt"]})
    return out


def parse_cursor(repo: Path) -> list[dict]:
    """Cursor project rules: .cursor/rules/*.mdc (frontmatter description + body) and
    the legacy .cursorrules (one note per paragraph block)."""
    out = []
    for f in sorted((repo / ".cursor" / "rules").glob("*.mdc")):
        try:
            meta, body = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        title = meta.get("description") or f.stem.replace("-", " ")
        if body or title:
            out.append({"type": "pattern", "title": _clip(title, MAX_TITLE),
                        "description": _clip(body, MAX_BODY), "tags": ["imported", "cursor"]})
    legacy = repo / ".cursorrules"
    if legacy.exists():
        try:
            for block in re.split(r"\n\s*\n", legacy.read_text(encoding="utf-8", errors="replace")):
                block = block.strip()
                if len(block) >= 8:
                    out.append({"type": "pattern", "title": _clip(block, MAX_TITLE),
                                "description": _clip(block, MAX_BODY),
                                "tags": ["imported", "cursor"]})
        except OSError:
            pass
    return out


def parse_agents(path: Path) -> list[dict]:
    """Any AGENTS.md: each top-level bullet becomes a note. The Nevertwice-managed
    block is skipped - that text came OUT of this store."""
    try:
        text = _MANAGED_RE.sub("", path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        mm = re.match(r"^\s*[-*]\s+(.{8,})$", line)
        if mm:
            item = mm.group(1).strip()
            out.append({"type": "pattern", "title": _clip(item, MAX_TITLE),
                        "description": _clip(item, MAX_BODY), "tags": ["imported", "agents-md"]})
    return out


# ── idempotency ledger ──────────────────────────────────────────────────────────

def _ledger_path() -> Path:
    return m.VAULT / ".imported.json"


def _ledger_load() -> set:
    try:
        d = json.loads(_ledger_path().read_text(encoding="utf-8"))
        return set(d) if isinstance(d, list) else set()
    except (OSError, ValueError):
        return set()


def _ledger_save(h: set) -> None:
    m.VAULT.mkdir(parents=True, exist_ok=True)
    m.write_atomic(_ledger_path(), json.dumps(sorted(h)))


def _hash(source: str, lesson: dict) -> str:
    key = f"{source}|{lesson['type']}|{lesson['title']}|{lesson.get('description', '')[:400]}"
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()


# ── the one entry ────────────────────────────────────────────────────────────────

def run_import(source: str, path: Path, project: str, dry_run: bool = False) -> dict:
    """Parse `source` at `path`, drop what the ledger has seen, write the rest as one
    batch. Returns {"found", "new", "written", "skipped"} counts."""
    parser = {"claude": parse_claude, "chatgpt": parse_chatgpt,
              "cursor": parse_cursor, "agents": parse_agents}[source]
    lessons = parser(path)
    seen = _ledger_load()
    fresh = [(ln, _hash(source, ln)) for ln in lessons if _hash(source, ln) not in seen]
    res = {"found": len(lessons), "new": len(fresh),
           "skipped": len(lessons) - len(fresh), "written": 0}
    if dry_run or not fresh:
        return res
    written = api.remember_lessons([ln for ln, _ in fresh], project=project)
    res["written"] = len(written)
    # ledger everything attempted: a lesson the write path rejects (injection-shaped)
    # is rejected deterministically - retrying it forever would just spam the log
    _ledger_save(seen | {h for _, h in fresh})
    return res


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="nevertwice-import",
        description="Import memories from other tools into the Nevertwice store.")
    ap.add_argument("--from", dest="source", required=True,
                    choices=("claude", "chatgpt", "cursor", "agents"))
    ap.add_argument("--path", help="source location (default: where that tool keeps it)")
    ap.add_argument("--project", help="project to file the notes under "
                    "(default: 'user' for claude/chatgpt, the directory name for cursor/agents)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    a = ap.parse_args()

    defaults = {"claude": Path.home() / ".claude" / "projects",
                "chatgpt": None, "cursor": Path.cwd(), "agents": Path.cwd() / "AGENTS.md"}
    path = Path(a.path).expanduser() if a.path else defaults[a.source]
    if path is None:
        print("[import] --path is required for --from chatgpt (the exported text file)",
              file=sys.stderr)
        return 2
    if not path.exists():
        print(f"[import] source not found: {path}", file=sys.stderr)
        return 2
    project = a.project or ("user" if a.source in ("claude", "chatgpt")
                            else (path.parent.name if path.is_file() else path.name) or "user")

    res = run_import(a.source, path, project, dry_run=a.dry_run)
    verb = "would import" if a.dry_run else "imported"
    print(f"[import] {a.source}: {res['found']} found, {res['skipped']} already imported, "
          f"{verb} {res['new']}" + ("" if a.dry_run else f", {res['written']} written")
          + f" -> project '{m.slug_project(project)}'")
    if not a.dry_run and res["new"] and res["written"] < res["new"]:
        print(f"[import] note: {res['new'] - res['written']} item(s) were rejected by the "
              "write path (injection-shaped or malformed) - they will not be retried")
    return 0


if __name__ == "__main__":
    sys.exit(main())
