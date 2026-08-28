#!/usr/bin/env python3
"""The scale detector's static half, pinned -- including the defect F4 found in it.

Mechanism 5 passed four of four gates in `invariants/v2` and had **no test suite at all**.
Its static recall of 1.000 was measured on cases its own author generated, and F4 put it
against seven quadratic fixes real maintainers wrote and named. The first thing that fell
out was not a recall number: it was that the *axis names* the detector reasons about were
wrong.

`_iterated_name` returned the **callee** for a call, so `for i, x in enumerate(rows)` and
`for j, y in enumerate(cols)` were both the axis `enumerate`, and the detector duly
reported "a loop over 'enumerate' inside a loop over 'enumerate'" on `pytest`'s pprint
module. `for k, v in d.iteritems()` was the axis `iteritems`. Neither is a growth axis; both
are function names, and a declaration naming `enumerate` is a declaration about nothing.

Run:  python tests/_test_scale.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import scale as SC  # noqa: E402

PASSED = 0
FAILED = 0
NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def decl(*names: str) -> SC.Declaration:
    return SC.Declaration.parse({"axes": [
        {"name": n, "unit": "records", "start": 1_000, "target": 50_000_000}
        for n in names]})


def problems(src: str, *names: str) -> list[SC.ScaleProblem]:
    return SC.find_problems(src, "m.py", decl(*names))


# ---------------------------------------------------------------------------
# the premise: no declaration, no finding
# ---------------------------------------------------------------------------

def test_without_a_declaration_it_says_nothing() -> None:
    print(NL + "- no declared axis, no finding, and that is correct behaviour -")
    src = "def f(rows):\n    for a in rows:\n        for b in rows:\n            pass\n"
    check("an empty declaration produces nothing", problems(src) == [])
    check("and a declaration about another name produces nothing",
          problems(src, "columns") == [])
    check("the declared axis produces the finding",
          len(problems(src, "rows")) == 1, str(problems(src, "rows")))


# ---------------------------------------------------------------------------
# the two shapes it claims to detect
# ---------------------------------------------------------------------------

def test_a_nested_walk_over_the_declared_axis() -> None:
    print(NL + "- a loop over the axis inside a loop over the axis -")
    src = "def f(rows):\n    for a in rows:\n        for b in rows:\n            pass\n"
    got = problems(src, "rows")
    check("one finding", len(got) == 1, str(got))
    check("named quadratic", got and got[0].kind == "quadratic")
    check("and it names the axis", got and got[0].axis == "rows")


def test_a_membership_test_inside_a_loop_over_the_axis() -> None:
    print(NL + "- `x in axis` inside a loop over the axis -")
    src = "def f(rows, seen):\n    for a in rows:\n        if a in rows:\n            pass\n"
    got = problems(src, "rows")
    check("one finding", len(got) == 1, str(got))
    check("named quadratic", got and got[0].kind == "quadratic")


def test_materialising_the_whole_axis() -> None:
    print(NL + "- pulling the axis into memory -")
    check("list(axis) is reported", len(problems("x = list(rows)\n", "rows")) == 1)
    check("sorted(axis) is reported", len(problems("x = sorted(rows)\n", "rows")) == 1)
    check("axis.readlines() is reported",
          len(problems("x = rows.readlines()\n", "rows")) == 1)
    check("sum(axis) is not -- it consumes lazily",
          problems("x = sum(rows)\n", "rows") == [])
    check("any(axis) is not either", problems("x = any(rows)\n", "rows") == [])


def test_two_separate_loops_are_not_quadratic() -> None:
    print(NL + "- sequential loops are linear -")
    src = ("def f(rows):\n    for a in rows:\n        pass\n"
           "    for b in rows:\n        pass\n")
    check("two loops in sequence produce nothing", problems(src, "rows") == [],
          str(problems(src, "rows")))


# ---------------------------------------------------------------------------
# the defect F4 found: what counts as the axis
# ---------------------------------------------------------------------------

def test_a_call_names_what_it_walks_over_not_what_it_calls() -> None:
    """`enumerate(rows)` is a walk over `rows`. It is not a walk over `enumerate`."""
    print(NL + "- the axis of `for x in f(y)` is y, not f -")
    src = ("def f(rows):\n    for i, a in enumerate(rows):\n"
           "        for j, b in enumerate(rows):\n            pass\n")
    check("declaring the real axis finds it", len(problems(src, "rows")) == 1,
          str(problems(src, "rows")))
    check("declaring the builtin finds nothing", problems(src, "enumerate") == [],
          str(problems(src, "enumerate")))


def test_two_different_collections_are_two_different_axes() -> None:
    """The shape that produced `a loop over 'enumerate' inside a loop over 'enumerate'`."""
    print(NL + "- enumerate(rows) and enumerate(cols) are not the same axis -")
    src = ("def f(rows, cols):\n    for i, a in enumerate(rows):\n"
           "        for j, b in enumerate(cols):\n            pass\n")
    check("declaring both axes still finds nothing -- these are two collections",
          problems(src, "rows", "cols") == [], str(problems(src, "rows", "cols")))
    check("and declaring the builtin finds nothing",
          problems(src, "enumerate") == [], str(problems(src, "enumerate")))


def test_a_method_call_names_its_receiver() -> None:
    print(NL + "- the axis of `for k, v in d.items()` is d -")
    src = ("def f(d):\n    for k, v in d.items():\n"
           "        for k2, v2 in d.items():\n            pass\n")
    check("the receiver is the axis", len(problems(src, "d")) == 1, str(problems(src, "d")))
    check("the method name is not", problems(src, "items") == [],
          str(problems(src, "items")))


def test_an_attribute_walk_still_names_the_attribute() -> None:
    """`for r in self.rows` is a walk over `rows`; that behaviour is unchanged."""
    print(NL + "- self.rows is still the axis `rows` -")
    src = ("def f(self):\n    for a in self.rows:\n"
           "        for b in self.rows:\n            pass\n")
    check("an attribute walk names the attribute", len(problems(src, "rows")) == 1,
          str(problems(src, "rows")))


def test_sorted_of_the_axis_is_still_a_walk_over_the_axis() -> None:
    print(NL + "- `for x in sorted(rows)` walks rows -")
    src = ("def f(rows):\n    for a in sorted(rows):\n"
           "        for b in rows:\n            pass\n")
    check("the outer walk resolves through sorted()", len(problems(src, "rows")) >= 1,
          str(problems(src, "rows")))


def test_the_shapes_it_cannot_see_are_pinned_as_gaps() -> None:
    """The capability boundary, written down so nobody claims more than it does.

    F5's probe put the detector in front of a 30B coding model. The two generations the
    dynamic canary condemned as quadratic -- time growing 15x and 17x faster than the
    input -- had **zero** static findings, and the five findings it did produce were on
    generations the canary cleared.

    Asked directly, the detector recognises exactly one of the six ordinary ways to write
    a pairwise scan or an accidental quadratic in Python. These assertions pin the
    *current* behaviour: if a later version closes one of these gaps the test goes red,
    and closing it is then a deliberate act with a line to update rather than a silent
    change to what the mechanism claims.
    """
    print(NL + "- the shapes it does NOT see, pinned -")
    seen = ("def f(records):\n    for a in records:\n        for b in records:\n"
            "            pass\n")
    check("the literal double walk IS seen", len(problems(seen, "records")) == 1)

    gaps = {
        "index pair scan, range(len(records))":
            "def f(records):\n    for i in range(len(records)):\n"
            "        for j in range(i + 1, len(records)):\n            pass\n",
        "enumerate outer, index inner":
            "def f(records):\n    for i, a in enumerate(records):\n"
            "        for j in range(i + 1, len(records)):\n            pass\n",
        "enumerate outer, slice inner":
            "def f(records):\n    for i, a in enumerate(records):\n"
            "        for j, b in enumerate(records[i + 1:], i + 1):\n            pass\n",
        "membership against a list that grows":
            "def f(records):\n    seen = []\n    for r in records:\n"
            "        if r['id'] not in seen:\n            seen.append(r['id'])\n",
        "string or list accumulation in a loop":
            "def f(records):\n    out = ''\n    for r in records:\n"
            "        out += str(r)\n    return out\n",
    }
    for label, src in gaps.items():
        check("NOT seen (a known gap): " + label,
              problems(src, "records") == [], str(problems(src, "records")))


def test_an_unparseable_file_produces_nothing() -> None:
    print(NL + "- a file this interpreter cannot read is not a file full of faults -")
    check("no findings, no exception", problems("def f(:\n", "rows") == [])


def main() -> int:
    for fn in (test_without_a_declaration_it_says_nothing,
               test_a_nested_walk_over_the_declared_axis,
               test_a_membership_test_inside_a_loop_over_the_axis,
               test_materialising_the_whole_axis,
               test_two_separate_loops_are_not_quadratic,
               test_a_call_names_what_it_walks_over_not_what_it_calls,
               test_two_different_collections_are_two_different_axes,
               test_a_method_call_names_its_receiver,
               test_an_attribute_walk_still_names_the_attribute,
               test_sorted_of_the_axis_is_still_a_walk_over_the_axis,
               test_the_shapes_it_cannot_see_are_pinned_as_gaps,
               test_an_unparseable_file_produces_nothing):
        fn()
    print(NL + "scale, static half: " + str(PASSED) + " passed, " + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
