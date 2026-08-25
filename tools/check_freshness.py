#!/usr/bin/env python3
"""Fail when a published number was produced by code that has since moved.

Task B1/B2 closed *document -> artifact* drift: every number in a tracked document resolves
to a manifest claim, and the tables are rendered from the manifest. That left the other half
open. A claim also carries a `commit`, and until B8 all 133 of them carried the same one -
`05cfdc96`, a directory move. It recorded when the artifact *file* was last touched, not what
the code did when the number was *measured*, so the entire published corpus could describe an
engine four review rounds out of date and nothing would say so.

This check closes *artifact -> code* drift:

    for each claim, for each file in produced_by:
        git log -1 --format=%H -- <file>   must be an ancestor of (or equal to) claim.commit

If a source file the claim's command imports changed after the claim was measured, the number
is stale by construction and the check fails.

A claim that cannot be re-measured here - a paid API, GPU time, an external dataset - declares
`stale` with a reason instead. Declaring it is not free: a stale claim must be withdrawn from
the public documents, so `cited_in` must be empty, and `--list-stale` prints the standing debt.
That is the whole point of the exemption - it buys honesty, not silence.

Usage:

    python tools/check_freshness.py              # fail on any stale claim (CI)
    python tools/check_freshness.py --list-stale # the withdrawn numbers and why
    python tools/check_freshness.py --verbose    # per-claim detail

Standard library only. Needs full git history: a shallow clone cannot answer the question.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"


def _git(*args: str) -> str:
    result = subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


class Git:
    """Cached git questions. One subprocess per distinct question, not per claim."""

    def __init__(self) -> None:
        self._last_touch: dict[str, str | None] = {}
        self._ancestor: dict[tuple[str, str], bool] = {}

    def last_touch(self, path: str) -> str | None:
        """The commit that most recently changed `path`, following renames."""
        if path not in self._last_touch:
            out = _git("log", "-1", "--follow", "--format=%H", "--", path)
            self._last_touch[path] = out or None
        return self._last_touch[path]

    def is_ancestor(self, older: str, newer: str) -> bool:
        key = (older, newer)
        if key not in self._ancestor:
            result = subprocess.run(("git", "merge-base", "--is-ancestor", older, newer),
                                    cwd=ROOT, capture_output=True, text=True)
            if result.returncode not in (0, 1):
                raise RuntimeError(f"git merge-base failed: {result.stderr.strip()}")
            self._ancestor[key] = result.returncode == 0
        return self._ancestor[key]


def check(manifest: dict, git: Git) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (failures, declared_stale, cited_while_stale)."""
    failures: list[dict] = []
    declared: list[dict] = []
    cited_while_stale: list[dict] = []

    for claim in manifest["claims"]:
        if claim.get("stale"):
            declared.append(claim)
            if claim.get("cited_in"):
                cited_while_stale.append(claim)
            continue
        produced_by = claim.get("produced_by")
        if not produced_by:
            failures.append({"claim": claim, "moved": [],
                             "reason": "no produced_by - run tools/produced_by.py --write"})
            continue
        moved = []
        for path in produced_by:
            touch = git.last_touch(path)
            if touch is None:
                moved.append((path, "untracked - not in git history"))
                continue
            if not git.is_ancestor(touch, claim["commit"]):
                moved.append((path, touch[:9]))
        if moved:
            failures.append({"claim": claim, "moved": moved,
                             "reason": "source moved after the number was measured"})
    return failures, declared, cited_while_stale


def _print_failures(failures: list[dict], verbose: bool) -> None:
    print(f"STALE: {len(failures)} claim(s) name code that changed after they were measured.\n")
    by_reason: dict[str, list[dict]] = {}
    for failure in failures:
        by_reason.setdefault(failure["reason"], []).append(failure)
    for reason, group in by_reason.items():
        print(f"  {reason}  ({len(group)} claims)")
        shown = group if verbose else group[:8]
        for failure in shown:
            claim = failure["claim"]
            print(f"    {claim['id']}  (stamped {claim['commit'][:9]})")
            moved = failure["moved"]
            for path, touch in (moved if verbose else moved[:3]):
                print(f"        {path} last changed at {touch}")
            if not verbose and len(moved) > 3:
                print(f"        ... and {len(moved) - 3} more files")
        if not verbose and len(group) > 8:
            print(f"    ... and {len(group) - 8} more claims")
        print()
    print("  Fix: regenerate the artifact at HEAD and restamp `commit`, or - when the run needs")
    print("  a gated resource - set `stale` with a reason and withdraw the number from the docs.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-stale", action="store_true",
                        help="print the withdrawn claims and their reasons, then exit 0")
    parser.add_argument("--verbose", action="store_true", help="name every moved file")
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    git = Git()
    failures, declared, cited_while_stale = check(manifest, git)

    if args.list_stale:
        if not declared:
            print("no claim is declared stale")
            return 0
        print(f"{len(declared)} claim(s) withdrawn from the public documents:\n")
        for claim in declared:
            print(f"  {claim['id']}")
            print(f"      value  {claim['value']} {claim['unit']}")
            print(f"      stale  {claim['stale']}")
        return 0

    ok = True
    if failures:
        ok = False
        _print_failures(failures, args.verbose)

    if cited_while_stale:
        ok = False
        print(f"WITHDRAWN BUT STILL CITED: {len(cited_while_stale)} claim(s) declare `stale` "
              f"and keep a non-empty `cited_in`.\n")
        for claim in cited_while_stale:
            print(f"    {claim['id']}  cited_in={claim['cited_in']}")
        print("\n  A stale number is not published. Remove the citation, or un-stale the claim.")

    if ok:
        fresh = len(manifest["claims"]) - len(declared)
        print(f"OK: {fresh} claim(s) trace to code no newer than the commit they were measured "
              f"at; {len(declared)} withdrawn as stale.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
