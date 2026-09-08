"""`research/asof_bench.py`: the scoring behind the as-of stand, without an extractor.

A case is correct only when the old day returns the old fact without the new one and the new
day returns the new fact; the dateless floor answers both days with one ranking; the pooled
score keeps per-run rates; the Mem0 blocker is recorded, never a zero.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import asof_bench as ab  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


case = {"id": "val-http-timeout", "shape": "value_replaced", "domain": "the API client",
        "sessions": [["Settled it: the HTTP client timeout is 30 seconds.", "Tests stayed green."],
                     ["Came back to it. The HTTP client timeout is 5 seconds.", "Pairing session."]],
        "query": "what is the HTTP client timeout", "current": ["5 second", "5s"], "superseded": ["30 second", "30s"]}

print("\n- one case, two days -")
r = ab._row(case, ["http client timeout: the timeout is 30 seconds"], ["timeout: the HTTP client timeout is 5 seconds"])
check("old day right, new day right, both right", r["old_day_correct"] and r["new_day_correct"] and r["both_correct"])
r = ab._row(case, ["the timeout is 5 seconds now (was 30 seconds)"], ["the HTTP client timeout is 5 seconds"])
check("the old day is wrong when the new fact leaks into it", not r["old_day_correct"] and r["new_day_correct"] and not r["both_correct"])
r = ab._row(case, [], ["the timeout is 5 seconds"])
check("nothing on the old day is wrong on the old day", not r["old_day_correct"])
r = ab._row(case, ["the timeout is 30 seconds"], ["nothing relevant"])
check("the new day needs the replacement", r["old_day_correct"] and not r["new_day_correct"])

print("\n- the dateless floor -")
items = ab._naive_items(case, 5)
check("the floor ranks the case's own sentences by overlap with the query, timeout sentences first",
      len(items) == 4 and all("timeout" in s for s in items[:2]), str(items[:2]))
res = ab.run_naive([case], 5)
row = res["rows"][0]
check("with both facts in the same ranking the old day cannot be right", not row["old_day_correct"] and row["new_day_correct"])
check("the floor's score is over supersession cases", res["n_cases"] == 1 and res["both_correct_rate"] == 0.0)

print("\n- the score -")
rows = [{"shape": "value_replaced", "both_correct": True, "old_day_correct": True, "new_day_correct": True},
        {"shape": "value_replaced", "both_correct": False, "old_day_correct": True, "new_day_correct": False},
        {"shape": "control", "both_correct": False, "old_day_correct": False, "new_day_correct": False}]
sc = ab.score(rows)
check("controls are not counted", sc["n_cases"] == 2 and sc["both_correct_rate"] == 0.5 and sc["old_day_rate"] == 1.0)
check("a Wilson interval around the both-correct rate", sc["both_correct_ci"][0] < 0.5 < sc["both_correct_ci"][1])
check("errors counted", ab.score(rows + [{"shape": "value_replaced", "both_correct": False, "old_day_correct": False,
                                           "new_day_correct": False, "error": "x"}])["errors"] == 1)

print("\n- the days are fixed and ordered -")
check("first < between < second < after", ab.DAY_FIRST < ab.DAY_BETWEEN < ab.DAY_SECOND < ab.DAY_AFTER)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
