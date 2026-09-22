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
    #: `printed` is the register's own rounding, so a document may legally write 0.983.
    "a.live":      {"id": "a.live", "value": 0.9833, "printed": ["0.983"]},
    "a.units":     {"id": "a.units", "value": 30, "printed": ["30 ms"]},
    "a.round":     {"id": "a.round", "value": 0.0667, "printed": ["0.067"]},
    "a.one":       {"id": "a.one", "value": 1.0, "printed": ["1.000"]},
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

print("\n- the number standing BESIDE the reference, which is how a document gets migrated -")
#: The naive migration appends `[[claim:id]]` to the existing line and leaves the figure. The
#: first version of this tool passed exactly that with zero complaints while the line still read
#: `current 1.000 / 1.000` against a stored 0.9833 (audit 2026-09-22, on a simulated
#: post-campaign register where the liveness check no longer masked it).
n, probs = run("Frozen: current 1.000 / 1.000 [[claim:a.live]]\n")
check("a stale number left beside a reference is caught", len(probs) == 1
      and "1.000" in probs[0] and "0.9833" in probs[0], str(probs))

n, probs = run("Frozen: cold import 29 ms [[claim:a.units]]\n")
check("and caught when the register's printed form carries a unit",
      len(probs) == 1 and "29" in probs[0], str(probs))

print("\n- and the legal spellings it must NOT report -")
n, probs = run("Frozen: current 0.983 [[claim:a.live]]\n")
check("a `printed` rounding is legal - it is the register's own form", not probs, str(probs))
n, probs = run("Frozen: stale 0.067 [[claim:a.round]]\n")
check("0.067 beside a stored 0.0667 is legal for the same reason", not probs, str(probs))
n, probs = run("Frozen: cold import 30 ms [[claim:a.units]]\n")
check("the bare number inside a united printed form is legal", not probs, str(probs))
n, probs = run("Frozen: current 1.0 [[claim:a.live = 0.9833]]\n")
check("a number INSIDE the brackets is not also reported as an adjacent one",
      len(probs) == 1 and "1.0" in probs[0], str(probs))
n, probs = run("Two refs: 0.9833 [[claim:a.live]] and 30 [[claim:a.units]]\n")
check("a line with two references reports no adjacent number - which figure is whose is a guess",
      not probs, str(probs))
n, probs = run("Gate: as-of 0.80 [[claim:a.declared]]\n")
check("a declared value has no measurement for an adjacent number to drift from", not probs,
      str(probs))

print("\n- the noise that would switch this check off -")
#: Reporting every unmatched number on a one-reference line gave 8 messages on a corpus of
#: realistic gate lines, of which 5 were false: a sample size, a section number, and a date
#: producing three by itself (audit 2026-09-22). A check that noises stops being read - which
#: is not a hypothesis about people, it is what happened to six gates of part 3.
n, probs = run("Gate: current at or below 0.9833 on n=120 sessions [[claim:a.live]]\n")
check("a sample size beside a correct figure is not reported", not probs, str(probs))
n, probs = run("Gate: cold import at or below 30 ms, see section 4.2 [[claim:a.units]]\n")
check("a section number is not reported", not probs, str(probs))
n, probs = run("Gate: measured 2026-09-18, holds at 0.9833 [[claim:a.live]]\n")
check("a date is not reported - it would be three messages on its own", not probs, str(probs))

print("\n- and the paired line, which is the frozen block's own shape -")
#: Half the block's pairs repeat the figure (`0.000 / 0.000`), so per-line deduplication - right
#: for one reference - collapsed two numbers to one, the count stopped matching two references,
#: and the rule went silent on exactly the case it exists for.
n, probs = run("Frozen: current 1.000 / 1.000 [[claim:a.live]] / [[claim:a.one]]\n")
check("two numbers against two references are paired in reading order",
      len(probs) == 1 and "1.000" in probs[0] and "0.9833" in probs[0], str(probs))
n, probs = run("Frozen: current 0.983 / 1.0 [[claim:a.live]] / [[claim:a.one]]\n")
check("and a correct pair is silent", not probs, str(probs))
n, probs = run("Frozen: 0.983 and 30 and 0.067 [[claim:a.live]] / [[claim:a.units]]\n")
check("three numbers against two references stay silent - the pairing would be a guess",
      not probs, str(probs))

