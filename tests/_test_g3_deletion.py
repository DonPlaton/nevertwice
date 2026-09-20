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


def _seal_history() -> list[dict]:
    """Every COMMITTED version of the seal, oldest first."""
    revs = _git("log", "--format=%H", "--", SEAL_REL).decode().split()
    return [json.loads(_git("show", rev + ":" + SEAL_REL).decode("utf-8"))
            for rev in reversed(revs)]


def _digests_available_to(entry: dict, history: list[dict]) -> set[str]:
    """Digests the seal is allowed to say this amendment replaced.

    The `from` at the head of a file's amendment chain has nothing in the current seal to
    link it to, so it is the one digest a fabricated entry could name freely. History knows:
    the seal is a tracked file, and a digest no revision of it ever recorded was never
    replaced by anything.
    """
    name = str(entry.get("file"))
    written = (name, entry.get("from"), entry.get("to"))
    available: set[str] = set()
    for doc in history:
        if any((str(o.get("file")), o.get("from"), o.get("to")) == written
               for o in doc.get("amendments") or []):
            break                     # from here on the entry is its own evidence
        digest = (doc.get("frozen_code") or {}).get(name)
        if digest:
            available.add(digest)
    return available


#: What a `from` may be measured against - stated as fixtures, because the answer is not
#: obvious and the wrong answer is invisible. A seal revision carries BOTH the freeze and the
#: amendments, so reading the amendments of every revision lets a forged entry witness itself
#: the moment it is committed: red in the worktree, green one commit later, with its own
#: revision as its only evidence. A digest is confirmed by what the FREEZE recorded before the
#: entry claiming to replace it was written, and by nothing else.
A, B, C = "a" * 64, "b" * 64, "c" * 64


def _rev(frozen: str, *amendments: tuple[str, str]) -> dict:
    return {"frozen_code": {"f.py": frozen},
            "amendments": [{"file": "f.py", "from": f, "to": t} for f, t in amendments]}


HEAD_FIXTURES: tuple[tuple[str, dict, list[dict], bool], ...] = (
    ("a digest the freeze recorded before the amendment was written",
     {"file": "f.py", "from": A, "to": B}, [_rev(A), _rev(B, (A, B))], True),
    ("a digest whose only witness is the revision that carries the amendment",
     {"file": "f.py", "from": C, "to": B}, [_rev(A), _rev(B, (C, B))], False),
    ("a revert, whose head is a value the freeze held two revisions ago",
     {"file": "f.py", "from": A, "to": B}, [_rev(A), _rev(B, (A, B)), _rev(A, (A, B), (B, A))],
     True),
    ("an uncommitted forgery, judged against the whole committed history",
     {"file": "f.py", "from": C, "to": B}, [_rev(A), _rev(B, (A, B))], False),
    ("a head the freeze never recorded for this file, whatever it held for another",
     {"file": "f.py", "from": A, "to": B},
     [{"frozen_code": {"g.py": A}}, _rev(B, (A, B))], False),
)


def test_a_from_is_confirmed_by_the_freeze_and_not_by_its_own_entry() -> None:
    print("\n- what a replaced digest may be measured against -")
    for label, entry, history, admissible in HEAD_FIXTURES:
        got = entry.get("from") in _digests_available_to(entry, history)
        check(("accepted: " if admissible else "refused: ") + label, got is admissible,
              "accepted" if got else "refused")


def test_every_amendment_answers_to_the_files_and_not_to_itself() -> None:
    """Recording two digests is not the same as the two digests being true.

    The check above asks whether an amendment *has* a `from` and a `to`. It cannot tell a
    re-recorded digest from an invented one, so an entry naming sixty-four characters of noise
    would pass it while the freeze it claims to amend says something else - the same
    "written" against "measured" distinction that this repository keeps having to close.
    Four properties are measurable, so they are measured:

      * the chain - each amendment's `from` is the previous amendment's `to` for that file;
      * the head of the chain - the first `from` for a file is a digest the FREEZE recorded
        before this entry was written, which is what makes an invented one visible. Not "any
        digest any revision ever mentions": a revision carries the freeze and the amendments
        together, so reading the amendments too lets a forged entry witness itself the moment
        it is committed - red in the worktree, green one commit later, on its own evidence;
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

    # An amendment records that the frozen bytes changed. `from == to` records that they did
    # not, and the head rule above would accept it - the freeze really did hold that digest
    # before the entry was written. There is no edit it could describe, so it is malformed.
    inert = [str(e.get("file")) for e in amendments if e.get("from") == e.get("to")]
    check("and no amendment claims to replace a digest with itself", not inert, str(inert))

    chains: dict[str, list[dict]] = {}
    for entry in amendments:
        chains.setdefault(str(entry.get("file")), []).append(entry)

    broken = [f"{name}[{i}]" for name, chain in sorted(chains.items())
              for i in range(1, len(chain)) if chain[i].get("from") != chain[i - 1].get("to")]
    check("each amendment starts where the previous one for that file ended", not broken,
          str(broken))

    history = _seal_history()
    invented = [name for name, chain in sorted(chains.items())
                if chain[0].get("from") not in _digests_available_to(chain[0], history)]
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
               test_every_amendment_answers_to_the_files_and_not_to_itself,
               test_a_from_is_confirmed_by_the_freeze_and_not_by_its_own_entry):
        fn()
    print("\nG3, the deletion pinned: " + str(PASSED) + " passed, "
          + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
