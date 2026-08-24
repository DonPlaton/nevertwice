#!/usr/bin/env python3
"""No absolute comparative claim survives in a tracked document.

A superlative about other systems - "every other memory system", "no competitor
does X", "the field does not measure Y" - is not falsifiable and not checkable. It
also rots: the sentence keeps asserting a thing about a landscape that moved on.
The rule this suite enforces is that such a claim must be replaced by a **dated,
scoped, protocol-linked** statement: which systems, measured or surveyed how, when.

An absolute about the project's OWN behaviour ("the only network calls Nevertwice
makes are ...") is a different thing - it is checkable against this repository - so
those are allowed by name, each with the reason it is not a comparative claim.
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

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# Phrasings that assert something about every other system, or about "the field",
# without naming a scope or a date.
ABSOLUTE_PATTERNS = [
    r"\bevery other (memory|system|tool|competitor|agent|vendor)\b",
    r"\bno other (memory|system|tool|competitor|vendor)\b",
    r"\bnone of the competitors?\b",
    r"\ball competitors\b",
    r"\bevery competitor\b",
    r"\bevery leader\b",
    r"\bno competitor\b",
    r"\balmost no competitor\b",
    r"\bbeats every\b",
    r"\bleads on every\b",
    r"\bwins every\b",
    r"\bon every metric\b(?!.{0,40}\bin this table\b)",
    r"\bout-?retrieves\b",
    r"\bno one (measures|does|else does)\b",
    r"\bthe field does not\b",
    r"\bthe (whole )?field'?s\b",
    r"\bwhere the field is moving\b",
    r"\bahead of the field\b",
    r"\bmost systems ship\b",
    r"\bunmatched\b",
    r"\bunlike any (other )?\b",
    r"\bbest[- ]in[- ]class\b",
    r"\bindustry[- ]leading\b",
    r"\bworld[- ]class\b",
    r"\bfirst (ever|of its kind)\b",
    r"\bthe only (memory system|system|tool) (that|to)\b",
]
RX = re.compile("|".join(ABSOLUTE_PATTERNS), re.I)

# Matches that are not comparative claims. Each names the document, the exact text,
# and why it is allowed. A new allowance has to be argued for in this list.
ALLOWED: list[tuple[str, str, str]] = [
    ("research/ABSTRACTIVE.md", "the field's load-bearing question",
     "names an open research question rather than claiming superiority over anyone"),
]


def _tracked_markdown() -> list[str] | None:
    try:
        out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted(out.stdout.split()) if out.returncode == 0 else None


def _allowed(doc: str, line: str) -> bool:
    return any(d == doc and text in line for d, text, _ in ALLOWED)


def test_no_absolute_comparative_claims() -> None:
    print("\n- no unfalsifiable comparative claim in a tracked document -")
    tracked = _tracked_markdown()
    if tracked is None:
        print("       (git unavailable - not checked)")
        return

    offenders: list[str] = []
    for doc in tracked:
        text = (ROOT / doc).read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            m = RX.search(line)
            if m and not _allowed(doc, line):
                offenders.append(f"{doc}:{i}: {m.group(0)!r}")
    check(f"all {len(tracked)} tracked documents are free of absolute "
          f"comparative claims", not offenders, "; ".join(offenders[:6]))


def test_allowances_are_still_needed() -> None:
    """An allowance that no longer matches anything is dead weight that would let a
    real claim slip in later under the same wording."""
    print("\n- every allowance still applies and still says why -")
    stale = []
    for doc, text, reason in ALLOWED:
        path = ROOT / doc
        lines = (path.read_text(encoding="utf-8", errors="replace").splitlines()
                 if path.exists() else [])
        # An allowance earns its place only while a line both contains the text and
        # is actually flagged by the patterns. Otherwise it is dead weight that would
        # silently cover a real claim later.
        if not any(text in line and RX.search(line) for line in lines):
            stale.append(f"{doc}: {text!r}")
    check("every allowance still covers a line the patterns flag", not stale,
          "; ".join(stale[:4]))
    unexplained = [d for d, _, reason in ALLOWED if not reason.strip()]
    check("every allowance states why it is not a comparative claim",
          not unexplained, ", ".join(unexplained))


def test_comparative_sections_carry_a_date() -> None:
    """A comparison is only checkable if the reader knows what it was true of and
    when. Both comparison surfaces must name the run or the survey date."""
    print("\n- the comparative surfaces are dated -")
    for doc, needles in (
        ("docs/COMPARISON.md", ("2026-07-05", "mid-2026")),
        ("docs/BENCHMARKS.md", ("2026-07-05",)),
        ("README.md", ("2026-07-05",)),
    ):
        text = (ROOT / doc).read_text(encoding="utf-8", errors="replace")
        missing = [n for n in needles if n not in text]
        check(f"{doc} dates its comparison", not missing, ", ".join(missing))


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nclaims language: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
