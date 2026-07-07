#!/usr/bin/env python3
"""Interop: make Nevertwice memory portable to other tools and standards.

  M-13 AGENTS.md - export a project's distilled card into the project's AGENTS.md
       (the cross-tool standard read by Cursor / Windsurf / Copilot / Codex), in a
       managed block so hand-written content is preserved.
  M-14 OKF - emit an Open Knowledge Format `index.md` so the store is a valid OKF
       bundle (markdown + YAML + `type`, which the typed notes already carry).

    python interop.py agents <project> [target_dir]   # write/merge AGENTS.md
    python interop.py okf                              # write <store>/index.md
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from . import memory_hook as m
except ImportError:
    import memory_hook as m

AGENTS_START = "<!-- NEVERTWICE:START -->"
AGENTS_END = "<!-- NEVERTWICE:END -->"


def agents_md_block(project: str) -> str:
    """The Nevertwice project card rendered as an AGENTS.md managed block."""
    card = m.build_project_card(project)
    body = re.sub(re.escape(m.CARD_START) + r"|" + re.escape(m.CARD_END), "", card).strip()
    if not body:
        body = f"## Project memory: {project}\n(no distilled lessons yet)"
    return f"{AGENTS_START}\n{body}\n\n_Maintained by Nevertwice - edits here are overwritten._\n{AGENTS_END}"


def write_agents_md(project: str, target_dir) -> Path:
    """Write/merge the managed block into <target_dir>/AGENTS.md, preserving any
    hand-written content outside the markers. Idempotent."""
    target = Path(target_dir) / "AGENTS.md"
    block = agents_md_block(project)
    existing = ""
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    if AGENTS_START in existing and AGENTS_END in existing:
        # the replacement goes through a lambda: a plain string here is a regex TEMPLATE,
        # so a Windows path in the card ("C:\Users\...") would be read as escapes and
        # crash the idempotent refresh with "bad escape" (critic 2026-07, verified)
        merged = re.sub(re.escape(AGENTS_START) + r".*?" + re.escape(AGENTS_END),
                        lambda _m: block, existing, flags=re.S)
    elif existing.strip():
        merged = existing.rstrip() + "\n\n" + block + "\n"
    else:
        merged = block + "\n"
    m.write_atomic(target, merged)
    return target


def write_okf_index() -> Path:
    """Make the store a valid OKF bundle. The canonical index is the single
    `Index.md` - the human entry point that ALSO carries `type: index`
    frontmatter, so it is itself OKF-valid. Writing a separate lowercase
    `index.md` (the round-1 behaviour) collided with `Index.md` on
    case-insensitive filesystems and clobbered the human index every
    consolidation (audit H1); we just (re)build the one file instead."""
    m.rebuild_index()
    return m.VAULT / "Index.md"


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "agents" and len(args) >= 2:
        project = args[1]
        target = args[2] if len(args) > 2 else (m._project_dir_for_cwd(str(Path.cwd())) or Path.cwd())
        p = write_agents_md(project, target)
        print(f"[interop] wrote {p}")
        return 0
    if args and args[0] == "okf":
        print(f"[interop] wrote {write_okf_index()}")
        return 0
    print('usage: interop.py agents <project> [target_dir] | interop.py okf', file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
