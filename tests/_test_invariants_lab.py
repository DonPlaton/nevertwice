#!/usr/bin/env python3
"""Controls for the answer key that Phase C builds, and they run without the corpus.

The corpus itself lives outside git -- multi-gigabyte, disposable, regenerable -- so a
suite that needed it would be red on every fresh clone. What *can* be pinned here is the
part that decides whether a commit is a positive: `sigscan`'s extractor and `mutate`'s
arity rule. Both are held to cases written by hand, including the three the checker under
test is known to get wrong.

The direction of these controls matters. `research/BLAST_RADIUS_PRECISION.md` reports a
labeller that was wrong five times, and four of the corrections moved sites the way its
author was biased. So the key is checked in **both** directions: it must be able to say
"broken" about code that is broken, and "fine" about code that is fine. A key that only
ever says one of those cannot produce a precision or a recall.

Run:  python tests/_test_invariants_lab.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import mutate as M  # noqa: E402
import sigscan as S  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _def(src: str, qualname: str) -> S.Def:
    return S.scan_defs(src)[qualname]


def _call(src: str, name: str) -> S.RefSite:
    return next(s for s in S.scan_refs(src, {name}) if s.is_call)


def _fails(callsite: str, definition: str, qualname: str | None = None) -> str | None:
    name = (qualname or "").rsplit(".", 1)[-1] or definition.split("def ")[1].split("(")[0]
    return M.call_fails(_call(callsite, name), _def(definition, qualname or name))


# ---------------------------------------------------------------------------
# the key must be able to say BROKEN
# ---------------------------------------------------------------------------


def test_the_key_reports_a_real_type_error() -> None:
    print("\n- positive controls: the reverted call really does raise -")
    check("a new required parameter breaks an existing call",
          _fails("f(1)\n", "def f(a, b): pass\n") is not None)
    check("too many positionals breaks a call",
          _fails("f(1, 2, 3)\n", "def f(a, b): pass\n") is not None)
    check("a keyword the callee dropped breaks a call",
          _fails("f(1, mode='x')\n", "def f(a): pass\n") is not None)
    check("a parameter promoted to required keyword-only breaks a call",
          _fails("f(1)\n", "def f(a, *, mode): pass\n") is not None)
    check("a method losing a parameter breaks its call site",
          _fails("obj.m(1, 2)\n", "class C:\n    def m(self, a): pass\n", "C.m") is not None)


# ---------------------------------------------------------------------------
# and it must be able to say FINE -- the direction the last labeller drifted
# ---------------------------------------------------------------------------


def test_the_key_accepts_what_still_fits() -> None:
    print("\n- negative controls: a compatible change is not a breakage -")
    check("a defaulted parameter appearing does not break a call",
          _fails("f(1)\n", "def f(a, b=2): pass\n") is None)
    check("a keyword-only parameter with a default does not break a call",
          _fails("f(1)\n", "def f(a, *, mode='x'): pass\n") is None)
    check("*args absorbs extra positionals",
          _fails("f(1, 2, 3)\n", "def f(a, *rest): pass\n") is None)
    check("**kwargs absorbs an unknown keyword",
          _fails("f(1, mode='x')\n", "def f(a, **kw): pass\n") is None)
    check("a required parameter supplied by keyword is supplied",
          _fails("f(a=1, b=2)\n", "def f(a, b): pass\n") is None)
    check("an annotation added to an unchanged parameter list is no change at all",
          S.signature_deltas("def f(a): pass\n", "def f(a: int) -> str: pass\n", "m.py") == [])
    check("a return annotation alone is no change",
          S.signature_deltas("def f(a): pass\n", "def f(a) -> None: pass\n", "m.py") == [])
    check("a class gaining a method is not a signature change",
          S.signature_deltas("class C:\n    def a(self): pass\n",
                             "class C:\n    def a(self): pass\n    def b(self): pass\n",
                             "m.py") == [])


def test_the_key_abstains_where_it_cannot_know() -> None:
    print("\n- abstention: an unprovable call is dropped, not counted -")
    check("*args at the CALL site makes arity unknowable",
          _fails("f(*args)\n", "def f(a, b, c): pass\n") is None)
    check("**kwargs at the call site makes arity unknowable",
          _fails("f(1, **kw)\n", "def f(a, b, c): pass\n") is None)
    check("a class is not judged by the function rule",
          M.call_fails(_call("C(1, 2)\n", "C"), _def("class C: pass\n", "C")) is None)


# ---------------------------------------------------------------------------
# the extractor's own known holes -- D2's defect must not be a hole in the key
# ---------------------------------------------------------------------------


def test_the_extractor_sees_tuple_unpacking() -> None:
    print("\n- the key sees what the checker under test does not -")
    defs = S.scan_defs("A, B, C = load()\n")
    check("tuple unpacking binds every name", {"A", "B", "C"} <= set(defs), str(sorted(defs)))
    defs = S.scan_defs("first, *rest = load()\n")
    check("a starred target binds too", {"first", "rest"} <= set(defs), str(sorted(defs)))
    defs = S.scan_defs("[X, Y] = load()\n")
    check("a list target binds too", {"X", "Y"} <= set(defs), str(sorted(defs)))
    check("a tuple-bound name is therefore not a removal",
          S.signature_deltas("A, B = load()\n", "A, B = load()\n", "m.py") == [])
    check("losing one of a tuple's names IS a removal",
          [d.qualname for d in S.signature_deltas("A, B = load()\n", "A, = load()\n", "m.py")]
          == ["B"])


def test_nested_functions_are_not_public_symbols() -> None:
    print("\n- scope: a nested def is invisible to any caller -")
    defs = S.scan_defs("def outer():\n    def inner(a): pass\n    return inner\n")
    check("the inner function is not recorded", "outer.inner" not in defs, str(sorted(defs)))
    check("the outer one is", "outer" in defs)


def test_module_suffix_matching_is_not_a_substring_test() -> None:
    print("\n- import resolution: a suffix match, not a substring match -")
    check("a relative tail resolves against the defining file",
          M._defines("_utils", "httpx/_utils.py"))
    check("a dotted tail resolves", M._defines("httpx._utils", "httpx/_utils.py"))
    check("a package __init__ resolves by its package name",
          M._defines("httpx", "httpx/__init__.py"))
    check("a different module does not resolve",
          not M._defines("_models", "httpx/_utils.py"))
    check("a longer path than the file has does not resolve",
          not M._defines("a.b.httpx._utils", "httpx/_utils.py"))


def test_the_arity_rule_strips_self_only_for_methods() -> None:
    print("\n- self is bound at the call site, and only for methods -")
    method = _def("class C:\n    def m(self, a): pass\n", "C.m")
    plain = _def("def m(self, a): pass\n", "m")
    check("a method's self is not counted against the caller",
          method.arity()[0] == 1, str(method.arity()))
    check("a module-level function named with self is not a method",
          plain.arity()[0] == 2, str(plain.arity()))


def main() -> int:
    for fn in (test_the_key_reports_a_real_type_error,
               test_the_key_accepts_what_still_fits,
               test_the_key_abstains_where_it_cannot_know,
               test_the_extractor_sees_tuple_unpacking,
               test_nested_functions_are_not_public_symbols,
               test_module_suffix_matching_is_not_a_substring_test,
               test_the_arity_rule_strips_self_only_for_methods):
        fn()
    print(f"\ninvariants lab answer key: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
