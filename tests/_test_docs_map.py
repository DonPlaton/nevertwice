#!/usr/bin/env python3
"""Every document is two clicks from the README, and the map is organised by task.

A documentation set rots by accretion: someone writes a good page, links it from the one
place they were working, and it is never reachable again. Before this suite there were six
such pages - `CODE_OF_CONDUCT.md`, `examples/README.md`, and four research write-ups
(`CAUSAL_VOCAB`, `EMBED_SPECIALIZE`, `QUANTIZATION`, `TWIN_GATE`) - plus `CHANGELOG.md` at
three clicks. Nothing failed when that happened, which is exactly why it happened.

Two rules:

* **reachability** - from `README.md`, following relative Markdown links only, every tracked
  document is at depth ≤ 2. A directory link resolves to its `README.md`, the way GitHub
  renders it;
* **the map is a task map** - `docs/README.md` answers *what do you want to do*, with the six
  lanes the reader actually arrives with, not an alphabetical file listing.

One exclusion, and it is about provenance rather than convenience:
`research/embed_universal/data/` vendors cloned third-party repositories, whose READMEs are
not this project's to link or to keep. `.github/` templates are excluded too - GitHub surfaces
them in its own UI, and linking them from prose would not make them any more discoverable.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
import _docs_scope  # noqa: E402 - one definition of "document"

MAP = ROOT / "docs" / "README.md"
MAX_DEPTH = 2
LANES = ("Install", "Integrate", "Operate", "Understand", "Reproduce", "Contribute")

EXCLUDED_PREFIXES = (
    "research/embed_universal/data/",   # cloned third-party repositories
    ".github/",                         # GitHub surfaces these itself
)

LINK = re.compile(r"\]\(([^)\s]+)")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def tracked_markdown() -> list:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return []
    return _docs_scope.documents(p for p in out.stdout.split()
                                 if not p.startswith(EXCLUDED_PREFIXES))


def outbound(rel: str) -> set:
    """Relative Markdown link targets of one document, as repo-relative paths."""
    path = ROOT / rel
    if not path.exists():
        return set()
    targets = set()
    for raw in LINK.findall(path.read_text(encoding="utf-8", errors="replace")):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        raw = raw.split("#", 1)[0]
        if not raw:
            continue
        target = path.parent / raw
        if target.is_dir():                    # GitHub renders the folder's README
            target = target / "README.md"
        try:
            targets.add(target.resolve().relative_to(ROOT).as_posix())
        except (ValueError, OSError):
            continue                           # outside the repo: not our graph
    return targets


def depths() -> dict:
    depth = {"README.md": 0}
    frontier = ["README.md"]
    for level in range(1, MAX_DEPTH + 2):      # one past the limit, to report how deep
        nxt = []
        for source in frontier:
            for target in outbound(source):
                if target not in depth:
                    depth[target] = level
                    nxt.append(target)
        frontier = nxt
    return depth


DEPTH = depths()


def test_every_document_is_two_clicks_away() -> None:
    print(f"\n- every tracked document is within {MAX_DEPTH} clicks of the README -")
    docs = tracked_markdown()
    check("git listed the tracked documents", len(docs) > 20, str(len(docs)))
    unreachable = [d for d in docs if d not in DEPTH]
    check("no document is unreachable", not unreachable, ", ".join(unreachable[:6]))
    deep = [f"{d} ({DEPTH[d]})" for d in docs if DEPTH.get(d, 0) > MAX_DEPTH]
    check(f"no document is deeper than {MAX_DEPTH} clicks", not deep, ", ".join(deep[:6]))


def test_the_map_is_organised_by_task() -> None:
    print("\n- docs/README.md answers 'what do you want to do' -")
    text = MAP.read_text(encoding="utf-8")
    # Anchored: `f"## {lane}" in text` is also true of `### Lane`, so demoting a lane to a
    # sub-heading of the one above it would have slipped through (it did, in the mutation run).
    headings = re.findall(r"^## (.+)$", text, re.M)
    for lane in LANES:
        check(f"the {lane!r} lane is a top-level section", lane in headings,
              f"top-level sections are {headings}")
    check("the map says the reachability rule is enforced",
          "_test_docs_map.py" in text)

    # A lane with nothing in it is a heading, not a map.
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    empty = [s.splitlines()[0] for s in sections if s.count("](") < 2]
    check("every lane points somewhere", not empty, ", ".join(empty))


def test_the_map_reaches_what_the_readme_does_not() -> None:
    """The map earns its place by covering what the front page dropped."""
    print("\n- the map covers the pages the README no longer lists -")
    mapped = outbound("docs/README.md")
    for doc in ("CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "ROADMAP.md",
                "examples/README.md", "examples/sample-store/README.md",
                "docs/DEMO.md", "docs/SELF_EXTRACTION.md",
                "research/data/README.md", "research/evidence_manifest.json",
                "skills/nevertwice-remember/SKILL.md"):
        check(f"the map links {doc}", doc in mapped)


def test_no_link_in_the_graph_is_broken() -> None:
    print("\n- every link the graph walks actually resolves -")
    broken = []
    for source in sorted(set(tracked_markdown()) | {"README.md"}):
        for target in outbound(source):
            if not (ROOT / target).exists():
                broken.append(f"{source} -> {target}")
    check("no relative link points at a missing file", not broken,
          "; ".join(broken[:6]))


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\ndocs map: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
