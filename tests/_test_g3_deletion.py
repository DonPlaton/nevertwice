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

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

LAB = ROOT / "research" / "invariants_lab"
SEAL_REL = "research/invariants_lab/heldout_seal.json"

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
    # Two kinds of amendment, and each has to justify itself in its own terms. Before the
    # held-out corpus existed an edit could be *harmless* -- nothing had been measured, so
    # nothing could move. After it exists that claim is no longer available, and
    # `PREREGISTRATION-SHIP.md` §1 calls such an edit a **deviation**. A deviation must
    # say so and must argue, specifically, why it moves no verdict.
    for entry in seal.get("amendments", []):
        name = entry.get("file", "?")
        check("amendment to " + name + " records both digests",
              bool(entry.get("from")) and bool(entry.get("to")))
        check("and says what changed and why", bool(entry.get("change"))
              and bool(entry.get("why")))
        if entry.get("deviation_not_correction"):
            check(name + " is declared a deviation and argues it moves no verdict",
                  bool(entry.get("why_it_moves_no_verdict"))
                  and bool(entry.get("declared_status")))
        else:
            check(name + " claims harmlessness and says why",
                  bool(entry.get("harmless_because")))
    check("the seal was opened against a preregistration",
          "PREREGISTRATION" in str(seal.get("opened_against", "")))


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def _is_digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _digests_this_seal_ever_recorded() -> dict[str, set[str]]:
    """Every digest any COMMITTED version of the seal has held, per frozen file.

    The `from` at the head of a file's amendment chain has nothing in the current seal to
    link it to, so it is the one digest a fabricated entry could name freely. History knows:
    the seal is a tracked file, and a digest no revision of it ever recorded was never
    replaced by anything.
    """
    ever: dict[str, set[str]] = {}
    for rev in _git("log", "--format=%H", "--", SEAL_REL).decode().split():
        old = json.loads(_git("show", rev + ":" + SEAL_REL).decode("utf-8"))
        for name, digest in (old.get("frozen_code") or {}).items():
            ever.setdefault(name, set()).add(digest)
        for entry in old.get("amendments") or []:
            ever.setdefault(str(entry.get("file")), set()).update(
                {entry.get("from"), entry.get("to")})
    return ever


def test_every_amendment_answers_to_the_files_and_not_to_itself() -> None:
    """Recording two digests is not the same as the two digests being true.

    The check above asks whether an amendment *has* a `from` and a `to`. It cannot tell a
    re-recorded digest from an invented one, so an entry naming sixty-four characters of noise
    would pass it while the freeze it claims to amend says something else - the same
    "written" against "measured" distinction that this repository keeps having to close.
    Four properties are measurable, so they are measured:

      * the chain - each amendment's `from` is the previous amendment's `to` for that file;
      * the head of the chain - the first `from` for a file is a digest some committed version
        of this seal really held, which is what makes an invented one visible;
      * the end of the chain - the last `to` is what `frozen_code` now records;
      * portability - `frozen_code` is the sha256 of the bytes GIT holds, not of a worktree
        copy. That is not academic: 23 of these 29 digests were the hash of a CRLF worktree,
        green on this machine and red on every clone, until `8f9c94b`.

    The portability check goes red while a frozen file is edited and uncommitted. That is the
    freeze working, and it is the same rule the registrars enforce: a digest is recorded
    against committed bytes or it is recorded against nothing.
    """
    print("\n- and every amendment answers to the files -")
    seal = json.loads((LAB / "heldout_seal.json").read_text(encoding="utf-8"))
    frozen = seal.get("frozen_code") or {}
    amendments = seal.get("amendments") or []

    malformed = [str(e.get("file")) for e in amendments
                 if not (_is_digest(e.get("from")) and _is_digest(e.get("to")))]
    check("every recorded digest is a sha256 rather than a sentence", not malformed,
          str(malformed))

    chains: dict[str, list[dict]] = {}
    for entry in amendments:
        chains.setdefault(str(entry.get("file")), []).append(entry)

    broken = [f"{name}[{i}]" for name, chain in sorted(chains.items())
              for i in range(1, len(chain)) if chain[i].get("from") != chain[i - 1].get("to")]
    check("each amendment starts where the previous one for that file ended", not broken,
          str(broken))

    ever = _digests_this_seal_ever_recorded()
    invented = [name for name, chain in sorted(chains.items())
                if chain[0].get("from") not in ever.get(name, set())]
    check("and the first one replaces a digest this seal really held", not invented,
          str(invented))

    dangling = [name for name, chain in sorted(chains.items())
                if chain[-1].get("to") != frozen.get(name)]
    check("the last amendment for a file is what the freeze now records", not dangling,
          str(dangling))

    drifted = []
    for name, digest in sorted(frozen.items()):
        try:
            blob = _git("cat-file", "blob", "HEAD:research/invariants_lab/" + name)
        except subprocess.CalledProcessError:
            drifted.append(name + " (git has no such file at HEAD)")
            continue
        if hashlib.sha256(blob).hexdigest() != digest:
            drifted.append(name + " (the HEAD blob hashes to something else)")
    check("every frozen digest is the hash of the bytes git holds, so a clone agrees",
          not drifted, "; ".join(drifted[:6]))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_registry_is_empty,
               test_no_mechanism_module_moved_into_the_package,
               test_the_verdict_is_written_down_and_says_no_go,
               test_the_seal_records_what_was_frozen_and_every_amendment,
               test_every_amendment_answers_to_the_files_and_not_to_itself):
        fn()
    print("\nG3, the deletion pinned: " + str(PASSED) + " passed, "
          + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
