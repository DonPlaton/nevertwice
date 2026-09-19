#!/usr/bin/env python3
"""R1's three clauses, as the owner confirmed them on 2026-09-19, are what the tool applies.

A rate at this stand's n is not an instrument - two draws of the SAME commit read a stale rate of
0.050 and 0.0667 - so R1 re-expresses the part-3 gates on case identity. It lowers nothing: the
requirement is still zero loss. What changes is the evidence admitted for it.

The three clauses, each pinned below:

1. a loss is charged only when the SAME case fails in both draws; a one-draw failure is named and
   not counted;
2. credit is symmetric - a fix counts only when the same case is fixed in both draws, because
   charging by identity while crediting by rate is a rule that can only flatter a change;
3. a third draw is required when the union of failing ids is LARGER than the base's. That is the
   confirmed wording, and the tool is held to it: a union that contains new ids but is smaller is a
   mechanism fixing more than it breaks, and gating on that would be a stricter rule than the one on
   the books.

    python tests/_test_r1_judges_by_case.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before anything reads it

import r1_verdict as r1  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


def doc(draw1: list, draw2: list, all_ids: list) -> dict:
    """An artifact in the stand's own shape: two draws over the same case set."""
    def rows(failing):
        return [{"id": i, "current_retired": i in failing} for i in all_ids]
    return {"arms": {"nevertwice": {"rows": rows(draw1)},
                     "nevertwice_run2": {"rows": rows(draw2)}}}


IDS = ["a", "b", "c", "d", "e", "f", "g", "h"]

print("# clause 1: only a case that fails in BOTH draws is charged")
v = r1.verdict(doc(["a", "b"], ["b", "c"], IDS), "over_retraction", None)
check("the shared failure is charged", v["charged"] == ["b"], repr(v["charged"]))
check("the one-draw failures are named, not counted",
      v["named_not_counted"] == ["a", "c"], repr(v["named_not_counted"]))
check("and the gate is not met while a loss is charged", v["ok"] is False)

print("# ... and two draws that share nothing charge nothing")
v = r1.verdict(doc(["a", "b", "c"], ["d"], IDS), "over_retraction", None)
check("nothing is charged", v["charged"] == [], repr(v["charged"]))
check("the gate is met with no base to compare against", v["ok"] is True)
check("but all four are named", len(v["named_not_counted"]) == 4, repr(v["named_not_counted"]))

print("# clause 2: a fix counts only when both draws fixed the same case")
base = doc(["a", "b", "c"], ["a", "b", "d"], IDS)     # base shares a, b
now = doc(["b"], ["b"], IDS)
v = r1.verdict(now, "over_retraction", base)
check("a case failing in both base draws and neither now is credited",
      v["credited_fixes"] == ["a"], repr(v.get("credited_fixes")))
check("a case still failing in both is not credited", "b" not in v["credited_fixes"])

print("# ... and a case fixed in ONE draw only is named rather than credited")
half = doc(["a", "b"], ["b"], IDS)                     # 'a' fixed in draw 2 only
v = r1.verdict(half, "over_retraction", base)
check("the half-fix is not credited", "a" not in (v.get("credited_fixes") or []),
      repr(v.get("credited_fixes")))
check("it is named instead", "a" in (v.get("fixes_named_not_counted") or []),
      repr(v.get("fixes_named_not_counted")))

print("# clause 3: a third draw is required when the union is LARGER than the base's")
small_base = doc(["a"], ["b"], IDS)                     # base union {a, b}
grown = doc(["c", "d"], ["e"], IDS)                     # union {c, d, e}
v = r1.verdict(grown, "over_retraction", small_base)
check("the larger union demands a third draw", v["third_draw_required"] is True,
      f"union {v['union_size']} vs base {v.get('base_union_size')}")
check("and the gate is not judged until it is in", v["ok"] is False)

print("# ... but a union of new ids that is SMALLER does not, which is the confirmed wording")
big_base = doc(["a", "b", "c", "d"], ["e", "f"], IDS)   # base union of six
smaller_new = doc(["g"], ["h"], IDS)                    # two ids, both new
v = r1.verdict(smaller_new, "over_retraction", big_base)
check("every failing id is new to this arm",
      sorted(v.get("union_new_ids") or []) == ["g", "h"], repr(v.get("union_new_ids")))
check("no third draw is demanded, because the union shrank",
      v["third_draw_required"] is False,
      f"union {v['union_size']} vs base {v.get('base_union_size')} - gating on new ids rather "
      f"than on size would be stricter than the rule the owner confirmed")

print("# ... and the same size with entirely different cases DOES, which the size test cannot see")
same_base = doc(["a", "b"], ["c"], IDS)                 # base union {a, b, c}
moved = doc(["d", "e"], ["f"], IDS)                     # union {d, e, f}: same size, disjoint
v = r1.verdict(moved, "over_retraction", same_base)
check("the union is the same size as the base's",
      v["union_size"] == v["base_union_size"], f"{v['union_size']} vs {v.get('base_union_size')}")
check("it shares no case with the base", v["union_disjoint_from_base"] is True)
check("so a third draw is demanded although the rate did not move",
      v["third_draw_required"] is True,
      "the set moved whole while the size stayed put - invisible to the size test alone")

print("# ... and a union that overlaps the base at the same size does not")
overlap = doc(["a", "d"], ["e"], IDS)                   # union {a, d, e}: same size, shares 'a'
v = r1.verdict(overlap, "over_retraction", same_base)
check("no third draw when the union still shares a case with the base",
      v["third_draw_required"] is False,
      f"union {v['union_size']} vs base {v.get('base_union_size')}")

print("# ... and two clean readings against a clean base are not 'disjoint'")
#: The degenerate case the clause's wording does not cover on its own: an empty union is the same
#: size as an empty base union and shares no id with it, so a literal reading would demand a third
#: draw for a result with nothing wrong in it.
clean = doc([], [], IDS)
v = r1.verdict(clean, "over_retraction", doc([], [], IDS))
check("nothing failing anywhere is not a moved set", v["third_draw_required"] is False,
      "an empty union trivially satisfies 'not smaller and disjoint'")
check("and the gate is met", v["ok"] is True)

print("# a mode with only one draw is not judged at all")
one = {"arms": {"nevertwice": {"rows": [{"id": "a", "current_retired": False}]}}}
v = r1.verdict(one, "over_retraction", None)
check("one draw is refused, not treated as agreement", v["ok"] is False and "two draws" in v.get("detail", ""),
      repr(v)[:160])

print()
print(f"R1 judges by case: {P} passed, {F} failed")
sys.exit(1 if F else 0)
