#!/usr/bin/env python3
"""The head-to-head stand writes what it already computed, per question.

Part 3.5's numbers are a comparison of arms that answer the *same* questions, and the artifact was
recording only the totals. Unpaired, this design resolves 4.4 points; the difference that started
the whole line - "behind Mem0 at R@5" - is 0.4 points, eight questions of 1,977, inside an interval
three points wide. Paired, a McNemar over the same data resolves 2.0-4.0 points depending on how
often the arms disagree at all. The rows cost nothing to produce: `score()` already decides, per
question, whether each arm had the gold session in its top k. They were simply thrown away.

The gate this pins is the one that matters for a measurement harness: **the aggregates must not
move**. A row dump that quietly changed a published number would be worse than no rows at all, so
the checks below recompute every headline figure FROM the rows and require them to agree with the
aggregate the function returns, on cases whose answers are known by hand.

    python tests/_test_per_question_rows.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "research"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

import head_to_head as H  # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


#: Four questions with answers worked out by hand. s1..s4 are in the pool; sX is not, so the
#: fourth question is outside the canonical subset and must not be scored at all.
POOL = ["s1", "s2", "s3", "s4"]
DATA = [
    {"question_id": "q1", "answer_session_ids": ["s1"], "category": 1},   # gold at rank 1
    {"question_id": "q2", "answer_session_ids": ["s2"], "category": 2},   # gold at rank 3
    {"question_id": "q3", "answer_session_ids": ["s3"], "category": 1},   # never retrieved
    {"question_id": "q4", "answer_session_ids": ["sX"], "category": 3},   # outside the pool
]
RANKED = {"q1": ["s1", "s9", "s8"],
          "q2": ["s9", "s8", "s2"],
          "q3": ["s9", "s8", "s7"],
          "q4": ["sX"]}

out = H.score(RANKED, DATA, POOL)
rows = out.get("per_question")

print("# the rows are there, and they describe the right questions")
check("per_question is present", isinstance(rows, list), repr(type(rows)))
check("one row per scored question, and only those",
      rows is not None and [r["q"] for r in rows] == ["q1", "q2", "q3"], str(rows))
check("n agrees with the rows", out.get("n") == len(rows or []), f"n={out.get('n')}")
check("the category travels with the row",
      rows is not None and [r.get("category") for r in rows] == [1, 2, 1], str(rows))

print("# and every headline number can be rebuilt from them")
for k in H.KS:
    from_rows = sum(r[f"h{k}"] for r in rows) / len(rows)
    check(f"recall@{k} = {out[f'recall@{k}']} is the mean of the rows",
          abs(from_rows - out[f"recall@{k}"]) < 0.0006, f"rows give {from_rows:.4f}")
mrr_rows = sum(r["rr"] for r in rows) / len(rows)
check(f"mrr = {out['mrr']} is the mean of the rows", abs(mrr_rows - out["mrr"]) < 0.0002,
      f"rows give {mrr_rows:.4f}")

print("# the hand-computed answers, so the rows are checked against truth and not only themselves")
by_q = {r["q"]: r for r in rows}
check("q1 is a hit at every k", all(by_q["q1"][f"h{k}"] == 1 for k in H.KS))
check("q1 has reciprocal rank 1.0", by_q["q1"]["rr"] == 1.0)
check("q2 misses at k=1 and hits from k=3", by_q["q2"]["h1"] == 0 and by_q["q2"]["h3"] == 1)
check("q2 has reciprocal rank 1/3", abs(by_q["q2"]["rr"] - 1 / 3) < 0.0002)
check("q3 misses everywhere and scores 0", all(by_q["q3"][f"h{k}"] == 0 for k in H.KS)
      and by_q["q3"]["rr"] == 0.0)
check("q4 was not scored - its answer is outside the pool", "q4" not in by_q)

print()
print(f"per-question rows: {P} passed, {F} failed")
sys.exit(1 if F else 0)
