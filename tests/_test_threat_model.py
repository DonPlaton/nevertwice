#!/usr/bin/env python3
"""The threat model is checked, not read.

GOAL E2's exit criterion is that the threat model *names an owner for every boundary and a
test for every claim*. Both halves are only worth having if something enforces them, because
a security document is exactly the kind of file that is written once, admired, and then
quietly outlives the code it describes.

So this suite parses `docs/THREAT_MODEL.md` and requires:

* every `## Boundary:` section names an **Owner**, and that owner is a file that exists;
* every section says what is trusted and what is not - a boundary with only one side is not a
  boundary;
* every **Claim** names a check in the form `path/to/suite.py::check name`;
* that suite exists, and that exact check name appears in it.

The last one is the load-bearing rule. Naming a test is cheap; naming a test *that exists* is
the part that fails when someone deletes a check, renames it, or writes a claim they never got
round to backing. It is the same contract `research/evidence_manifest.json` imposes on
published numbers, applied to published security claims.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

DOC = ROOT / "docs" / "THREAT_MODEL.md"

PASSED = 0
FAILED = 0

BOUNDARY = re.compile(r"^## Boundary:\s*(.+)$", re.M)
#: A field runs until the next bullet, the next heading, or the end - claims wrap across
#: lines, and a line-anchored capture silently drops the reference that lives on line two.
#: (It did exactly that on the first run: seventeen claims, every one reported unbacked.)
FIELD = re.compile(
    r"^- \*\*(Owner|Trusted|Untrusted|Claim|Residual risk):\*\*\s*(.*?)"
    r"(?=\n- \*\*|\n#|\Z)",
    re.M | re.S)
#: `tests/_test_x.py::a check name` - the whole point of the format is that both halves are
#: mechanically checkable.
REFERENCE = re.compile(r"`(tests/[\w/]+\.py)::([^`]+)`")


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def sections() -> list:
    """(name, body) per boundary, in document order."""
    text = DOC.read_text(encoding="utf-8")
    marks = list(BOUNDARY.finditer(text))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((mark.group(1).strip(), text[mark.end():end]))
    return out


def test_the_document_exists_and_has_boundaries() -> None:
    print("\n- there is a threat model, and it has boundaries -")
    check("docs/THREAT_MODEL.md exists", DOC.is_file(), str(DOC))
    found = sections()
    check("it declares boundaries", len(found) >= 5, str(len(found)))
    names = [n for n, _ in found]
    check("boundary names are unique", len(names) == len(set(names)),
          str([n for n in names if names.count(n) > 1]))
    print(f"       ({len(found)} boundaries: {', '.join(names)})")


def test_every_boundary_names_an_owner_that_exists() -> None:
    """The first half of the exit criterion."""
    print("\n- every boundary has somewhere for a gap to be fixed -")
    ownerless, phantom = [], []
    for name, body in sections():
        fields = {k: v for k, v in FIELD.findall(body)}
        owner = fields.get("Owner", "").strip()
        if not owner:
            ownerless.append(name)
            continue
        # The owner names one or more files; at least one must actually exist, so the
        # document cannot point at a module that was renamed away.
        paths = re.findall(r"`([\w./]+\.py)`", owner)
        if not paths:
            phantom.append(f"{name}: owner {owner!r} names no file")
        elif not any((ROOT / p).is_file() for p in paths):
            phantom.append(f"{name}: none of {paths} exists")
    check("every boundary names an owner", not ownerless, ", ".join(ownerless))
    check("every owner is a file that exists", not phantom, "; ".join(phantom[:3]))


def test_every_boundary_has_two_sides() -> None:
    print("\n- a boundary with one side is not a boundary -")
    incomplete = []
    for name, body in sections():
        fields = {k: v for k, v in FIELD.findall(body)}
        if not fields.get("Trusted", "").strip() or not fields.get("Untrusted", "").strip():
            incomplete.append(name)
    check("every boundary says what is trusted and what is not", not incomplete,
          ", ".join(incomplete))


def test_every_claim_names_a_test_that_exists() -> None:
    """The second half of the exit criterion, and the load-bearing one."""
    print("\n- every claim names a check, and every check is really there -")
    unbacked, missing_file, missing_check = [], [], []
    total = 0
    cache: dict = {}

    for name, body in sections():
        claims = [v for k, v in FIELD.findall(body) if k == "Claim"]
        check(f"boundary {name!r} makes at least one claim", bool(claims))
        for claim in claims:
            total += 1
            reference = REFERENCE.search(claim)
            if not reference:
                unbacked.append(f"{name}: {claim[:60]}")
                continue
            suite, check_name = reference.group(1), reference.group(2).strip()
            path = ROOT / suite
            if not path.is_file():
                missing_file.append(f"{name}: {suite}")
                continue
            if suite not in cache:
                cache[suite] = path.read_text(encoding="utf-8")
            if check_name not in cache[suite]:
                missing_check.append(f"{name}: {suite} has no check {check_name!r}")

    check("every claim names a test", not unbacked, "; ".join(unbacked[:3]))
    check("every named suite exists", not missing_file, "; ".join(missing_file[:3]))
    check("every named check exists in that suite", not missing_check,
          "; ".join(missing_check[:3]))
    print(f"       ({total} claims, each backed by a check that runs in CI)")


def test_the_known_gaps_are_written_down() -> None:
    """A threat model that lists only what it defends is marketing."""
    print("\n- what this does NOT defend is stated -")
    text = DOC.read_text(encoding="utf-8")
    check("there is a known-gaps section", "## Known gaps" in text)
    gaps = text.split("## Known gaps", 1)[-1]
    check("it names more than one gap", gaps.count("\n- ") >= 3, str(gaps.count("\n- ")))
    check("the HTML-comment injection gap is named", "HTML-comment" in gaps)
    check("the plausible-false-fact limit is named", "Plausible-false" in gaps)
    check("residual risks appear on the boundaries too",
          text.count("**Residual risk:**") >= 3, str(text.count("**Residual risk:**")))


def test_the_document_is_reachable_and_governed() -> None:
    print("\n- a security document nobody can find is not published -")
    linked_from = []
    for doc in ("README.md", "SECURITY.md", "docs/README.md"):
        path = ROOT / doc
        if path.is_file() and "THREAT_MODEL" in path.read_text(encoding="utf-8"):
            linked_from.append(doc)
    check("it is linked from at least one entry point", bool(linked_from),
          "nothing links to it")
    print(f"       (linked from: {', '.join(linked_from) or 'nowhere'})")


def main() -> int:
    for fn in (test_the_document_exists_and_has_boundaries,
               test_every_boundary_names_an_owner_that_exists,
               test_every_boundary_has_two_sides,
               test_every_claim_names_a_test_that_exists,
               test_the_known_gaps_are_written_down,
               test_the_document_is_reachable_and_governed):
        fn()
    print(f"\nthreat model: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
