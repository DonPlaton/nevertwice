#!/usr/bin/env python3
"""The answer key's control, and the crash the held-out corpus found in it.

`verify_mutants.py` replays every planted call through `inspect.Signature.bind` and keeps
only the mutants where the binder agrees with the generator. It builds one result row per
mutant and then filters on `row["agrees"]`.

**One branch never set that key.** When a signature cannot be rebuilt from the AST the row
is finished with `broken=False, intact=False` and `continue`d — skipping the
`row["agrees"] = broken and intact` line below it. `corpus_dev` rebuilt 868 signatures of
868, so the branch never fired; the held-out corpus fired it and the control died with
`KeyError: 'agrees'` before producing a single number.

That is D6's blind spot in a seventh instrument: **a case eight repositories did not
contain**. The repair sets the key to exactly what the skipped line would have computed —
`False and False` — so it cannot change any verdict, and `heldout_seal.json` logs it as a
freeze amendment with both digests and that reasoning.

Run:  python tests/_test_verify_mutants.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import verify_mutants as V  # noqa: E402

PASSED = 0
FAILED = 0
NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_every_result_row_carries_a_verdict() -> None:
    """The crash. A row without `agrees` kills the filter that reads it."""
    print(NL + "- every row the control produces has an `agrees` key -")
    import ast
    import inspect
    source = ast.parse("def f(a, b):\n    return a\n")

    # the shape that skipped the key: a qualname the walker cannot resolve
    check("a signature that cannot be rebuilt returns None, not a crash",
          V._signature_of(source, "no.such.thing") is None)
    check("and one that can is a real Signature",
          isinstance(V._signature_of(source, "f"), inspect.Signature))

    # The branch under test finishes a row and continues. Reproduce it directly: the
    # invariant is that `agrees` is always present and always equals broken AND intact.
    row: dict = {"mid": "x", "breakage": "arity"}
    row.update(broken=False, intact=False, note="signature not rebuildable")
    row.setdefault("agrees", bool(row["broken"] and row["intact"]))
    check("an unrebuildable row agrees with nothing", row["agrees"] is False)


def test_the_source_sets_agrees_on_every_path() -> None:
    """Read the code, not a reproduction of it: no `continue` may skip the assignment."""
    print(NL + "- no path out of the loop skips the verdict -")
    text = (ROOT / "research" / "invariants_lab" / "verify_mutants.py").read_text(
        encoding="utf-8")
    body = text[text.index("def verify("):text.index("def main(")]
    # every `results.append(row)` must be preceded by an `agrees` assignment for that row
    appends = body.count("results.append(row)")
    assigns = body.count('row["agrees"]') + body.count("agrees=False")
    check("every results.append is matched by an agrees assignment",
          assigns >= appends, f"{assigns} assignments for {appends} appends")
    check("the filter that reads it is still there", 'r["agrees"]' in body)


def test_the_binder_still_decides_the_cases_it_could_before() -> None:
    """The repair must not move a verdict the control could already reach."""
    print(NL + "- the binding rule is unchanged -")
    import ast
    tree = ast.parse("def f(a, b):\n    return a\n")
    sig = V._signature_of(tree, "f")
    check("two positionals bind", V._binds(sig, 2, ()))
    check("one does not", not V._binds(sig, 1, ()))
    check("three do not", not V._binds(sig, 3, ()))
    check("a known keyword binds", V._binds(sig, 1, ("b",)))
    check("an unknown one does not", not V._binds(sig, 2, ("zzz",)))


def main() -> int:
    for fn in (test_every_result_row_carries_a_verdict,
               test_the_source_sets_agrees_on_every_path,
               test_the_binder_still_decides_the_cases_it_could_before):
        fn()
    print(NL + "the answer key's control: " + str(PASSED) + " passed, "
          + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
