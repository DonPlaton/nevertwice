#!/usr/bin/env python3
"""Positive and negative controls for the I4 labeller.

The precision census came back **0 of 24**. A result like that has two explanations - the
checker's findings really are all false, or the labeller says "false" to everything - and only
one of them is interesting. So the labeller is held to controls: cases constructed to be
unambiguously TRUE, which it must call true, and cases constructed to be unambiguously FALSE,
which it must call false.

Without these the negative result would be unfalsifiable, which is the failure mode
`research/PREREGISTRATION.md` exists to prevent.

Five defects were found in the labeller while it ran, all by reading its own output, and each
one has a regression here:

* an Attribute call was given an implicit `self`, so `api.f(x)` looked over-supplied;
* module aliases were read from top-level statements only, so `import numpy as np` inside a
  try/except was missed and `np.where(...)` looked like a call into this project;
* a class signature (`class C() :: members`) was parsed as an empty parameter list;
* a reference inside the file that DEFINES the symbol was called a lexical collision;
* a removal was judged against the owner's definitions rather than the site's imports, so a
  symbol that moved and was imported back looked deleted.

Run:  python tests/_test_blast_radius_labeller.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research"))
import blast_radius_precision as P  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ---------------------------------------------------------------------------
# positive controls - the labeller must be able to say TRUE
# ---------------------------------------------------------------------------


def test_arity_reports_a_genuine_break() -> None:
    print("\n- positive controls: arity -")
    check("a required parameter appearing breaks an existing call",
          P.arity_verdict("f(1)\n", 1, "f", "f(a, b)") == "true")
    check("too many positionals breaks a call",
          P.arity_verdict("f(1, 2, 3)\n", 1, "f", "f(a, b)") == "true")
    check("a keyword the new signature does not accept breaks a call",
          P.arity_verdict("f(1, mode='x')\n", 1, "f", "f(a)") == "true")
    check("a method losing a parameter breaks its call site",
          P.arity_verdict("obj.m(1, 2)\n", 1, "m", "m(self, a)", "C.m") == "true")


def test_resolvability_reports_a_genuine_break() -> None:
    print("\n- positive controls: resolvability -")
    site = "from owner import gone\n\ngone()\n"
    check("a symbol imported only from the owner, and dropped there, breaks",
          P.resolvability_verdict(site, "gone", "def other(): pass\n", "owner") == "true")


# ---------------------------------------------------------------------------
# negative controls - the labeller must say FALSE for the right reason
# ---------------------------------------------------------------------------


def test_arity_accepts_what_still_fits() -> None:
    print("\n- negative controls: arity -")
    check("a defaulted parameter appearing breaks nothing",
          P.arity_verdict("f(1)\n", 1, "f", "f(a, b=2)") == "false")
    check("an annotation appearing breaks nothing",
          P.arity_verdict("f(1)\n", 1, "f", "f(a: int) -> None") == "false")
    check("*args absorbs extra positionals",
          P.arity_verdict("f(1, 2, 3)\n", 1, "f", "f(a, *rest)") == "false")
    check("**kwargs absorbs unknown keywords",
          P.arity_verdict("f(1, mode='x')\n", 1, "f", "f(a, **kw)") == "false")
    check("a keyword-only parameter with a default breaks nothing",
          P.arity_verdict("f(1)\n", 1, "f", "f(a, *, mode='x')") == "false")
    check("a module-qualified call gets NO implicit receiver",
          P.arity_verdict("api.f(1)\n", 1, "f", "f(a)") == "false")


def test_the_labeller_abstains_rather_than_guessing() -> None:
    print("\n- abstention: a truncated or non-function signature -")
    check("a truncated signature is refused", P._parameter_text("f(a, b, c…)") is None)
    check("a class signature is refused",
          P._parameter_text("class C() :: get, value") is None)
    check("a decorator prefix is stripped rather than confusing the parse",
          P._parameter_text("@cache f(a, b)") == "a, b")
    check("a return annotation is stripped",
          P._parameter_text("f(a: int = 1) -> dict[str, int]") == "a: int = 1")
    check("*args is seen through the parse",
          P.arity_verdict("f(1, 2)\n", 1, "f", "f(*a)") == "false")
    check("a starred call argument is refused rather than guessed",
          P.arity_verdict("f(*args)\n", 1, "f", "f(a, b)") is None)


def test_resolution_finds_the_collisions() -> None:
    print("\n- negative controls: resolution -")
    site = "import numpy as np\n\nx = np.where(c, 1, 0)\n"
    check("a call through a module alias that is not the owner is a collision",
          P.resolution_verdict(site, "where", "lenses", 3) == "false")
    guarded = "try:\n    import numpy as np\nexcept ImportError:\n    np = None\nx = np.where(c, 1, 0)\n"
    check("the alias is found even when the import sits inside a try",
          P.resolution_verdict(guarded, "where", "lenses", 5) == "false")
    own = "def where(pred):\n    return pred\n\nwhere(1)\n"
    check("a module that defines its own symbol of that name is a collision",
          P.resolution_verdict(own, "where", "lenses", 4) == "false")
    elsewhere = "from other import where\n\nwhere(1)\n"
    check("importing the name from another module is a collision",
          P.resolution_verdict(elsewhere, "where", "lenses", 3) == "false")
    owner = "from lenses import where\n\nwhere(1)\n"
    check("importing it from the owner is NOT a collision",
          P.resolution_verdict(owner, "where", "lenses", 3) is None)
    check("in the owner's own file, defining the name is not a collision",
          P.resolution_verdict(own, "where", "lenses", 4, same_file=True) is None)


def test_resolvability_sees_a_move() -> None:
    print("\n- negative controls: resolvability -")
    moved = "from receipt import est_tokens\n\nest_tokens('x')\n"
    check("a symbol imported back from where it moved still resolves",
          P.resolvability_verdict(moved, "est_tokens", "def other(): pass\n", "stats") == "false")
    own = "def est_tokens(s):\n    return 1\n"
    check("a site with its own definition still resolves",
          P.resolvability_verdict(own, "est_tokens", "", "stats") == "false")
    attribute = "import stats as _st\n\n_st.est_tokens('x')\n"
    check("a module-attribute reference is refused rather than guessed",
          P.resolvability_verdict(attribute, "est_tokens", "", "stats") is None)


def test_the_census_actually_used_these_rules() -> None:
    """The controls only matter if the artifact was produced by the code they test."""
    print("\n- the census and the controls are the same labeller -")
    import json
    artifact = ROOT / "research" / "blast_radius_precision.json"
    check("the census artifact is committed", artifact.is_file())
    if not artifact.is_file():
        return
    data = json.loads(artifact.read_text(encoding="utf-8"))
    check("it names the decision document it is judged against",
          data["thresholds"] == "research/BLAST_RADIUS_DECISION.md")
    sites = [s for f in data["checker"]["findings"] for s in f["sites"]]
    check("every site carries a label", all(s["label"] in ("true", "false") for s in sites),
          str({s["label"] for s in sites}))
    check("every site names the rule or the hand that decided it",
          all(s["source"] in ("mechanical", "manual") for s in sites))
    mechanical = [s for s in sites if s["source"] == "mechanical"]
    check("most sites were decided mechanically, not by hand",
          len(mechanical) > len(sites) / 2, f"{len(mechanical)}/{len(sites)}")
    check("every hand label carries a reason",
          all(s["why"] for s in sites if s["source"] == "manual"))


def main() -> int:
    for fn in (test_arity_reports_a_genuine_break,
               test_resolvability_reports_a_genuine_break,
               test_arity_accepts_what_still_fits,
               test_the_labeller_abstains_rather_than_guessing,
               test_resolution_finds_the_collisions,
               test_resolvability_sees_a_move,
               test_the_census_actually_used_these_rules):
        fn()
    print(f"\nblast-radius labeller: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
