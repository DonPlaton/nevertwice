#!/usr/bin/env python3
"""Phase E's judge, and the defect E5 found in it by asking another binder.

`ENDTOEND_E.md` §11. The judge decides whether an agent's returned file still contains a
call that cannot bind. E5 replayed all 53 stale and all 53 maintainer trees through
`inspect.Signature.bind` and found **one** disagreement in each direction:
`tests/urlpatterns_reverse/namespace_urls.py` is a Django URLconf, it legitimately contains
no `def` or `class`, and a guard requiring at least one definition scored it *unusable*.

**The fix is not to delete the guard.** Its purpose is that "delete the call site" cannot
score as a fix, and for a file with no definitions the `was - now` check cannot do that job
— an empty answer would have no failing calls and would score as a pass. So the rule
becomes: **lose no definition, and do not lose every call site to the changed symbol.**

**This fix was made after the reported E4 run and is not in its numbers.** A stand re-run
after seeing its own result is a stand tuned once; `ENDTOEND_E.md` §11 records that the
defect cost 1 task of 53, excluded from both arms.

Run:  python tests/_test_endtoend_judge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import measure_endtoend as E  # noqa: E402

PASSED = 0
FAILED = 0
NL = "\n"

NEW_DEF = "def include(arg, namespace):\n    return arg, namespace\n"
URLCONF_STALE = (
    "from django.conf.urls import include\n"
    "\n"
    "urlpatterns = [\n"
    "    include('a.urls'),\n"
    "    include('b.urls'),\n"
    "]\n"
)
URLCONF_FIXED = (
    "from django.conf.urls import include\n"
    "\n"
    "urlpatterns = [\n"
    "    include('a.urls', 'a'),\n"
    "    include('b.urls', 'b'),\n"
    "]\n"
)
WITH_DEFS_STALE = (
    "def go():\n"
    "    include('a.urls')\n"
    "    include('b.urls')\n"
)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def task(stale: str) -> dict:
    return {"stale": stale, "qualname": "include", "symbol": "include",
            "new_def_source": NEW_DEF}


def test_a_file_with_no_definitions_is_still_gradeable() -> None:
    """The defect. A URLconf has no `def` and is a perfectly ordinary Python module."""
    print(NL + "- a definition-less file is graded, not excluded -")
    t = task(URLCONF_STALE)
    ok, why = E.judge(URLCONF_STALE, t)
    check("the stale URLconf is graded as failing", ok is False, str((ok, why)))
    ok, why = E.judge(URLCONF_FIXED, t)
    check("and the repaired one as passing", ok is True, str((ok, why)))


def test_deleting_the_call_sites_is_not_a_fix() -> None:
    """What the guard was for, and it still has to hold where there are no definitions."""
    print(NL + "- an answer that removed the problem is not an answer -")
    t = task(URLCONF_STALE)
    emptied = "from django.conf.urls import include\n\nurlpatterns = []\n"
    ok, why = E.judge(emptied, t)
    check("removing every call site is unusable, not a pass", ok is None, str((ok, why)))
    ok, why = E.judge("", t)
    check("an empty answer is unusable", ok is None, str((ok, why)))


def test_losing_a_definition_is_still_unusable() -> None:
    print(NL + "- the original guard's job, unchanged -")
    t = task(WITH_DEFS_STALE)
    ok, why = E.judge("x = 1\n", t)
    check("an answer that dropped the function is unusable", ok is None, str((ok, why)))
    ok, why = E.judge("def go():\n    include('a.urls', 'a')\n    include('b.urls', 'b')\n", t)
    check("and a real repair passes", ok is True, str((ok, why)))


def test_an_unparseable_answer_is_unusable() -> None:
    print(NL + "- unusable is never a failure -")
    ok, why = E.judge("def go(:\n", task(WITH_DEFS_STALE))
    check("a syntax error is unusable", ok is None, str((ok, why)))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_a_file_with_no_definitions_is_still_gradeable,
               test_deleting_the_call_sites_is_not_a_fix,
               test_losing_a_definition_is_still_unusable,
               test_an_unparseable_answer_is_unusable):
        fn()
    print(NL + "Phase E judge: " + str(PASSED) + " passed, " + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
