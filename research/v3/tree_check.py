#!/usr/bin/env python3
"""PREREG-V3 TB4.11a A2 (A6): the tree check at the anchor, STAND START and STAND END (rev1 §1.3, T16, P0e; the
auditor's Q10 and D10).

* argv (Q10): exactly one fixed git command and one argv_exception index (the repository path) -
  git -C <repo> --no-optional-locks status --porcelain=v2 --branch -z --untracked-files=all --ignored=traditional.
  --no-optional-locks: git status refreshes no index, so the check writes nothing. With --untracked-files=all the
  traditional ignored listing names each file inside an ignored directory, so nothing hides behind a directory entry.
* parse: porcelain v2, NUL-separated - the head (# branch.oid), tracked changes (1, 2 with its original path, u),
  untracked (?) and ignored (!) paths; any other entry refuses by name.
* verdict (D10 a): dirty = HEAD is not the anchor, or a tracked change anywhere, or an untracked path under research/
  outside research/v3/results/ (P0e); .claude/ entries are recorded by name (the owner's global ignore is not read in
  the check's environment, so .claude/settings.local.json shows as untracked) and never fail; the ignored listing is
  recorded; the name check (§1.3): no untracked or ignored path matching codesess_code_heldout_* or *heldout* (any
  case), except the auditor's process documents, the public-heldout run logs, and __pycache__ bytecode of a TRACKED
  module - a module file on disk that the listing names neither untracked nor ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable

ARGV_TAIL = ("--no-optional-locks", "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all",
             "--ignored=traditional")
ARGV_EXCEPTION = {2: "repo"}                    # Q10: the one argv index a contract exception may name
RESULTS = "research/v3/results/"
HELDOUT = re.compile(r"heldout", re.IGNORECASE)
HELDOUT_EXCEPTIONS = frozenset({
    ".loop/HELDOUT-LABELING.md", ".loop/heldout_clean.sh",                         # the auditor's process documents
    ".loop/campaign-log/d1_heldout.log",                                          # the public external heldout set's runs
    ".loop/campaign-v2-log/b4_heldout.log", ".loop/campaign-v2-log/.cm_b4_heldout",
    ".loop/campaign-v2-log/.mark_b4_heldout"})


class TreeError(ValueError):
    """git's output is not the porcelain v2 -z listing this check reads."""


@dataclass
class TreeState:
    head: str | None = None
    branch: str | None = None
    tracked: list = field(default_factory=list)          # (XY, path)
    renamed_from: list = field(default_factory=list)
    untracked: list = field(default_factory=list)
    ignored: list = field(default_factory=list)


def argv(git: str, repo: str) -> list[str]:
    return [git, "-C", repo, *ARGV_TAIL]


def parse(out: bytes) -> TreeState:
    """git status --porcelain=v2 --branch -z: headers and entries separated by NUL; a rename's original path follows
    its entry as a NUL-separated field of its own."""
    parts = out.decode("utf-8").split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    st = TreeState()
    i = 0
    while i < len(parts):
        e = parts[i]
        if e.startswith("# branch.oid "):
            st.head = e[len("# branch.oid "):]
        elif e.startswith("# branch.head "):
            st.branch = e[len("# branch.head "):]
        elif e.startswith("# "):
            pass                                         # branch.upstream / branch.ab: not read
        elif e.startswith("1 "):
            f = e.split(" ", 8)
            st.tracked.append((f[1], f[8]))
        elif e.startswith("2 "):
            f = e.split(" ", 9)
            st.tracked.append((f[1], f[9]))
            i += 1
            if i >= len(parts):
                raise TreeError("a rename entry without its original path")
            st.renamed_from.append(parts[i])
        elif e.startswith("u "):
            f = e.split(" ", 10)
            st.tracked.append((f[1], f[10]))
        elif e.startswith("? "):
            st.untracked.append(e[2:])
        elif e.startswith("! "):
            st.ignored.append(e[2:])
        else:
            raise TreeError(f"an entry of an unknown kind in the porcelain v2 listing: {e[:40]!r}")
        i += 1
    return st


def _tracked_bytecode(path: str, listed: set[str], exists: Callable[[str], bool]) -> bool:
    """__pycache__/<module>.<tag>.pyc of a module on disk that the listing does not name - so git tracks it."""
    p = PurePosixPath(path)
    if p.parent.name != "__pycache__" or p.suffix != ".pyc":
        return False
    module = str(p.parent.parent / (p.name.split(".")[0] + ".py"))
    return exists(module) and module not in listed


def verdict(st: TreeState, *, anchor: str, exists: Callable[[str], bool]) -> dict:
    """D10 (a). ``exists(path)`` says whether a repository-relative path is on disk."""
    problems: list[str] = []
    if st.head != anchor:
        problems.append(f"HEAD {st.head} is not the anchor {anchor}")
    if st.tracked:
        problems.append(f"{len(st.tracked)} tracked file(s) changed, first {st.tracked[0][1]!r}")
    research = [p for p in st.untracked if p.startswith("research/") and not p.startswith(RESULTS)]
    if research:
        problems.append(f"{len(research)} untracked path(s) under research/ outside {RESULTS}, first {research[0]!r} (P0e)")
    listed = set(st.untracked) | set(st.ignored)
    heldout = [p for p in st.untracked + st.ignored if HELDOUT.search(p) and p not in HELDOUT_EXCEPTIONS
               and not _tracked_bytecode(p, listed, exists)]
    if heldout:
        problems.append(f"{len(heldout)} heldout-named path(s) among untracked and ignored files, first {heldout[0]!r}")
    return {"clean": not problems, "problems": problems, "head": st.head, "heldout": heldout,
            "claude": [p for p in st.untracked + st.ignored if p.startswith(".claude/")],
            "untracked": [p for p in st.untracked if not p.startswith(".claude/")], "ignored": list(st.ignored)}
