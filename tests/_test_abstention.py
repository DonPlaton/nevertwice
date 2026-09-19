#!/usr/bin/env python3
"""The abstention ladder: six nested policies, and the lock that keeps them honest.

`ABSTENTION_F1.md` declares the ladder before the measurement. This suite pins the two
properties the measurement depends on and cannot check itself:

* **nesting** -- `kept(P_i+1)` is a subset of `kept(P_i)`, so the family is one
  dimension and the curve is a curve rather than six unrelated points;
* **independence from the answer key** -- no policy may consult `mutate`, the mutant
  records or any measurement harness. An abstention rule fitted to the oracle would make
  the precision column circular *and* quietly contaminate the recall column with it.

Every case here is hand-written source. The corpus is not needed and is not touched.

Run:  python tests/_test_abstention.py
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
import abstain as A  # noqa: E402
import blast_radius_deleted as br  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def kept(before: dict, after: dict, policy: str) -> set[tuple[str, str, int]]:
    """Findings the checker emits under `policy`, as (qualname, path, lineno)."""
    verdict = br.check_sources(before, after, scan=after)
    return {(f.qualname, f.path, f.lineno)
            for f in A.decide(verdict, after, policy)}


def quals(before: dict, after: dict, policy: str) -> set[str]:
    return {q for q, _p, _l in kept(before, after, policy)}


# ---------------------------------------------------------------------------
# P0 is the mechanism as measured, unchanged
# ---------------------------------------------------------------------------

def test_emit_all_is_exactly_what_the_checker_reports() -> None:
    print("\n- P0 emit-all reproduces the measured mechanism -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1)\n"}
    verdict = br.check_sources(before, after, scan=after)
    high = {(q, r.path, r.lineno)
            for q, refs in verdict.unhandled.items() for r in refs
            if r.confidence == "high"}
    check("P0 emits every high-confidence unhandled reference and no more",
          kept(before, after, "emit-all") == high,
          str(kept(before, after, "emit-all")) + " vs " + str(high))
    check("and the broken call is among them", "render" in quals(before, after, "emit-all"))


# ---------------------------------------------------------------------------
# P1 -- a signature change cannot break a bare mention
# ---------------------------------------------------------------------------

def test_a_bare_mention_survives_p0_and_not_p1() -> None:
    print("\n- P1 no-bare-mentions -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nhandler = render\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nhandler = render\n"}
    check("P0 reports the mention", "render" in quals(before, after, "emit-all"))
    check("P1 does not", "render" not in quals(before, after, "no-bare-mentions"),
          str(kept(before, after, "no-bare-mentions")))


def test_p1_keeps_a_real_call() -> None:
    """The rule must cost nothing on the shape it is not aimed at."""
    print("\n- P1 does not touch calls -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1)\n"}
    check("the stale call survives P1", "render" in quals(before, after, "no-bare-mentions"))


def test_p1_keeps_an_import_of_a_removed_symbol() -> None:
    """`removed` is not `signature`: an import of a vanished name really does break."""
    print("\n- P1 is scoped to signature changes -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n"}
    after = {"lib.py": "def other(a):\n    return a\n",
             "app.py": "from lib import render\n"}
    check("the dangling import survives P1",
          "render" in quals(before, after, "no-bare-mentions"),
          str(kept(before, after, "no-bare-mentions")))


# ---------------------------------------------------------------------------
# P2 -- arity through a splat is not knowable
# ---------------------------------------------------------------------------

def test_a_star_call_survives_p1_and_not_p2() -> None:
    print("\n- P2 no-star-calls -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(*args)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(*args)\n"}
    check("P1 reports the splat call",
          "render" in quals(before, after, "no-bare-mentions"))
    check("P2 does not", "render" not in quals(before, after, "no-star-calls"),
          str(kept(before, after, "no-star-calls")))


def test_p2_keeps_a_plain_call() -> None:
    print("\n- P2 does not touch plain calls -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1)\n"}
    check("the plain stale call survives P2", "render" in quals(before, after, "no-star-calls"))


# ---------------------------------------------------------------------------
# P3 -- an unknown receiver may or may not be this class
# ---------------------------------------------------------------------------

def test_an_unknown_receiver_survives_p2_and_not_p3() -> None:
    print("\n- P3 no-unknown-receiver -")
    # `send` is at or under the checker's short-name confidence floor and would never be
    # emitted at all; a name that clears it is what makes this case test P3 rather than
    # `_confidence`.
    lib = "class Mailer:\n    def deliver_now(self, a):\n        return a\n"
    lib2 = "class Mailer:\n    def deliver_now(self, a, b):\n        return a\n"
    caller = "def go(backend):\n    return backend.deliver_now(1)\n"
    before = {"lib.py": lib, "app.py": caller}
    after = {"lib.py": lib2, "app.py": caller}
    check("P2 reports the call on an unbound receiver",
          any(q.endswith("deliver_now") for q in quals(before, after, "no-star-calls")),
          str(kept(before, after, "no-star-calls")))
    check("P3 does not",
          not any(q.endswith("deliver_now")
                  for q in quals(before, after, "no-unknown-receiver")),
          str(kept(before, after, "no-unknown-receiver")))


def test_p3_keeps_a_module_level_function() -> None:
    """The receiver rule applies to members. A plain function has no receiver."""
    print("\n- P3 is scoped to methods -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1)\n"}
    check("the module-level call survives P3",
          "render" in quals(before, after, "no-unknown-receiver"))


# ---------------------------------------------------------------------------
# P4 -- what a kind change breaks depends on use, not on the name
# ---------------------------------------------------------------------------

def test_a_kind_change_survives_p3_and_not_p4() -> None:
    print("\n- P4 no-kind-changes -")
    before = {"lib.py": "def Widget(a):\n    return a\n",
              "app.py": "from lib import Widget\n\nWidget(1)\n"}
    after = {"lib.py": "class Widget:\n    def __init__(self, a):\n        self.a = a\n",
             "app.py": "from lib import Widget\n\nWidget(1)\n"}
    p3 = quals(before, after, "no-unknown-receiver")
    p4 = quals(before, after, "no-kind-changes")
    check("P3 reports the kind change", "Widget" in p3, str(p3))
    check("P4 does not", "Widget" not in p4, str(p4))


# ---------------------------------------------------------------------------
# P5 -- emit only what can be simulated
# ---------------------------------------------------------------------------

def test_p5_keeps_a_call_that_provably_cannot_bind() -> None:
    print("\n- P5 decidable-only, on a call that really fails -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1)\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1)\n"}
    check("the call that cannot bind survives the strictest policy",
          "render" in quals(before, after, "decidable-only"),
          str(kept(before, after, "decidable-only")))


def test_p5_drops_a_call_that_still_binds() -> None:
    """The contract change is real; *this* call site survives it, and P5 can tell.

    `render(a, b)` -> `render(a, b, c)` breaks two-argument callers, so the checker
    reports the change. `render(1, 2, 3)` binds against the new definition anyway. Every
    weaker policy emits it; only the one that simulates the call knows to keep quiet.
    """
    print("\n- P5 on a call that still binds -")
    before = {"lib.py": "def render(a, b):\n    return a\n",
              "app.py": "from lib import render\n\nrender(1, 2, 3)\n"}
    after = {"lib.py": "def render(a, b, c):\n    return a\n",
             "app.py": "from lib import render\n\nrender(1, 2, 3)\n"}
    check("the weaker policies do emit it, so the case is not vacuous",
          "render" in quals(before, after, "no-kind-changes"),
          str(kept(before, after, "no-kind-changes")))
    check("a still-binding call is silence under P5",
          "render" not in quals(before, after, "decidable-only"),
          str(kept(before, after, "decidable-only")))


def test_p5_keeps_an_import_of_a_symbol_that_left_its_module() -> None:
    print("\n- P5 on a dangling import -")
    before = {"lib.py": "def render(a):\n    return a\n",
              "app.py": "from lib import render\n"}
    after = {"lib.py": "def other(a):\n    return a\n",
             "app.py": "from lib import render\n"}
    check("the import that will actually raise survives P5",
          "render" in quals(before, after, "decidable-only"),
          str(kept(before, after, "decidable-only")))


# ---------------------------------------------------------------------------
# the two structural properties
# ---------------------------------------------------------------------------

def test_the_ladder_is_nested() -> None:
    print("\n- the ladder is nested, on every case above -")
    cases = [
        ({"lib.py": "def render(a):\n    return a\n",
          "app.py": "from lib import render\n\nrender(1)\nhandler = render\nrender(*a)\n"},
         {"lib.py": "def render(a, b):\n    return a\n",
          "app.py": "from lib import render\n\nrender(1)\nhandler = render\nrender(*a)\n"}),
        ({"lib.py": "class Mailer:\n    def deliver_now(self, a):\n        return a\n",
          "app.py": "def go(backend):\n    return backend.deliver_now(1)\n"},
         {"lib.py": "class Mailer:\n    def deliver_now(self, a, b):\n        return a\n",
          "app.py": "def go(backend):\n    return backend.deliver_now(1)\n"}),
        ({"lib.py": "def Widget(a):\n    return a\n",
          "app.py": "from lib import Widget\n\nWidget(1)\n"},
         {"lib.py": "class Widget:\n    def __init__(self, a):\n        self.a = a\n",
          "app.py": "from lib import Widget\n\nWidget(1)\n"}),
    ]
    broken = []
    for i, (before, after) in enumerate(cases):
        sets = [kept(before, after, p) for p in A.POLICIES]
        for j in range(len(sets) - 1):
            if not sets[j + 1] <= sets[j]:
                broken.append(f"case {i}: {A.POLICIES[j+1]} not a subset of {A.POLICIES[j]}")
    check("every policy keeps a subset of what the previous one keeps",
          not broken, str(broken))
    check("the ladder is the six policies the preregistration named",
          A.POLICIES == ("emit-all", "no-bare-mentions", "no-star-calls",
                         "no-unknown-receiver", "no-kind-changes", "decidable-only"),
          str(A.POLICIES))


def test_no_policy_can_consult_the_answer_key() -> None:
    """The anti-circularity lock. A rule fitted to the oracle measures nothing."""
    print("\n- abstain.py cannot see the answer key -")
    import ast as _ast
    tree = _ast.parse((LAB / "abstain.py").read_text(encoding="utf-8"))
    # Prose is not code. `abstain.py`'s docstring names the harness it must not import,
    # because a rule whose reason is unwritten is a rule that gets removed by the next
    # person. The lock reads imports, calls and non-docstring literals instead.
    docstrings = {id(n.body[0].value) for n in _ast.walk(tree)
                  if isinstance(n, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                                    _ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], _ast.Expr)
                  and isinstance(n.body[0].value, _ast.Constant)
                  and isinstance(n.body[0].value.value, str)}
    imported: set[str] = set()
    used: set[str] = set()
    literals: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported.update(a.name for a in node.names)
        elif isinstance(node, _ast.Name):
            used.add(node.id)
        elif isinstance(node, _ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, _ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                literals.append(node.value)
    banned = {"mutate", "verify_mutants", "measure_blast_radius", "measure_together",
              "measure_ratchet", "measure_scale", "corpus_census"}
    check("no import of the oracle, the mutants or any measurement harness",
          not (imported & banned), str(sorted(imported & banned)))
    check("and no call into one of them", not (used & banned), str(sorted(used & banned)))
    check("no policy reads the answer key off disk",
          not any("mutants.json" in s or "corpus_census" in s for s in literals),
          str([s for s in literals if "json" in s]))
    check("and no policy takes a truth argument",
          not (used & {"is_true_finding", "truth", "truths"}),
          str(sorted(used & {"is_true_finding", "truth", "truths"})))
    check("the prose may still name what it must not import, and does",
          "measure_blast_radius" in (_ast.get_docstring(tree) or ""))


def test_an_unknown_policy_is_an_error_not_a_default() -> None:
    """Silently falling back to emit-all would report P5's row with P0's numbers."""
    print("\n- an unknown policy name raises -")
    before = {"lib.py": "def render(a):\n    return a\n"}
    after = {"lib.py": "def render(a, b):\n    return a\n"}
    verdict = br.check_sources(before, after, scan=after)
    raised = None
    try:
        A.decide(verdict, after, "no-such-policy")
    except Exception as exc:  # noqa: BLE001 - the type is the assertion below
        raised = exc
    check("decide() raises on an unknown policy", isinstance(raised, ValueError),
          repr(raised))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_emit_all_is_exactly_what_the_checker_reports,
               test_a_bare_mention_survives_p0_and_not_p1,
               test_p1_keeps_a_real_call,
               test_p1_keeps_an_import_of_a_removed_symbol,
               test_a_star_call_survives_p1_and_not_p2,
               test_p2_keeps_a_plain_call,
               test_an_unknown_receiver_survives_p2_and_not_p3,
               test_p3_keeps_a_module_level_function,
               test_a_kind_change_survives_p3_and_not_p4,
               test_p5_keeps_a_call_that_provably_cannot_bind,
               test_p5_drops_a_call_that_still_binds,
               test_p5_keeps_an_import_of_a_symbol_that_left_its_module,
               test_the_ladder_is_nested,
               test_no_policy_can_consult_the_answer_key,
               test_an_unknown_policy_is_an_error_not_a_default):
        fn()
    print("\nabstention ladder: " + str(PASSED) + " passed, " + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