print("\n- a stray number must not shift the pairing -")
#: Counting alone is not enough, because the counts can match by accident. "Frozen 2026:
#: current 1.000 [[a]] / [[b]]" has two figures and two references, so pairing in reading order
#: put the YEAR against the first claim - a message invented out of nothing - and paired the
#: real defect, 1.000 against a stored 0.9833, with the second claim, where it matched and
#: passed. The true defect was swallowed and a false one printed in its place (audit
#: 2026-09-22). Only the last contiguous run of figures pairs now.
n, probs = run("Frozen 2026: current 1.000 [[claim:a.live]] / [[claim:a.one]]\n")
check("a year before the figures does not produce a message about the year", not probs,
      str(probs))
n, probs = run("Frozen: current 1.000 / 1.000 [[claim:a.live]] / [[claim:a.one]]\n")
check("and the block's own pair is still caught", len(probs) == 1 and "1.000" in probs[0],
      str(probs))
n, probs = run("Frozen: 0.017 / 0.067 [[claim:a.live]] / [[claim:a.one]]\n")
check("both halves of a wrong pair are reported", len(probs) == 2, str(probs))

print("\n- a stray number AFTER the references breaks it the same way -")
#: The first fix took the last run of the whole line, which closed the case where a stray number
#: stands BEFORE the figures and opened the identical hole after it. A trailing interval paired
#: the CI bounds against the two claims - two invented messages - and swallowed the real defect;
#: a trailing date did the same (audit 2026-09-22). Neither form is contrived: the register
#: itself stores `ci: {low, high}`. The run NEAREST the references pairs now.
n, probs = run("Frozen: current 1.000 / 1.000 [[claim:a.live]] / [[claim:a.one]] "
               "(95% CI 0.9412, 0.9954)\n")
check("a trailing interval does not pair its bounds against the claims",
      len(probs) == 1 and "1.000" in probs[0], str(probs))
n, probs = run("Frozen: 0.017 / 0.067 [[claim:a.live]] / [[claim:a.one]], measured 2026, 09\n")
check("a trailing date does not pair either",
      len(probs) == 2 and all("2026" not in x for x in probs), str(probs))
n, probs = run("Frozen: [[claim:a.live]] / [[claim:a.one]] read 1.000 / 1.000\n")
check("and a line that puts its figures AFTER the references still pairs",
      len(probs) == 1 and "1.000" in probs[0], str(probs))

print("\n- the two misses, named so they are chosen rather than discovered -")
#: Both are silences, not false alarms, and that is the direction chosen deliberately: a check
#: that invents a message gets switched off, a check that misses one is still worth running.
n, probs = run("Frozen: current 0.9833, was 1.000 [[claim:a.live]]\n")
check("MISS: a wrong figure hiding behind a right one on a one-reference line", not probs,
      str(probs))
n, probs = run("Frozen 2026: current 1.000 [[claim:a.live]] / [[claim:a.one]]\n")
check("MISS: a wrong figure in a paired line that also carries a stray number", not probs,
      str(probs))
doc = (ROOT / "tools" / "check_gate_refs.py").read_text(encoding="utf-8")
check("and both misses are written in the docstring, not left to be discovered",
      "hiding behind a correct one" in doc and "stray" in doc)

print("\n- a document that names nothing must FAIL, not merely be mentioned -")
#: The first version printed "names no claim" and returned 0. CI reads the exit code, by which
#: the state this tool exists to end passed (audit 2026-09-22).
with tempfile.TemporaryDirectory() as tmp:
    q = Path(tmp) / "gates.md"
    q.write_text("Gate: chars/query at or below 400, under Mem0's 411.\n", encoding="utf-8")
    rc = g.main([str(q)])
    check("a file with gates and no references exits non-zero", rc == 1, f"exit {rc}")

print("\n- the limit is stated, because a tool read as catching more than it does is worse -")
doc = (ROOT / "tools" / "check_gate_refs.py").read_text(encoding="utf-8")
check("the docstring says a WRONG claim written correctly is not caught",
      "does not close a claim chosen by family" in doc)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
