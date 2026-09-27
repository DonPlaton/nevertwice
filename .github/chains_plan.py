#!/usr/bin/env python3
"""The per-commit chains' plan (.github/workflows/chains.yml, the owner's P1 of 2026-09-27): which commits a push to
invariants/v3 chains, as the chain job's matrix.

* the commits of the push: `git rev-list --reverse --no-merges <before>..<after>` - each commit the push brought, in
  order, merges excluded;
* the one-off backlog: when the push changes .github/chains-backlog.txt, every commit it lists (full 40-hex SHAs, one
  per line, # comments) is chained too - each must be a commit, and an ancestor of the pushed head;
* deduplicated, in first-seen order; the matrix is every commit x {windows-latest py3.14, ubuntu-latest py3.12}.

Refused by name, never silently green: a push that creates the branch (before = 000...0 - there is no range), a before or
after that is not a commit in the clone, a backlog line that is not a full SHA or not an ancestor of the head, and more
than MAX_SHAS commits (the matrix limit is 256 jobs). A push that brings no commit plans 0 jobs and says so.

    BEFORE=<sha> AFTER=<sha> python .github/chains_plan.py      (writes matrix= and count= to $GITHUB_OUTPUT)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
BACKLOG = ".github/chains-backlog.txt"
MAX_SHAS = 120                                  # x 2 platforms = 240 jobs, under Actions' 256
PLATFORMS = (("windows-latest", "3.14"), ("ubuntu-latest", "3.12"))
_SHA = re.compile(r"[0-9a-f]{40}")


class PlanRefused(Exception):
    """A push this plan cannot turn into chains; the plan job fails with this message."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def _is_commit(repo: Path, sha: str) -> bool:
    """`cat-file -e <sha>^{commit}`: rev-parse --verify echoes a full SHA back even when no such object exists."""
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def backlog_shas(text: str) -> list[str]:
    """The backlog's SHAs: one full lowercase 40-hex SHA per line; blank lines and # comments are skipped."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _SHA.fullmatch(line):
            raise PlanRefused(f"{BACKLOG}:{n}: {line!r} is not a full 40-hex SHA")
        out.append(line)
    return out


def plan(repo: Path, before: str, after: str) -> list[str]:
    """The commits to chain, in order (see the module docstring)."""
    if not before or before == ZERO:
        raise PlanRefused("the push creates the branch (before = 000...0): there is no range to chain")
    for name, sha in (("before", before), ("after", after)):
        if not (_SHA.fullmatch(sha or "") and _is_commit(repo, sha)):
            raise PlanRefused(f"{name} {sha!r} is not a commit in this clone (a force push?)")
    shas = _git(repo, "rev-list", "--reverse", "--no-merges", f"{before}..{after}").stdout.split()
    if _git(repo, "diff", "--name-only", before, after, "--", BACKLOG).stdout.strip():
        text = _git(repo, "show", f"{after}:{BACKLOG}", check=False).stdout
        for sha in backlog_shas(text):
            if not _is_commit(repo, sha):
                raise PlanRefused(f"backlog {sha} is not a commit in this clone")
            if _git(repo, "merge-base", "--is-ancestor", sha, after, check=False).returncode != 0:
                raise PlanRefused(f"backlog {sha} is not an ancestor of the pushed head {after[:7]}")
            shas.append(sha)
    shas = list(dict.fromkeys(shas))
    if len(shas) > MAX_SHAS:
        raise PlanRefused(f"{len(shas)} commits exceed the cap of {MAX_SHAS} (x {len(PLATFORMS)} platforms; the "
                          f"matrix limit is 256 jobs) - split the backlog")
    return shas


def matrix(shas: list[str]) -> dict:
    return {"include": [{"sha": s, "sha7": s[:7], "os": os_, "python": py} for s in shas for os_, py in PLATFORMS]}


def main() -> int:
    repo = Path.cwd()
    try:
        shas = plan(repo, os.environ.get("BEFORE", ""), os.environ.get("AFTER", ""))
    except PlanRefused as e:
        print(f"::error::chains plan refused: {e}")
        return 1
    if not shas:
        print("::warning::this push brings no commit to chain - 0 chain jobs")
    for s in shas:
        print(f"chain {s}")
    out = os.environ.get("GITHUB_OUTPUT")
    lines = f"matrix={json.dumps(matrix(shas), separators=(',', ':'))}\ncount={len(shas)}\n"
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(lines)
    else:
        sys.stdout.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
