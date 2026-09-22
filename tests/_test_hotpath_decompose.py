#!/usr/bin/env python3
"""`tools/hotpath_decompose.py`: its headline must agree with its own prose.

The tool's numbers belong to the machine that prints them and move between runs, so this suite
never asks how many milliseconds come out. It asks something that has an answer on every
machine: the "engine's own share" is the total minus EVERY step the same output declares not the
engine's. The first version subtracted the interpreter only - `total - rows[0]` - so the headline
still carried `site`, which the paragraph three lines below said belonged to the machine: one
quantity credited to the engine and disowned in the same printout, twelve percentage points on
the line a reader quotes first (the auditing session's run: 67.4% printed, 55.0% by the tool's
own logic). The tool fed the part 2a verdict and an owner line, and no test stood behind it.

Synthetic rows only - no process is timed here, so nothing in this file can flake.

    python tests/_test_hotpath_decompose.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before anything resolves a store
import hotpath_decompose as H  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not condition else ""))
    PASSED += int(condition)
    FAILED += int(not condition)


def rows_from(steps: list[float], flags: list[bool]) -> list[tuple[str, float, float, bool]]:
    """Cumulative rows the way `main()` builds them, from per-step costs and ownership flags."""
    out, cum = [], 0.0
    for i, (step, ours) in enumerate(zip(steps, flags)):
        cum += step
        out.append((f"stage {i}", cum, step, ours))
    return out


def test_the_share_subtracts_every_step_the_output_disowns() -> None:
    print("\n- the engine's share is the total minus every step marked not the engine's -")
    #: The auditing session's run, step by step: interpreter, site, import, work.
    flags = [ours for _label, ours in H.STAGES]
    rows = rows_from([17.82, 6.77, 25.08, 4.98], flags)
    s = H.shares(rows)
    disowned = sum(step for _l, _c, step, ours in rows if not ours)
    check("ours == total - (sum of every step flagged not ours)",
          abs(s["ours"] - (s["total"] - disowned)) < 1e-9, str(s))
    check("so on these rows the share is 55%, not the 67% the first version printed",
          abs(s["ours_share"] - 0.550) < 0.001, f"{s['ours_share']:.3f}")
    old_logic = s["total"] - rows[0][1]           # what `total - rows[0][1]` gave
    check("and it differs from subtracting the first row alone - the defect this replaces",
          abs(s["ours"] - old_logic) > 1.0, f"ours {s['ours']:.2f}, old {old_logic:.2f}")


def test_the_step_the_prose_disowns_is_flagged_in_the_table() -> None:
    print("\n- the table and the prose read one flag -")
    site_rows = [(label, ours) for label, ours in H.STAGES if label.startswith("+ site")]
    check("there is exactly one `site` stage", len(site_rows) == 1, str(site_rows))
    check("and it is flagged not the engine's, which is what the prose says of it",
          site_rows and site_rows[0][1] is False, str(site_rows))
    check("the interpreter is flagged not the engine's",
          H.STAGES[0][1] is False, str(H.STAGES[0]))
    check("and both engine stages are flagged the engine's",
          [ours for _l, ours in H.STAGES[2:]] == [True, True], str(H.STAGES[2:]))


def test_a_new_disowned_step_is_subtracted_without_touching_the_formula() -> None:
    print("\n- a step added tomorrow and marked not ours leaves the engine's share -")
    base = rows_from([10.0, 5.0, 20.0, 5.0], [False, False, True, True])
    more = rows_from([10.0, 5.0, 3.0, 20.0, 5.0], [False, False, False, True, True])
    a, b = H.shares(base), H.shares(more)
    check("the engine's milliseconds do not change when a non-engine step is inserted",
          abs(a["ours"] - b["ours"]) < 1e-9, f"{a['ours']} vs {b['ours']}")
    check("only its share of a larger total does", b["ours_share"] < a["ours_share"],
          f"{a['ours_share']:.3f} -> {b['ours_share']:.3f}")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_share_subtracts_every_step_the_output_disowns,
               test_the_step_the_prose_disowns_is_flagged_in_the_table,
               test_a_new_disowned_step_is_subtracted_without_touching_the_formula):
        fn()
    print(f"\nhotpath decompose: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
