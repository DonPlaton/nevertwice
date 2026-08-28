"""E5: check the stand before believing it, and check its judge against another binder.

`ENDTOEND_E.md` §7. Five times in five harnesses this project has read *"could not
answer"* as *"answered fine"* -- three in T3, a `# noqa` silencing `ruff` in F3, and a
canary abstaining on half of F5's first probe. This runs before any number from
`measure_endtoend.py` is quoted, and it is published whether or not it finds anything.

Two parts.

**The judge, against an instrument nobody here wrote.** Phase E decides pass/fail with
`binding.call_fails`. This replays every call through **`inspect.Signature.bind`** -- a real
`Signature` rebuilt from the AST, and a `TypeError` from the standard library is not an
opinion. It is checked on the exact inputs E4 used and where the truth is already known:
all 53 **stale** trees must fail and all 53 **maintainer** trees must pass. A judge that
cannot tell those apart cannot tell an agent's answers apart either.

**The stand's own bookkeeping.** Unusable counts and their reasons, both arms' failure
counts, and the arithmetic that turns a base rate into a trial count -- so a base rate of
zero cannot be a harness that produced nothing.

    python research/invariants_lab/audit_endtoend.py
    python research/invariants_lab/audit_endtoend.py --print
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_endtoend as E  # noqa: E402
import verify_mutants as V  # noqa: E402
from corpusio import progress  # noqa: E402

ARTIFACT = Path(__file__).with_name("endtoend_audit_e5.json")


def independent_verdict(source: str, task: dict) -> bool | None:
    """Does every call to the changed symbol bind, per `inspect.Signature.bind`?

    None when the signature cannot be rebuilt -- which is *abstention*, not a pass. The
    count of abstentions is reported, because an instrument that could not answer is not
    an instrument that answered "fine".
    """
    import ast
    try:
        def_tree = ast.parse(task["new_def_source"])
    except (SyntaxError, ValueError, RecursionError):
        return None
    sig = V._signature_of(def_tree, task["qualname"])
    if sig is None:
        return None
    calls = V._calls_to(source, task["symbol"])
    if not calls:
        return None
    decided = False
    for _lineno, n_pos, kwnames, has_star in calls:
        if has_star:
            continue
        decided = True
        if not V._binds(sig, n_pos, tuple(kwnames)):
            return False
    return True if decided else None


def run() -> dict:
    pool = E.build_pool()
    progress(f"auditing the judge over {len(pool)} tasks, both known trees each")

    agree_stale = disagree_stale = abstain_stale = 0
    agree_fixed = disagree_fixed = abstain_fixed = 0
    disagreements: list[dict] = []

    for i, task in enumerate(pool):
        if i % 10 == 0:
            progress(f"  {i}/{len(pool)}")
        for label, source, expected in (("stale", task["stale"], False),
                                        ("fixed", task["fixed"], True)):
            mine, why = E.judge(source, {**task, "stale": task["stale"]})
            theirs = independent_verdict(source, task)
            if theirs is None:
                if label == "stale":
                    abstain_stale += 1
                else:
                    abstain_fixed += 1
                continue
            same = (mine == theirs)
            if label == "stale":
                agree_stale += int(same)
                disagree_stale += int(not same)
            else:
                agree_fixed += int(same)
                disagree_fixed += int(not same)
            if not same:
                disagreements.append({
                    "mid": task["mid"], "tree": label, "symbol": task["symbol"],
                    "call_fails_says": mine, "signature_bind_says": theirs,
                    "why": why,
                })

    # the ground truth the answer key already established
    stale_should_fail = sum(1 for t in pool
                            if E.judge(t["stale"], t)[0] is False)
    fixed_should_pass = sum(1 for t in pool
                            if E.judge(t["fixed"], {**t, "stale": t["stale"]})[0] is True)

    stand = {}
    if E.ARTIFACT.exists():
        data = json.loads(E.ARTIFACT.read_text(encoding="utf-8"))
        b, h = data["benefit"], data["harm"]
        stand = {
            "stage": data["stage"], "model": data["model"],
            "trials": b["trials"], "usable": b["usable"], "unusable": b["unusable"],
            "unusable_reasons": b["unusable_reasons"],
            "off_failures": b["base_rate"]["off_failures"],
            "on_failures": b["on_failures"],
            "fired_on": b["fired_on"],
            "harm_usable": h["usable"],
            "harm_fired_on_correct_code": h["fired_on_correct_code"],
            "collapsed": (b["usable"] == 0
                          or (b["base_rate"]["off_failures"] == 0
                              and b["on_failures"] == 0 and b["fired_on"] == 0)),
        }

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "E5",
        "judge_check": {
            "instrument": "inspect.Signature.bind, rebuilt from the AST by verify_mutants",
            "tasks": len(pool),
            "stale_trees": {"agree": agree_stale, "disagree": disagree_stale,
                            "abstained": abstain_stale},
            "fixed_trees": {"agree": agree_fixed, "disagree": disagree_fixed,
                            "abstained": abstain_fixed},
            "disagreements": disagreements[:20],
            "ground_truth": {
                "stale_trees_the_judge_fails": stale_should_fail,
                "of": len(pool),
                "fixed_trees_the_judge_passes": fixed_should_pass,
            },
            "verdict": ("pass" if not disagreements else "DISAGREEMENT -- one of the two "
                        "instruments is wrong and which is not obvious in advance"),
        },
        "stand_bookkeeping": stand,
    }


def _print(d: dict) -> None:
    j = d["judge_check"]
    print("E5 -- the audit, published whether or not it found anything")
    print()
    print("the judge against " + j["instrument"])
    print(f"  tasks {j['tasks']}")
    for label in ("stale_trees", "fixed_trees"):
        b = j[label]
        print(f"  {label:12s} agree {b['agree']:4d}  disagree {b['disagree']:4d}  "
              f"abstained {b['abstained']:4d}")
    g = j["ground_truth"]
    print(f"  ground truth: the judge fails {g['stale_trees_the_judge_fails']}/{g['of']} "
          f"stale trees and passes {g['fixed_trees_the_judge_passes']}/{g['of']} fixed ones")
    print(f"  verdict: {j['verdict']}")
    for x in j["disagreements"][:8]:
        print(f"     {x['tree']:6s} {x['symbol']:24s} call_fails={x['call_fails_says']} "
              f"bind={x['signature_bind_says']}")
    print()
    s = d["stand_bookkeeping"]
    if not s:
        print("no Phase E artifact to audit yet")
        return
    print(f"the stand's bookkeeping ({s['stage']}, {s['model']})")
    print(f"  trials {s['trials']}, usable {s['usable']}, unusable {s['unusable']}")
    for why, k in sorted(s["unusable_reasons"].items(), key=lambda x: -x[1])[:6]:
        print(f"     {k:3d}  {why}")
    print(f"  off arm failures {s['off_failures']}, on arm failures {s['on_failures']}, "
          f"mechanism fired on {s['fired_on']}")
    print(f"  harm arm usable {s['harm_usable']}, fired on correct code "
          f"{s['harm_fired_on_correct_code']}")
    print("  COLLAPSED -- treat every number as a harness artefact" if s["collapsed"]
          else "  not collapsed: the arms produced failures and the mechanism spoke")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    data = run()
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
