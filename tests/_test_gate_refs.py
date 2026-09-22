#!/usr/bin/env python3
"""`tools/check_gate_refs.py`: a gate that names a claim is checkable; one that quotes is not.

The defect this answers was measured twice on 2026-09-22. Eight of the ten lines of part 3
compare against figures the register has withdrawn, and the frozen "taken and don't touch"
block disagrees with the register in four numbers of ten. Neither was caught by a machine,
because no connection between a gate and its evidence existed - both were found by reading.

Matching back by value was tried and failed: 0.8 collides with `longmem.hybrid.recall_at_5`,
0.05 with `frontier.judge_disagreement`. So the reference has to be written, and this pins what
the checker must catch when it is: a renamed claim, a withdrawn one, and a number that has
drifted from the register. It also pins the quiet case - a document that names nothing is
reported, because "no references" is the state the tool exists to end, not a pass.

    python tests/_test_gate_refs.py
"""
import _env_guard  # noqa: F401
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import check_gate_refs as g  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


CLAIMS = {
    "a.live":      {"id": "a.live", "value": 0.9833},
    "a.pending":   {"id": "a.pending", "value": 411.0, "stale": "reason", "pending_remeasure": True},
    "a.withdrawn": {"id": "a.withdrawn", "value": 0.5, "stale": "reason"},
    "a.declared":  {"id": "a.declared", "value": 0.80, "declaration": "ledger J2: written before the run"},
}


def run(body: str):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "order.md"
        p.write_text(body, encoding="utf-8")
        return g.check_file(p, CLAIMS)


print("\n- the three shapes a reference can be broken in -")
n, probs = run("Gate: at or below [[claim:a.nosuch]]\n")
check("a claim that is not in the register is named", n == 1 and len(probs) == 1
      and "no claim" in probs[0], str(probs))

n, probs = run("Gate: at or below [[claim:a.pending]]\n")
check("a pending claim is refused, with the reason a reader needs",
      len(probs) == 1 and "pending re-measure" in probs[0] and "campaign" in probs[0], str(probs))

n, probs = run("Gate: at or below [[claim:a.withdrawn]]\n")
check("a withdrawn claim is refused", len(probs) == 1 and "withdrawn" in probs[0], str(probs))

n, probs = run("Frozen: current [[claim:a.live = 1.000]]\n")
check("a number that drifted from the register is caught, printing both",
      len(probs) == 1 and "1.000" in probs[0] and "0.9833" in probs[0], str(probs))

print("\n- and what must NOT be reported -")
n, probs = run("Frozen: current [[claim:a.live = 0.9833]]\n")
check("a reference whose number matches passes", n == 1 and not probs, str(probs))

n, probs = run("Gate: at or below [[claim:a.live]]\n")
check("a reference with no number passes - naming the claim is the point",
      n == 1 and not probs, str(probs))

n, probs = run("Gate: as-of both days [[claim:a.declared = 0.80]]\n")
check("a DECLARED value is not compared: a decision has no measurement to drift from",
      n == 1 and not probs, str(probs))

n, probs = run("Gate: chars/query at or below 400, that is under Mem0's 411.\n")
check("a gate that only QUOTES a number yields no reference at all - which is the defect",
      n == 0 and not probs)

print("\n- the register the tool reads is the project's own -")
live = [cid for cid, c in g.load_claims().items() if g.state(c) == "live"]
check("the real register loads and has live claims to reference", len(live) > 0, str(len(live)))
check("state() separates the three cases on real data",
      {g.state(c) for c in g.load_claims().values()} <= {"live", "withdrawn", "pending re-measure"})

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
