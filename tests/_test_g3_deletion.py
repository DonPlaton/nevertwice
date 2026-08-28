#!/usr/bin/env python3
"""G3: the `invariants/v3` run promoted nothing, and this is what that looks like.

A decision nobody executes is a decision nobody made. `T4` deleted `blast_radius` from
`nevertwice/invariants/` when it failed its gates and pinned the deletion with tests. This
run reached **NO-GO** — [`VERDICT_G1.md`] — and the consequence is the same: the package
stays empty.

These are **pins on a decision**, not tests of new behaviour. They were written after the
verdict, so the TDD cycle does not apply to them in its usual form; instead each one was
checked by temporarily making it false, to be sure it can go red at all. What they defend
against is the quiet re-addition of a checker that has not passed a gate — which is the
only way a NO-GO stops being a NO-GO without anybody saying so.

Run:  python tests/_test_g3_deletion.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

LAB = ROOT / "research" / "invariants_lab"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_the_registry_is_empty() -> None:
    print("\n- nothing was promoted -")
    import nevertwice.invariants as inv
    check("the invariant registry is empty", inv.INVARIANTS == {}, str(inv.INVARIANTS))
    check("and the package re-exports nothing", inv.__all__ == [], str(inv.__all__))
    check("the package says why in its own docstring",
          "empty" in (inv.__doc__ or "").lower())


def test_no_mechanism_module_moved_into_the_package() -> None:
    """The lab is where a mechanism lives until a gate says otherwise."""
    print("\n- every mechanism is still in the lab -")
    package = ROOT / "nevertwice" / "invariants"
    stray = sorted(p.name for p in package.glob("*.py") if p.name != "__init__.py")
    check("no module other than __init__ is in the package", not stray, str(stray))
    for name in ("blast_radius_deleted.py", "abstain.py", "surface.py", "ratchet.py",
                 "scale.py", "complexity.py"):
        check(name + " is in the lab, not the package", (LAB / name).is_file())


def test_the_verdict_is_written_down_and_says_no_go() -> None:
    print("\n- the verdict exists and is legible -")
    verdict = LAB / "VERDICT_G1.md"
    check("VERDICT_G1.md exists", verdict.is_file())
    if not verdict.is_file():
        return
    text = verdict.read_text(encoding="utf-8")
    check("it says NO-GO", "NO-GO" in text)
    for gate in ("G-A", "G-B", "G-C"):
        check("it names " + gate, gate in text)
    check("it distinguishes unevaluable from failed",
          "UNEVALUABLE" in text and "not the same as" in text)
    check("and it says what would change the verdict",
          "What would change the verdict" in text)


def test_the_seal_records_what_was_frozen_and_every_amendment() -> None:
    """A freeze whose amendments are invisible is a freeze nobody can audit."""
    print("\n- the freeze and its amendments -")
    seal = json.loads((LAB / "heldout_seal.json").read_text(encoding="utf-8"))
    check("the freeze names at least 25 files", len(seal.get("frozen_code") or {}) >= 25,
          str(len(seal.get("frozen_code") or {})))
    for entry in seal.get("amendments", []):
        check("amendment to " + entry.get("file", "?") + " records both digests",
              bool(entry.get("from")) and bool(entry.get("to")))
        check("and says why it was harmless", bool(entry.get("harmless_because")))
    check("the seal was opened against a preregistration",
          "PREREGISTRATION" in str(seal.get("opened_against", "")))


def main() -> int:
    for fn in (test_the_registry_is_empty,
               test_no_mechanism_module_moved_into_the_package,
               test_the_verdict_is_written_down_and_says_no_go,
               test_the_seal_records_what_was_frozen_and_every_amendment):
        fn()
    print("\nG3, the deletion pinned: " + str(PASSED) + " passed, "
          + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
