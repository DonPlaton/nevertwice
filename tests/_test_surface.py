#!/usr/bin/env python3
"""F2: scoping a checker to a surface, and keeping the word "declared" honest.

`SURFACE_F2.md` §1: the declared surface the task asks for is not present in found
history. Three of 859 confirmed positives name a symbol its defining module lists in
`__all__`. What is available instead are **conventions**, and the difference is the whole
point of the task, so it is pinned here too: `surface.DECLARED` names the one surface that
is a declaration, and the suite fails if a convention is quietly added to it.

Like `abstain`, no surface may consult the answer key -- asserted statically below.

Run:  python tests/_test_surface.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

LAB = ROOT / "research" / "invariants_lab"
sys.path.insert(0, str(LAB))
import abstain  # noqa: E402
import blast_radius_deleted as br  # noqa: E402
import surface as S  # noqa: E402

PASSED = 0
FAILED = 0
NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def kept(before: dict, after: dict, surface: str,
         policy: str = "emit-all") -> set[str]:
    verdict = br.check_sources(before, after, scan=after)
    findings = abstain.decide(verdict, after, policy)
    changes = {c.qualname: c for c in verdict.contract_changes}
    return {f.qualname for f in S.apply(findings, after, changes, surface)}


# ---------------------------------------------------------------------------

def test_everything_changes_nothing() -> None:
    print(NL + "- W0 everything is the mechanism unchanged -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1)\n"}
    check("the stale call survives W0", "render" in kept(before, after, "everything"))


def test_a_private_symbol_is_dropped_by_convention() -> None:
    print(NL + "- W1 public-by-convention -")
    before = {"lib.py": "def _render_row(a):\n    return a\n",
              "app.py": "from lib import _render_row\n\n_render_row(1)\n"}
    after = {"lib.py": "def _render_row(a, b):\n    return a\n",
             "app.py": "from lib import _render_row\n\n_render_row(1)\n"}
    check("W0 reports the underscored symbol",
          "_render_row" in kept(before, after, "everything"))
    check("W1 does not",
          "_render_row" not in kept(before, after, "public-by-convention"))


def test_a_dunder_is_not_private() -> None:
    """`__init__` starts with an underscore and is the most public thing a class has.

    Tested on the predicate rather than end to end: the checker matches references by
    short name, and `Mailer(1)` mentions `Mailer`, never `__init__`, so a
    constructor-change case produces no finding for a surface to filter. Asserting
    "nothing survived" there would pass whatever the rule did.
    """
    print(NL + "- W1 does not read a dunder as private -")
    check("__init__ is public", S.is_public_by_convention("Mailer.__init__"))
    check("__enter__ is public", S.is_public_by_convention("Session.__enter__"))
    check("_render_row is not", not S.is_public_by_convention("_render_row"))
    check("a single trailing underscore is not a dunder",
          not S.is_public_by_convention("_private_"))
    check("and a public method of a public class is public",
          S.is_public_by_convention("Mailer.deliver_now"))


def test_a_private_owner_makes_its_method_private() -> None:
    print(NL + "- W1 reads the whole qualname, not just the last component -")
    before = {"lib.py": "class _Engine:\n    def deliver_now(self, a):\n        return a\n",
              "app.py": "from lib import _Engine\n\n_Engine().deliver_now(1)\n"}
    after = {"lib.py": "class _Engine:\n    def deliver_now(self, a, b):\n        return a\n",
             "app.py": "from lib import _Engine\n\n_Engine().deliver_now(1)\n"}
    got = kept(before, after, "public-by-convention")
    check("a method of a private class is private too",
          not any("deliver_now" in q for q in got), str(got))


def test_a_test_file_consumer_is_dropped_by_w2() -> None:
    print(NL + "- W2 shipped-consumers -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "tests/test_lib.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "tests/test_lib.py": "from lib import render\n\nrender(1)\n"}
    check("W0 reports a stale caller in a test file",
          "render" in kept(before, after, "everything"))
    check("W2 does not", "render" not in kept(before, after, "shipped-consumers"))


def test_w2_keeps_a_package_consumer() -> None:
    print(NL + "- W2 does not touch package code -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "pkg/app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "pkg/app.py": "from lib import render\n\nrender(1)\n"}
    check("the package caller survives W2",
          "render" in kept(before, after, "shipped-consumers"))


def test_declared_public_needs_an_actual_declaration() -> None:
    print(NL + "- W4 declared-public, the only surface that is a declaration -")
    caller = "from lib import render\n\nrender(1)\n"
    before_no = {"lib.py": "def render(a):\n    return a\n", "app.py": caller}
    after_no = {"lib.py": "def render(a, b):\n    return a\n", "app.py": caller}
    check("a module with no __all__ declares nothing, and W4 is silent",
          "render" not in kept(before_no, after_no, "declared-public"),
          str(kept(before_no, after_no, "declared-public")))
    before_yes = {"lib.py": '__all__ = ["render"]\n\n\ndef render(a):\n    return a\n',
                  "app.py": caller}
    after_yes = {"lib.py": '__all__ = ["render"]\n\n\ndef render(a, b):\n    return a\n',
                 "app.py": caller}
    check("a module that declares the symbol is in scope",
          "render" in kept(before_yes, after_yes, "declared-public"),
          str(kept(before_yes, after_yes, "declared-public")))
    before_other = {"lib.py": '__all__ = ["other"]\n\n\ndef render(a):\n    return a\n',
                    "app.py": caller}
    after_other = {"lib.py": '__all__ = ["other"]\n\n\ndef render(a, b):\n    return a\n',
                   "app.py": caller}
    check("a module that declares something else is not in scope",
          "render" not in kept(before_other, after_other, "declared-public"))


def test_both_is_the_intersection_of_its_two_levers() -> None:
    print(NL + "- W3 both -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "pkg/app.py": "from lib import render\n\nrender(1)\n",
              "tests/test_lib.py": "from lib import render\n\nrender(2)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "pkg/app.py": "from lib import render\n\nrender(1)\n",
             "tests/test_lib.py": "from lib import render\n\nrender(2)\n"}
    verdict = br.check_sources(before, after, scan=after)
    findings = abstain.decide(verdict, after, "emit-all")
    changes = {c.qualname: c for c in verdict.contract_changes}
    sites = {s: {(f.qualname, f.path, f.lineno)
                 for f in S.apply(findings, after, changes, s)}
             for s in S.SURFACES}
    check("W3 keeps exactly what W1 and W2 both keep",
          sites["both"] == sites["public-by-convention"] & sites["shipped-consumers"],
          str(sites["both"]))


def test_every_surface_keeps_a_subset_of_everything() -> None:
    print(NL + "- no surface invents a finding -")
    before = {"lib.py": "def render(a):\n    return a\n"
                        "def _hidden(a):\n    return a\n",
              "pkg/app.py": "from lib import render, _hidden\n\nrender(1)\n_hidden(1)\n",
              "tests/test_lib.py": "from lib import render\n\nrender(2)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n"
                       "def _hidden(a, b):\n    return a\n",
             "pkg/app.py": "from lib import render, _hidden\n\nrender(1)\n_hidden(1)\n",
             "tests/test_lib.py": "from lib import render\n\nrender(2)\n"}
    verdict = br.check_sources(before, after, scan=after)
    findings = abstain.decide(verdict, after, "emit-all")
    changes = {c.qualname: c for c in verdict.contract_changes}
    base = {(f.qualname, f.path, f.lineno)
            for f in S.apply(findings, after, changes, "everything")}
    bad = []
    for s in S.SURFACES:
        got = {(f.qualname, f.path, f.lineno)
               for f in S.apply(findings, after, changes, s)}
        if not got <= base:
            bad.append(s)
    check("every surface is a subset of W0", not bad, str(bad))


def test_the_word_declared_is_reserved() -> None:
    """A convention promoted to a declaration is the failure this task is about."""
    print(NL + "- 'declared' names exactly the one surface that is one -")
    check("DECLARED holds only declared-public", S.DECLARED == ("declared-public",),
          str(S.DECLARED))
    check("and the conventions are labelled as conventions",
          set(S.CONVENTIONS) == {"public-by-convention", "shipped-consumers", "both"},
          str(S.CONVENTIONS))
    check("every surface is in exactly one of the two groups, or is the baseline",
          set(S.SURFACES) == {"everything"} | set(S.CONVENTIONS) | set(S.DECLARED),
          str(S.SURFACES))


def test_no_surface_can_consult_the_answer_key() -> None:
    print(NL + "- surface.py cannot see the answer key -")
    import ast as _ast
    tree = _ast.parse((LAB / "surface.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    used: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, _ast.Name):
            used.add(node.id)
        elif isinstance(node, _ast.Attribute):
            used.add(node.attr)
    banned = {"mutate", "verify_mutants", "measure_blast_radius", "measure_abstention",
              "corpus_census"}
    check("no import of the oracle or any harness", not (imported & banned),
          str(sorted(imported & banned)))
    check("and no call into one", not (used & banned), str(sorted(used & banned)))


def test_an_unknown_surface_is_an_error() -> None:
    print(NL + "- an unknown surface name raises -")
    raised = None
    try:
        S.apply([], {}, {}, "no-such-surface")
    except Exception as exc:  # noqa: BLE001 - the type is the assertion
        raised = exc
    check("apply() raises ValueError", isinstance(raised, ValueError), repr(raised))


def main() -> int:
    for fn in (test_everything_changes_nothing,
               test_a_private_symbol_is_dropped_by_convention,
               test_a_dunder_is_not_private,
               test_a_private_owner_makes_its_method_private,
               test_a_test_file_consumer_is_dropped_by_w2,
               test_w2_keeps_a_package_consumer,
               test_declared_public_needs_an_actual_declaration,
               test_both_is_the_intersection_of_its_two_levers,
               test_every_surface_keeps_a_subset_of_everything,
               test_the_word_declared_is_reserved,
               test_no_surface_can_consult_the_answer_key,
               test_an_unknown_surface_is_an_error):
        fn()
    print(NL + "surfaces: " + str(PASSED) + " passed, " + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
